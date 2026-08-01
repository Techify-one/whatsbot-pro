"""Popup "vincular ao protocolo anterior" (plano 49) — backend do vínculo.

Cobre a sugestão (dentro/fora da janela + toggle), o ``relink`` (move ciclos, descarta
o protocolo novo, reabre o anterior, mantém 1 aberto por contato) e as recusas 404/409.
Aponta para o código INSTALADO em ``storages/plugins/protocolos`` (monkeypatch
``REAL_PLUGIN_EXAMPLES``), como ``test_avaliacao_protocolo``.

Fecha uma lacuna de cobertura: ``reopen_protocolo``/``on_inbound`` não tinham teste.

    venv/bin/python -m pytest tests/test_protocolos_popup.py -q
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from db.engine import get_engine

# O plugin ``protocolos`` é um plugin Pro instalado (não versionado): a cópia VIVA mora em
# ``storages/plugins/protocolos``. Apontamos ``build_app`` para lá (não ``assets``).
_STORAGES_PLUGINS = Path(__file__).resolve().parents[1] / "storages" / "plugins"
pytestmark = pytest.mark.skipif(
    not (_STORAGES_PLUGINS / "protocolos" / "plugin.yaml").is_file(),
    reason="plugin protocolos não instalado em storages/plugins "
           "(fonte intencional deste módulo)",
)


@pytest.fixture(autouse=True)
def _load_from_storages(monkeypatch):
    monkeypatch.setattr("tests.support.REAL_PLUGIN_EXAMPLES", _STORAGES_PLUGINS)


def _logic():
    """Módulo logic do plugin JÁ carregado por ``build_app`` (whatsbot_plugins.protocolos)."""
    return importlib.import_module("whatsbot_plugins.protocolos.logic")


def _seed_protocolo(contact_id: int, *, status: str = "fechado",
                    closed_at: float | None = None, opened_at: float | None = None,
                    assignee_name: str = "Ana") -> int:
    """Insere um protocolo direto na tabela do plugin (raw SQL, como test_avaliacao)."""
    ts = time.time()
    with get_engine().begin() as conn:
        pid = conn.execute(text(
            "INSERT INTO plugin_protocolos_protocolos "
            "(contact_id, contact_phone, contact_name, status, assignee_name, fields, "
            " opened_at, closed_at, created_at, updated_at) "
            "VALUES (:cid, '5511', 'Cliente', :st, :an, '{}', :op, :cl, :ts, :ts) "
            "RETURNING id"),
            {"cid": contact_id, "st": status, "an": assignee_name,
             "op": opened_at if opened_at is not None else ts,
             "cl": closed_at, "ts": ts}).scalar()
    return int(pid)


def _seed_ciclo(protocolo_id: int, conversation_id: int, contact_id: int) -> int:
    """Insere um ciclo (atendimento) vinculado ao protocolo. conversation_id é livre
    (sem FK cross-table) — não precisa de conversa real do core."""
    ts = time.time()
    with get_engine().begin() as conn:
        cid = conn.execute(text(
            "INSERT INTO plugin_protocolos_atendimentos "
            "(protocolo_id, conversation_id, contact_id, assignee_name, fields, "
            " started_at, created_at, updated_at) "
            "VALUES (:pid, :conv, :contact, '', '{}', :ts, :ts, :ts) RETURNING id"),
            {"pid": protocolo_id, "conv": conversation_id, "contact": contact_id,
             "ts": ts}).scalar()
    return int(cid)


def _count_open(contact_id: int) -> int:
    with get_engine().connect() as conn:
        return int(conn.execute(text(
            "SELECT COUNT(*) FROM plugin_protocolos_protocolos "
            "WHERE contact_id = :c AND status = 'aberto'"), {"c": contact_id}).scalar())


def test_suggestion_within_and_outside_window(build_app):
    built = build_app(["gowa", "protocolos"])
    c = built.client
    now = time.time()

    # Fechado há 5 min (< janela default 30) → suggest True + previous preenchido.
    cid = 990101
    pid = _seed_protocolo(cid, status="fechado", closed_at=now - 300, assignee_name="Bia")
    r = c.get(f"/api/plugins/protocolos/contacts/{cid}/relink-suggestion")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["suggest"] is True
    assert data["previous"]["id"] == pid
    assert data["previous"]["assignee_name"] == "Bia"
    assert data["seconds_since_close"] >= 0
    assert data["window_minutes"] == 30

    # Fechado há 90 min (> janela) → suggest False.
    cid2 = 990102
    _seed_protocolo(cid2, status="fechado", closed_at=now - 90 * 60)
    assert c.get(f"/api/plugins/protocolos/contacts/{cid2}/relink-suggestion").json()["data"]["suggest"] is False

    # Sem protocolo fechado → suggest False, previous None.
    body = c.get("/api/plugins/protocolos/contacts/990199/relink-suggestion").json()["data"]
    assert body["suggest"] is False and body["previous"] is None


def test_suggestion_respects_toggle(build_app):
    from db.repositories import config_repo
    built = build_app(["gowa", "protocolos"])
    c = built.client
    cid = 990103
    _seed_protocolo(cid, status="fechado", closed_at=time.time() - 60)
    # Chave persistida carrega o prefixo `general_` (via logic._general_key).
    config_repo.set("plugin.protocolos.general_relink_prompt_enabled", False)
    try:
        data = c.get(f"/api/plugins/protocolos/contacts/{cid}/relink-suggestion").json()["data"]
        assert data["suggest"] is False
        assert data["enabled"] is False
    finally:
        config_repo.set("plugin.protocolos.general_relink_prompt_enabled", True)


def test_suggestion_window_configurable(build_app):
    from db.repositories import config_repo
    built = build_app(["gowa", "protocolos"])
    c = built.client
    cid = 990104
    _seed_protocolo(cid, status="fechado", closed_at=time.time() - 40 * 60)  # 40 min atrás
    # Default 30 → fora da janela.
    assert c.get(f"/api/plugins/protocolos/contacts/{cid}/relink-suggestion").json()["data"]["suggest"] is False
    # Janela 60 → agora dentro. Chave persistida carrega o prefixo `general_`.
    config_repo.set("plugin.protocolos.general_relink_window_minutes", 60)
    try:
        data = c.get(f"/api/plugins/protocolos/contacts/{cid}/relink-suggestion").json()["data"]
        assert data["window_minutes"] == 60
        assert data["suggest"] is True
    finally:
        config_repo.set("plugin.protocolos.general_relink_window_minutes", 30)


def test_relink_moves_cycles_discards_new_reopens_previous(build_app):
    built = build_app(["gowa", "protocolos"])
    c = built.client
    plogic = _logic()
    cid = 990201
    now = time.time()
    prev = _seed_protocolo(cid, status="fechado", closed_at=now - 120)
    cur = _seed_protocolo(cid, status="aberto", closed_at=None)
    cyc1 = _seed_ciclo(cur, 771001, cid)
    cyc2 = _seed_ciclo(cur, 771002, cid)

    r = c.post(f"/api/plugins/protocolos/protocolos/{prev}/relink",
               json={"current_open_id": cur})
    assert r.status_code == 200, r.text
    proto = r.json()["data"]["protocolo"]
    assert proto["id"] == prev and proto["status"] == "aberto"

    # O anterior voltou a aberto; o novo sumiu.
    assert plogic.get_protocolo(prev)["status"] == "aberto"
    assert plogic.get_protocolo(cur) is None
    # Ciclos repontados p/ o anterior.
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT protocolo_id FROM plugin_protocolos_atendimentos WHERE id IN (:a, :b)"),
            {"a": cyc1, "b": cyc2}).all()
    assert rows and all(int(row[0]) == prev for row in rows)
    # Exatamente 1 aberto para o contato (índice único respeitado).
    assert plogic._select_open_protocolo(cid)["id"] == prev
    assert _count_open(cid) == 1


def test_relink_without_current_just_reopens(build_app):
    built = build_app(["gowa", "protocolos"])
    c = built.client
    plogic = _logic()
    cid = 990401
    prev = _seed_protocolo(cid, status="fechado", closed_at=time.time() - 60)
    r = c.post(f"/api/plugins/protocolos/protocolos/{prev}/relink", json={})
    assert r.status_code == 200, r.text
    assert plogic.get_protocolo(prev)["status"] == "aberto"
    assert _count_open(cid) == 1


def test_relink_absorbs_current_open_even_when_id_stale(build_app):
    """Corrida do ``auto_link``: o popup calcula a sugestão ANTES de o auto_link abrir o
    protocolo novo, então o frontend manda ``current_open_id`` nulo/defasado. O relink deve
    resolver o aberto ATUAL do contato pelo banco e absorvê-lo — senão o reopen do anterior
    colidia com o novo aberto ('Já existe um protocolo aberto para este contato')."""
    built = build_app(["gowa", "protocolos"])
    c = built.client
    plogic = _logic()
    cid = 990501
    now = time.time()
    prev = _seed_protocolo(cid, status="fechado", closed_at=now - 60)
    new_open = _seed_protocolo(cid, status="aberto", closed_at=None)  # o que o auto_link abriu
    cyc = _seed_ciclo(new_open, 772001, cid)

    # Frontend manda SEM current_open_id (defasado) — antes disto dava 409.
    r = c.post(f"/api/plugins/protocolos/protocolos/{prev}/relink", json={})
    assert r.status_code == 200, r.text
    assert plogic.get_protocolo(prev)["status"] == "aberto"
    assert plogic.get_protocolo(new_open) is None          # novo absorvido/descartado
    assert _count_open(cid) == 1                            # 1 aberto (o anterior reaberto)
    with get_engine().connect() as conn:
        pid = conn.execute(text(
            "SELECT protocolo_id FROM plugin_protocolos_atendimentos WHERE id=:i"),
            {"i": cyc}).scalar()
    assert int(pid) == prev                                 # ciclo repontado p/ o anterior


def test_suggest_during_window(build_app):
    """Dentro da janela (protocolo fechado há pouco, não revisado) a sugestão aparece —
    ela é consumida no momento de RESOLVER o atendimento."""
    built = build_app(["gowa", "protocolos"])
    plogic = _logic()
    cid = 990601
    _seed_protocolo(cid, status="fechado", closed_at=time.time() - 120)
    assert plogic.relink_suggestion_for_contact(cid)["suggest"] is True


def test_open_protocol_still_suggests(build_app):
    """A mensagem do cliente SEMPRE abre um protocolo novo — logo, no momento de resolver
    existe um protocolo aberto E um anterior fechado: a pergunta continua valendo."""
    built = build_app(["gowa", "protocolos"])
    plogic = _logic()
    cid = 990602
    _seed_protocolo(cid, status="fechado", closed_at=time.time() - 120)
    _seed_protocolo(cid, status="aberto", closed_at=None)
    assert plogic.relink_suggestion_for_contact(cid)["suggest"] is True


def test_reviewed_stops_suggest(build_app):
    """Protocolo anterior marcado como revisado → para de sugerir (não repergunta)."""
    built = build_app(["gowa", "protocolos"])
    plogic = _logic()
    cid = 990603
    prev = _seed_protocolo(cid, status="fechado", closed_at=time.time() - 120)
    assert plogic.relink_suggestion_for_contact(cid)["suggest"] is True
    plogic.mark_relink_reviewed(prev)
    assert plogic.relink_suggestion_for_contact(cid)["suggest"] is False


def test_reopen_clears_reviewed_flag(build_app):
    """Reabrir um protocolo limpa o relink_reviewed (fica fresco para um futuro ciclo)."""
    built = build_app(["gowa", "protocolos"])
    plogic = _logic()
    cid = 990604
    prev = _seed_protocolo(cid, status="fechado", closed_at=time.time() - 120)
    plogic.mark_relink_reviewed(prev)
    at, err = plogic.reopen_protocolo(prev)
    assert err is None and at["status"] == "aberto"
    assert not plogic.get_protocolo(prev).get("relink_reviewed")


def test_relink_rejects_not_closed_wrong_contact_and_missing(build_app):
    built = build_app(["gowa", "protocolos"])
    c = built.client
    now = time.time()

    # (1) anterior NÃO está fechado → 409.
    cid = 990301
    open_prev = _seed_protocolo(cid, status="aberto", closed_at=None)
    r = c.post(f"/api/plugins/protocolos/protocolos/{open_prev}/relink", json={})
    assert r.status_code == 409, r.text

    # (2) current_open de OUTRO contato → 409 (e sem efeito colateral: prev segue fechado).
    prev = _seed_protocolo(990302, status="fechado", closed_at=now - 60)
    other = _seed_protocolo(990399, status="aberto", closed_at=None)
    r = c.post(f"/api/plugins/protocolos/protocolos/{prev}/relink",
               json={"current_open_id": other})
    assert r.status_code == 409, r.text
    assert _logic().get_protocolo(prev)["status"] == "fechado"
    assert _logic().get_protocolo(other)["status"] == "aberto"

    # (3) protocolo inexistente → 404.
    r = c.post("/api/plugins/protocolos/protocolos/99999999/relink", json={})
    assert r.status_code == 404, r.text
