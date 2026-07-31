"""Testes do plugin ``trackify`` (jornada do cliente + espelho para o CDP).

Divididos em dois grupos, de propósito:

* **puros** — telefone, ``external_id``, derivação de assinatura e tolerância aos
  formatos tortos do CDP. Não tocam banco nem rede: carregam o módulo por
  ``importlib`` e rodam em milissegundos.
* **com app** — fila de saída e rotas, pela fixture ``plugin_app`` (monta o
  plugin de verdade, com migração aplicada).

Os dados torturados aqui não foram inventados: ``next_charge_date`` em
``dd/mm/yyyy`` e ``subscription_canceled_at`` valendo a string ``"system"`` são
valores REAIS lidos da produção durante o planejamento (plano 94).
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "assets" / "plugin_examples" / "trackify"
_PKG = "trackify_src"   # nome PRÓPRIO: não colide com o que o harness monta


def _load(module: str):
    """Carrega ``<módulo>`` do plugin como pacote isolado, SEM mexer no ``sys.path``.

    Inserir ``storages/plugins`` (ou ``assets/plugin_examples``) no ``sys.path``
    parece atalho e é armadilha: essas pastas têm um diretório ``gowa/`` que
    SOMBREIA o pacote ``gowa/`` da raiz do repo, e aí ``server/routes/contacts.py``
    quebra com ``No module named 'gowa.client'`` — derrubando qualquer teste que
    suba o app. Registrar o pacote por spec evita isso; os imports relativos
    (``from . import _config``) continuam funcionando porque o pacote existe de
    verdade em ``sys.modules``.
    """
    if _PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _PKG, _SRC / "__init__.py", submodule_search_locations=[str(_SRC)])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = pkg
        spec.loader.exec_module(pkg)
    return importlib.import_module(f"{_PKG}.{module}")


# ── Puros: telefone ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def phone():
    return _load("phone")


@pytest.mark.parametrize("value,expected", [
    ("5564996162906", "+5564996162906"),          # móvel 13 dígitos: já tem o 9
    ("556496162906", "+5564996162906"),           # móvel 12: restaura o 9
    ("+55 (64) 99616-2906", "+5564996162906"),    # mascarado
    ("5564996162906@s.whatsapp.net", "+5564996162906"),   # JID
    ("12025550123", "+12025550123"),              # fora do Brasil: intacto
])
def test_canonical_e164(phone, value, expected):
    assert phone.canonical_e164(value) == expected


def test_canonical_nao_inventa_nono_digito_em_telefone_fixo(phone):
    """O 9 só entra em CELULAR.

    ``br_phone_variants`` (do core) insere o 9 em qualquer número de 12 dígitos
    começando com 55 — o que num FIXO produziria um número que não existe. Como
    a política é criar o contato quando ele não está no CDP, esse número
    inventado ficaria gravado para sempre (contato lá só tem soft delete).
    """
    assert phone.canonical_e164("556432168000") == "+556432168000"
    # ...mas na BUSCA sobregerar é inofensivo, então a variante continua lá.
    assert "5564932168000" in phone.lookup_candidates("556432168000")


@pytest.mark.parametrize("value", [
    "wsess_f0qD7g7e3TmKcZwGfpoSHRtF_0vwLEy1",   # id de sessão do widget de site
    "120363999@g.us",                            # JID de grupo
    "12345",                                     # curto demais
    "1234567890123456",                          # longo demais (E.164 vai até 15)
    "abc", "", None,
])
def test_o_que_nao_e_telefone_nao_vira_candidato(phone, value):
    """O ``contacts.phone`` do WhatsBot é identificador genérico por canal, não
    telefone: o widget de site grava id de sessão ali. Sem este guard, os dígitos
    soltos do id virariam busca — e, na escrita, contato novo de lixo no CDP."""
    assert phone.looks_like_phone(value) is False
    assert phone.lookup_candidates(value) == []
    assert phone.canonical_e164(value) == ""


def test_candidatos_cobrem_as_quatro_grafias(phone):
    """90% do CDP grava com ``+`` e o WhatsBot sem — por isso as duas formas de
    cada variante entram na consulta indexada."""
    got = phone.lookup_candidates("556496162906")
    assert set(got) == {"556496162906", "+556496162906",
                        "5564996162906", "+5564996162906"}


# ── Puros: jornada ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def journey():
    return _load("journey")


def test_data_solta_do_cdp(journey):
    """O CDP guarda ``next_charge_date`` como STRING ``dd/mm/yyyy``."""
    assert journey._parse_loose_date("25/02/2027") == datetime.date(2027, 2, 25)
    assert journey._parse_loose_date("2027-02-25") == datetime.date(2027, 2, 25)


def test_system_nao_e_data(journey):
    """``subscription_canceled_at`` vem com a string ``"system"`` em linhas reais
    de ``active_subscription``. Não pode virar data nem exceção."""
    assert journey._parse_loose_date("system") is None
    assert journey._parse_loose_date("") is None
    assert journey._parse_loose_date(None) is None


def test_dinheiro_em_pt_br(journey):
    assert journey._money(Decimal("1234.5")) == "R$ 1.234,50"
    assert journey._money(Decimal("97.00")) == "R$ 97,00"
    assert journey._money(None) is None          # events.value é nullable


def test_assinatura_derivada_de_linha_real(journey, monkeypatch):
    """Payload copiado de um evento REAL de produção, com as duas armadilhas."""
    linha = {
        "id": "48c7304f", "event_type": "active_subscription",
        "title": "Assinatura ativa", "value": Decimal("97.00"),
        "occurred_at": datetime.datetime(2026, 4, 29, 13, 30, 16),
        "fields": {
            "status": "Pagamento Autorizado",
            "product_name": "Combo de Redes",
            "offer_name": "Combo de Redes (Multivendor)",
            "payment_method": "Pix", "successful_charges": "1", "failed_charges": "0",
            "next_charge_date": "25/02/2027",
            "subscription_canceled_at": "system",
        },
    }
    monkeypatch.setattr(journey.trackify_db, "run_read", lambda *a, **k: [linha])
    subs = journey.fetch_subscriptions("x", today=datetime.date(2026, 7, 31))

    assert len(subs) == 1
    s = subs[0]
    assert s["product"] == "Combo de Redes"
    assert s["next_charge"] == "2027-02-25"
    assert s["days_left"] == 209
    assert s["next_charge_raw"] == "25/02/2027"   # o cru fica, para exibir se o parse falhar
    assert s["canceled_at"] is None               # "system" NÃO virou data
    assert s["last_value"] == "R$ 97,00"


def test_nenhum_parametro_e_engolido_por_cast():
    """`:param::tipo` NÃO funciona no SQLAlchemy — e falha só em runtime.

    O regex de bind param do SQLAlchemy tem um lookahead negativo: `:cid`
    seguido de `:` é ignorado de propósito (para não colidir com o cast `::` do
    Postgres). O resultado é que `WHERE id = :cid::uuid` chega LITERAL no banco
    e vira `syntax error at or near ":"` — um 500 na cara do vendedor.

    Este teste existe porque os testes de jornada mockam `run_read`: o SQL nunca
    era compilado, então a suíte ficava verde com a consulta quebrada. A forma
    correta é `CAST(:cid AS uuid)`.
    """
    import re
    from sqlalchemy import text

    for mod_name in ("journey", "identity", "dispatcher", "mirror"):
        mod = _load(mod_name)
        for attr in dir(mod):
            value = getattr(mod, attr)
            if not isinstance(value, str) or "SELECT" not in value.upper():
                continue
            engolidos = re.findall(r":(\w+)::", value)
            assert not engolidos, (
                f"{mod_name}.{attr}: parâmetro(s) {engolidos} colados num cast "
                f"`::` — o SQLAlchemy não os substitui. Use CAST(:x AS tipo)."
            )
            # Cinto de segurança: todo `:nome` do SQL tem que virar bind param.
            declarados = set(re.findall(r"(?<![:\w]):(\w+)", value))
            reconhecidos = set(text(value)._bindparams)
            assert declarados == reconhecidos, (
                f"{mod_name}.{attr}: o SQLAlchemy reconheceu {reconhecidos}, "
                f"mas o SQL escreve {declarados}."
            )


def test_sem_dsn_tudo_degrada_para_vazio(journey, monkeypatch):
    """Sem configuração o plugin é no-op logado — nunca levanta, nunca 500."""
    monkeypatch.setattr(journey.trackify_db, "is_configured", lambda: False)
    out = journey.journey_for(phone="5564996162906")
    assert out["found"] is False and out["configured"] is False


# ── Puros: identidade e external_id ──────────────────────────────────────

def test_identidade_canonica_omite_vazios():
    """A ingestão devolve 422 se nenhum identificador sobreviver; mandar string
    vazia não ajuda ninguém."""
    identity = _load("identity")
    out = identity.canonical_identity(phone="556496162906", email="")
    assert out == {"whatsapp": "+5564996162906"}

    out = identity.canonical_identity(phone="556496162906", email="A@B.com ")
    assert out == {"email": "a@b.com", "whatsapp": "+5564996162906"}

    # Telegram: o "phone" é chat_id, não telefone — nunca vira ``whatsapp``.
    out = identity.canonical_identity(phone="123456789", contact_type="telegram")
    assert out == {"telegram_id": "123456789"}


def test_hash_de_etiquetas_e_estavel_e_ordem_nao_importa():
    """``contact.tagged`` é emitido em TODO save, mesmo sem mudança. O
    ``external_id`` endereçado pelo conteúdo colapsa re-saves idênticos em zero
    eventos no CDP — e a ordem das tags não pode gerar id diferente."""
    mirror = _load("mirror")
    a = mirror._tags_hash(["cliente", "vip"])
    b = mirror._tags_hash(["vip", "cliente"])
    assert a == b
    assert a != mirror._tags_hash(["cliente"])


def test_elegibilidade(monkeypatch):
    mirror = _load("mirror")
    monkeypatch.setattr(mirror._config, "setting",
                        lambda k, d=None: "whatsapp" if k == "mirror_contact_types" else d)

    ok, _ = mirror.eligible({"phone": "5564996162906", "contact_type": "whatsapp"})
    assert ok is True

    # Grupo: contato no Trackify só tem soft delete — um JID de grupo viraria
    # linha-lixo permanente no CDP.
    ok, why = mirror.eligible({"phone": "5564996162906", "is_group": 1})
    assert ok is False and "grupo" in why

    ok, why = mirror.eligible({"phone": "wsess_abc123def456", "contact_type": "whatsapp"})
    assert ok is False and "telefone" in why

    ok, why = mirror.eligible({"phone": "5564996162906", "contact_type": "outros"})
    assert ok is False and "outros" in why


# ── Com app: fila de saída ───────────────────────────────────────────────

def _mirror_on(**extra):
    base = {"plugin.trackify.mirror_enabled": True,
            "plugin.trackify.mirror_dry_run": True}
    base.update(extra)
    return base


def test_migracao_cria_a_fila(plugin_app):
    built = plugin_app("trackify")
    from sqlalchemy import text
    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(text("SELECT id, external_id, status, attempts, next_attempt_at "
                          "FROM plugin_trackify_outbox LIMIT 1"))
        conn.execute(text("SELECT phone, exact_value FROM plugin_trackify_identity LIMIT 1"))
    assert built is not None


def test_enfileira_uma_vez_por_fato(plugin_app):
    """Idempotência LOCAL: o mesmo fato enfileirado duas vezes (re-entrega do
    bus, replay, emit duplo) colapsa numa linha só, antes mesmo do dedup do CDP."""
    plugin_app("trackify", settings_overrides=_mirror_on())
    mirror = _load("mirror")
    contact = {"id": 1, "phone": "5564996162906", "name": "Fulano",
               "contact_type": "whatsapp", "email": ""}
    eid = mirror.make_external_id("proto.999.c1753900000")

    assert mirror.enqueue("protocolo_closed", contact=contact, external_id=eid,
                          data={"protocolo_id": 999}, occurred_at=1753900000.0) is True
    mirror.enqueue("protocolo_closed", contact=contact, external_id=eid,
                   data={"protocolo_id": 999}, occurred_at=1753900000.0)

    from sqlalchemy import text
    from db.engine import get_engine
    with get_engine().begin() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM plugin_trackify_outbox "
                              "WHERE external_id = :e"), {"e": eid}).scalar()
    assert n == 1


def test_grupo_nunca_entra_na_fila(plugin_app):
    plugin_app("trackify", settings_overrides=_mirror_on())
    mirror = _load("mirror")
    ok = mirror.enqueue("conversation_created",
                        contact={"id": 2, "phone": "120363999", "is_group": 1},
                        external_id=mirror.make_external_id("conv.2"), data={})
    assert ok is False


def test_envelope_tem_data_iso_com_fuso(plugin_app):
    """O adapter do Trackify faz ``new Date(v)``: número seria lido como
    MILISSEGUNDOS (o ts do WhatsBot é em segundos → tudo em 1970) e ISO sem
    offset seria lido no fuso do servidor de lá."""
    plugin_app("trackify", settings_overrides=_mirror_on())
    dispatcher = _load("dispatcher")
    row = {
        "id": 1, "external_id": "wb.x.conv.1", "kind": "conversation_created",
        "phone": "5564996162906", "occurred_at": 1753900000.0, "attempts": 0,
        "payload": json.dumps({
            "v": 1, "kind": "conversation_created", "title": "Atendimento iniciado",
            "contact": {"name": "Fulano", "phone": "5564996162906",
                        "contact_type": "whatsapp", "email": ""},
            "data": {"conversation_id": 7},
        }),
    }
    body = dispatcher.build_body(row)
    # Data EXATA: prova que segundos foram lidos como segundos. Se o envelope
    # mandasse o epoch cru, o ``new Date(número)`` do adapter leria como
    # milissegundos e carimbaria 1970 em todo evento.
    assert body["occurred_at"] == "2025-07-30T18:26:40+00:00"
    assert body["title"]                       # sem title a timeline lê o event_type
    assert "phone" not in body["contact"]      # telefone só como identificador
    assert body["identity"]["whatsapp"] == "+5564996162906"


def test_health_distingue_nao_configurado(plugin_app):
    """"Não configurado", "inalcançável" e "schema mudou" são coisas diferentes —
    um schema alterado no CDP não pode virar tela vazia sem explicação."""
    built = plugin_app("trackify")
    r = built.client.get("/api/plugins/trackify/health")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["configured"] is False and d["reachable"] is False
    assert "não configurado" in d["message"].lower()


def test_journey_recusa_contato_inexistente(plugin_app):
    built = plugin_app("trackify")
    r = built.client.get("/api/plugins/trackify/journey?contact_id=999999")
    assert r.status_code == 404
    assert r.json()["ok"] is False
