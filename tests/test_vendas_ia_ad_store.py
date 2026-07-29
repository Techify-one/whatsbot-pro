"""Contrato de PERSISTÊNCIA do fluxo de anúncio (CTWA) do plugin ``vendas_ia`` — plano 86.

Diferente de ``storages/plugins/vendas_ia/tests/test_ad_offer.py`` (DB-free, monkeypatch),
estes testes rodam contra o **Postgres de teste** de verdade: o que está sendo verificado
aqui é justamente o SQL — a idempotência do ``ON CONFLICT``, a semântica de "pendente" e
o TTL do cache. Um mock não provaria nada disso.

Roda pela suíte normal (``WHATSBOT_TEST_DB_URL``):

    venv/bin/python -m pytest tests/test_vendas_ia_ad_store.py -q
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "storages" / "plugins" / "vendas_ia"
MIGRATION = PLUGIN_DIR / "migrations" / "003_ad_leads.sql"

pytestmark = pytest.mark.skipif(
    not MIGRATION.is_file(),
    reason="plugin vendas_ia não instalado neste checkout (storages/ é gitignored)")


@pytest.fixture(scope="module")
def ad_store(_engine_ready):
    """Aplica a migration do plugin e devolve o módulo ``ad_store`` importável."""
    from db.engine import get_engine
    from plugins.migrator import _portable_sql, _split_statements

    sql = _portable_sql(MIGRATION.read_text(encoding="utf-8"))
    with get_engine().begin() as conn:
        for stmt in ("DROP TABLE IF EXISTS plugin_vendas_ia_ad_leads",
                     "DROP TABLE IF EXISTS plugin_vendas_ia_ad_cache"):
            conn.execute(text(stmt))
        for stmt in _split_statements(sql):
            if stmt.strip():
                conn.execute(text(stmt))

    plugins_root = str(REPO_ROOT / "storages" / "plugins")
    if plugins_root not in sys.path:
        sys.path.insert(0, plugins_root)
    from vendas_ia import ad_store as mod
    return mod


@pytest.fixture(autouse=True)
def _limpa(ad_store):
    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM plugin_vendas_ia_ad_leads"))
        conn.execute(text("DELETE FROM plugin_vendas_ia_ad_cache"))


# Bloco referral REAL de produção (logs_whatsbot.jsonl linha 89, clique em 27/07/2026).
_REFERRAL = {
    "source_id": "52516040145790",
    "source_type": "ad",
    "source_url": "https://fb.me/4N5IH0yoF",
    "ctwa_clid": "AfjSng_PmH6qD3TcV4GnQcjKosfbWeqyK",
    "headline": "Certificação de Segurança MikroTik com IA",
    "body": "curso oficial MTCSE com Módulo de IA aplicado à segurança de redes",
}


def test_replay_do_payload_real_grava_uma_linha_so(ad_store):
    """Critério de pronto da F2: a Meta REENTREGA o webhook — dois replays, 1 linha."""
    kw = dict(phone="5546999359099", msg_id="wamid.HBgMNTU0Njk5MzU5MDk5",
              channel_id="whatsapp_cloud_bc081279", referral=_REFERRAL, ts=1753600000.0)
    assert ad_store.record_lead(**kw) is True      # 1ª entrega: gravou
    assert ad_store.record_lead(**kw) is False     # reentrega: não duplicou

    lead = ad_store.pending_lead("5546999359099")
    assert lead is not None
    assert lead["source_id"] == "52516040145790"
    assert lead["source_type"] == "ad"
    assert "MTCSE" in lead["body"]


def test_pending_lead_devolve_o_clique_mais_recente(ad_store):
    """Dois cliques antes de a IA responder ⇒ vale o ÚLTIMO (intenção atual)."""
    ad_store.record_lead(phone="551", msg_id="m1", channel_id="c",
                         referral={**_REFERRAL, "source_id": "antigo"}, ts=1000.0)
    ad_store.record_lead(phone="551", msg_id="m2", channel_id="c",
                         referral={**_REFERRAL, "source_id": "novo"}, ts=2000.0)
    assert ad_store.pending_lead("551")["source_id"] == "novo"


def test_mark_consumed_tira_o_lead_de_pendente(ad_store):
    ad_store.record_lead(phone="552", msg_id="m", channel_id="c",
                         referral=_REFERRAL, ts=1.0)
    lead = ad_store.pending_lead("552")
    ad_store.mark_consumed(lead["id"], offercode="O5428A72F", source="codigo")
    assert ad_store.pending_lead("552") is None    # não re-fixa a cada turno


def test_mark_consumed_sem_oferta_tambem_encerra(ad_store):
    """Lead avaliado que não casou nada não pode ser reavaliado a cada mensagem."""
    ad_store.record_lead(phone="553", msg_id="m", channel_id="c",
                         referral=_REFERRAL, ts=1.0)
    ad_store.mark_consumed(ad_store.pending_lead("553")["id"])
    assert ad_store.pending_lead("553") is None


def test_leads_sao_isolados_por_telefone(ad_store):
    ad_store.record_lead(phone="554", msg_id="m", channel_id="c",
                         referral=_REFERRAL, ts=1.0)
    assert ad_store.pending_lead("555") is None


def test_cache_upsert_e_ttl(ad_store):
    ad_store.put_cache("ad1", campaign_id="camp1",
                       campaign_name="[C045] Segurança", codigo="C045")
    hit = ad_store.get_cached("ad1", ttl_seconds=3600)
    assert hit["codigo"] == "C045" and hit["campaign_id"] == "camp1"

    # Upsert sobrescreve (o anunciante renomeou a campanha).
    ad_store.put_cache("ad1", campaign_id="camp1",
                       campaign_name="[C099] Outro", codigo="C099")
    assert ad_store.get_cached("ad1", ttl_seconds=3600)["codigo"] == "C099"

    # TTL vencido ⇒ tratado como miss (a linha antiga continua lá para o sweep).
    ad_store.put_cache("velho", codigo="C001")
    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE plugin_vendas_ia_ad_cache SET fetched_at = 1 "
                          "WHERE source_id = 'velho'"))
    assert ad_store.get_cached("velho", ttl_seconds=60) is None

    # ttl <= 0 = sem expiração por tempo ("0 desliga", igual ao resto do plugin).
    assert ad_store.get_cached("velho", ttl_seconds=0)["codigo"] == "C001"
    assert ad_store.get_cached("inexistente", ttl_seconds=3600) is None


def test_source_ids_needing_resolve(ad_store):
    """Alimenta o resolvedor de fundo: sem cache, ou com cache vencido."""
    ad_store.record_lead(phone="561", msg_id="a", channel_id="c",
                         referral={**_REFERRAL, "source_id": "sem_cache"}, ts=1.0)
    ad_store.record_lead(phone="562", msg_id="b", channel_id="c",
                         referral={**_REFERRAL, "source_id": "com_cache"}, ts=1.0)
    ad_store.put_cache("com_cache", codigo="C001")

    pend = ad_store.source_ids_needing_resolve(ttl_seconds=3600, limit=10)
    assert "sem_cache" in pend
    assert "com_cache" not in pend               # cache fresco não é reconsultado

    # ttl <= 0 = sem expiração por tempo (MESMA convenção de get_cached): só entra quem
    # não tem linha de cache nenhuma.
    sem_ttl = ad_store.source_ids_needing_resolve(ttl_seconds=0, limit=10)
    assert sem_ttl == ["sem_cache"]

    # Cache velho + TTL curto ⇒ volta para a fila do resolvedor.
    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE plugin_vendas_ia_ad_cache SET fetched_at = 1"))
    assert set(ad_store.source_ids_needing_resolve(ttl_seconds=60, limit=10)) == {
        "sem_cache", "com_cache"}


def test_latest_source_id(ad_store):
    assert ad_store.latest_source_id() is None
    ad_store.record_lead(phone="591", msg_id="a", channel_id="c",
                         referral={**_REFERRAL, "source_id": "antigo"}, ts=10.0)
    ad_store.record_lead(phone="592", msg_id="b", channel_id="c",
                         referral={**_REFERRAL, "source_id": "recente"}, ts=99.0)
    assert ad_store.latest_source_id() == "recente"


def test_stats_reflete_o_que_foi_capturado(ad_store):
    ad_store.record_lead(phone="571", msg_id="a", channel_id="c",
                         referral=_REFERRAL, ts=time.time())
    ad_store.record_lead(phone="572", msg_id="b", channel_id="c",
                         referral=_REFERRAL, ts=time.time())
    ad_store.mark_consumed(ad_store.pending_lead("571")["id"],
                           offercode="O5428A72F", source="codigo")
    ad_store.put_cache("ad1", codigo="C045")
    ad_store.put_cache("ad2", codigo=None)

    st = ad_store.stats()
    assert st["leads"] == 2
    assert st["resolvidos"] == 1
    assert st["pendentes"] == 1
    assert st["anuncios_cacheados"] == 2
    assert st["com_codigo"] == 1


def test_record_lead_ignora_entrada_vazia(ad_store):
    assert ad_store.record_lead(phone="", msg_id="m", channel_id="c",
                                referral=_REFERRAL) is False
    assert ad_store.record_lead(phone="580", msg_id="m", channel_id="c",
                                referral={}) is False
    assert ad_store.pending_lead("580") is None
