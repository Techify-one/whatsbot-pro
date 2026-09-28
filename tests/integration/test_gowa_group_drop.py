"""Plano 173 — descarte de evento de grupo (JID não permitido) na ENTRADA do
webhook GOWA, em DUAS camadas independentes e redundantes:

  1. a ROTA (``Channel.should_drop_inbound``): cache QUENTE (memória, via
     ``channels.jid_allowed.peek``) descarta ANTES do ``channel_repo.get`` e de
     qualquer chamada ao cliente GOWA;
  2. o PARSER (``gowa.inbound.parse_gowa_inbound``): rede de segurança para
     cache FRIO — descarta antes de QUALQUER chamada ao cliente GOWA, mesmo
     quando a camada 1 não decidiu.

Origem: incidente em produção de 2026-09-28 — um disparo em massa em grupos fez
cada mensagem de grupo pagar 2+ chamadas HTTP ao GOWA (socket novo por
chamada) + thread, para só depois ser descartada no ingest. O processo estourou
o limite de descritores de arquivo (``Too many open files``) e caiu em
cascata. Ver ``docs-planos/173-plano-gowa-descartar-grupo-na-entrada.md``.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from channels import jid as jid_classifier
from channels import jid_allowed
from db.repositories import channel_repo, contact_repo

GROUP_JID = "120363173000001@g.us"


def _track_client_calls(client, *names: str) -> list[str]:
    """Envolve métodos do ``FakeGowaClient`` (que NÃO passam por ``.calls`` —
    são atributos reais, não o ``__getattr__`` genérico) para provar que o
    cliente GOWA nunca foi tocado."""
    calls: list[str] = []
    for name in names:
        orig = getattr(client, name)

        def _wrap(*args, _name=name, _orig=orig, **kwargs):
            calls.append(_name)
            return _orig(*args, **kwargs)

        setattr(client, name, _wrap)
    return calls


@contextlib.contextmanager
def _channel_allowed_types(client, channel_id: str, types: list[str]):
    """Seta ``config.allowed_jid_types`` do canal via API (mesmo caminho que
    invalida o cache em produção) e restaura ao sair — o canal ``default`` é
    COMPARTILHADO por toda a sessão de teste (CLAUDE.md / plano 173 D1)."""
    row_before = channel_repo.get(channel_id) or {}
    cfg_before = row_before.get("config")
    if isinstance(cfg_before, str) and cfg_before:
        cfg_before = json.loads(cfg_before)
    types_before = (cfg_before or {}).get("allowed_jid_types")

    r = client.put(f"/api/channels/{channel_id}",
                   json={"config": {"allowed_jid_types": types}})
    assert r.status_code == 200, r.text
    try:
        yield
    finally:
        restore = ({"allowed_jid_types": types_before}
                   if types_before is not None else {})
        client.put(f"/api/channels/{channel_id}", json={"config": restore})
        jid_allowed.reset()


def _group_payload(msg_id: str, body: str = "oi grupo") -> dict:
    return {"event": "message", "payload": {
        "chat_id": GROUP_JID, "from": "5511970001730@s.whatsapp.net",
        "id": msg_id, "body": body, "from_name": "Membro do Grupo"}}


def _person_payload(phone: str, msg_id: str, body: str = "olá") -> dict:
    return {"event": "message", "payload": {
        "from": f"{phone}@s.whatsapp.net", "id": msg_id, "body": body,
        "from_name": "Cliente"}}


# ── F3 — camada da ROTA (cache quente) ──────────────────────────────────────

def test_group_dropped_at_route_with_warm_cache(build_app, monkeypatch):
    """Cache QUENTE: a rota descarta ANTES do ``channel_repo.get`` e sem
    nenhuma chamada ao cliente GOWA — o próprio ponto do incidente."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    client = built.client

    with _channel_allowed_types(client, "default", ["person", "person_lid"]):
        # Esquenta o cache (get_sync) com uma mensagem de pessoa qualquer —
        # canal já resolvido no registry, então isso é o caminho normal.
        r = client.post("/api/webhook/gowa/default",
                        json=_person_payload("5511970001731", "warm_1"))
        assert r.status_code == 200, r.text
        assert jid_allowed.peek("default") == ["person", "person_lid"]

        calls = _track_client_calls(
            built.gowa_client, "get_group_name", "can_bot_send_in_group",
            "is_chat_archived", "get_message_filename")

        import db.repositories.channel_repo as channel_repo_module
        get_calls: list[str] = []
        real_get = channel_repo_module.get

        def _counting_get(channel_id):
            get_calls.append(channel_id)
            return real_get(channel_id)

        monkeypatch.setattr(channel_repo_module, "get", _counting_get)

        r = client.post("/api/webhook/gowa/default",
                        json=_group_payload("grp_warm_1"))

        assert r.status_code == 200, r.text
        assert r.json()["data"] == {"status": "ignored",
                                    "reason": "jid_type_not_allowed"}
        assert get_calls == [], \
            "cache quente não deveria disparar channel_repo.get na rota"
        assert calls == [], "cliente GOWA não deveria ser tocado"
        assert contact_repo.get_by_phone(GROUP_JID) is None


def test_person_still_ingested_with_group_disallowed(build_app):
    """Uma pessoa continua passando normalmente no mesmo canal que descarta
    grupo — o atalho não pode confundir tipos permitidos."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    client = built.client
    phone = "5511970001732"

    with _channel_allowed_types(client, "default", ["person", "person_lid"]):
        r = client.post("/api/webhook/gowa/default",
                        json=_person_payload(phone, "person_ok_1"))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "received"


# ── F2 — camada do PARSER (rede de segurança, cache frio) ───────────────────

def test_group_dropped_at_parser_with_cold_cache(build_app):
    """Logo após mudar a config (cache invalidado = frio), a rota NÃO decide
    (fail-open) e segue o fluxo normal — mas o PARSER descarta antes de
    QUALQUER chamada ao cliente GOWA. Zero calls é o ponto central do plano:
    nem com cache frio o incidente se repete."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    client = built.client

    with _channel_allowed_types(client, "default", ["person", "person_lid"]):
        assert jid_allowed.peek("default") is None, \
            "cache deveria estar frio logo após a mudança de config"

        calls = _track_client_calls(
            built.gowa_client, "get_group_name", "can_bot_send_in_group",
            "is_chat_archived", "get_message_filename")

        r = client.post("/api/webhook/gowa/default",
                        json=_group_payload("grp_cold_1"))
        assert r.status_code == 200, r.text
        assert calls == [], \
            "parser deve descartar ANTES de tocar o cliente GOWA (cache frio)"
        assert contact_repo.get_by_phone(GROUP_JID) is None
        # A leitura de get_sync dentro do parse_inbound aqueceu o cache.
        assert jid_allowed.peek("default") == ["person", "person_lid"]


def _assert_discarded(response) -> None:
    """Um evento descartado responde 200 de UMA das duas formas válidas: a
    ROTA (cache quente) devolve ``{"status": "ignored", ...}`` ANTES de sequer
    parsear; o PARSER (cache frio, safety net) devolve ``{"status": "received",
    "events": 0, ...}`` — o parser produziu zero eventos. As duas são
    "descartado"; qual delas aparece depende só de o cache estar quente ou não
    no momento exato da chamada (a 1ª chamada desta função aquece o cache para
    a 2ª — ambas são corretas)."""
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    if data.get("status") == "ignored":
        return
    assert data.get("status") == "received" and data.get("events") == 0, data


def test_group_receipt_and_reaction_also_dropped(build_app):
    """O descarte vale para QUALQUER evento do chat não permitido, não só
    ``message`` — recibo e reação de grupo também não chegam ao bus."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    client = built.client

    with _channel_allowed_types(client, "default", ["person", "person_lid"]):
        r = client.post("/api/webhook/gowa/default", json={
            "event": "message.ack", "payload": {
                "chat_id": GROUP_JID, "ids": ["grp_ack_1"],
                "receipt_type": "delivered"}})
        _assert_discarded(r)

        r = client.post("/api/webhook/gowa/default", json={
            "event": "message.reaction", "payload": {
                "chat_id": GROUP_JID, "from": "5511970001733@s.whatsapp.net",
                "id": "grp_react_1", "reaction": "👍"}})
        _assert_discarded(r)


# ── Grupo PERMITIDO: comportamento idêntico ao de hoje ──────────────────────

def test_group_allowed_channel_unaffected(build_app):
    """Canal com ``group`` permitido continua chamando o cliente GOWA e
    processando a mensagem normalmente — o atalho não pode confundir o caso
    permitido com o não permitido."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "group_reply_mode": "mention_only"})
    client = built.client

    with _channel_allowed_types(
            client, "default", list(jid_classifier.ALL_JID_TYPES)):
        r = client.post("/api/webhook/gowa/default",
                        json=_group_payload("grp_allowed_1"))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "received"
    built.settings.set("auto_reply", False)


# ── Fail-open: canal legado sem a chave, hook que levanta ───────────────────

def test_legacy_channel_without_saved_key_keeps_group(build_app):
    """Canal SEM ``allowed_jid_types`` salvo cai no fallback de RUNTIME (COM
    ``group`` — plano 103 D2): o atalho não pode silenciar grupo em canal
    legado que nunca configurou o filtro."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    client = built.client
    jid_allowed.reset()

    r = client.post("/api/webhook/gowa/default",
                    json=_group_payload("grp_legacy_1"))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "received", \
        "canal sem a chave salva não deve descartar grupo (fallback com group)"


def test_should_drop_inbound_exception_fails_open(build_app):
    """Uma exceção dentro do hook nunca pode derrubar o webhook nem descartar
    a mensagem — fail-open, fluxo normal."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    client = built.client
    phone = "5511970001734"

    inst = built.app.state.deps.channel_registry.get("default")
    assert inst is not None

    def _boom(raw):
        raise RuntimeError("hook quebrado de propósito")

    inst.should_drop_inbound = _boom
    try:
        r = client.post("/api/webhook/gowa/default",
                        json=_person_payload(phone, "hook_boom_1"))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "received"
    finally:
        del inst.should_drop_inbound
