"""Trava do "Limpar conversa" do Sandbox e da varredura de conversas-fantasma.

Incidente: o botão mandava ``phone=''`` quando o campo estava vazio e o backend
interpretava como "limpar tudo" — apagava as mensagens de todo cliente real em cache.
Com número preenchido, apagava qualquer contato (não só os de teste). E a varredura
de fantasmas (hard DELETE a cada 10 min) tratava nota privada / card de ferramenta
como "conversa vazia", levando a conversa inteira junto.
"""

from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import text, update

from agent.memory import ContactMemory
from db.engine import get_engine
from db.repositories import config_repo, conversation_repo, message_repo
from db.tables import conversations
from server.routes.sandbox import SANDBOX_CONTACT_PREFIX


def _phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _seed(phone: str, n: int = 3) -> int:
    """Contato com ``n`` mensagens do cliente; devolve o contact_id."""
    cm = ContactMemory(phone)
    for i in range(n):
        cm.add_message("user", f"mensagem {i}")
    return cm.id


def _count(contact_id: int) -> int:
    return len(message_repo.get_all(contact_id))


def test_clear_without_phone_is_refused_and_deletes_nothing(build_app, authenticated_admin):
    built = build_app(["gowa"])
    authenticated_admin(built.client)
    real = _phone()
    cid = _seed(real)
    # o cliente real está no cache do handler — o cenário exato do "limpar tudo"
    built.agent_handler._get_contact(real)
    before = _count(cid)
    assert before >= 3

    for body in ({}, {"phone": ""}, {"phone": "   "}):
        r = built.client.post("/api/sandbox/clear", json=body)
        assert r.status_code == 400, r.text
    assert _count(cid) == before


def test_clear_refuses_a_number_the_sandbox_never_flagged(build_app, authenticated_admin):
    built = build_app(["gowa"])
    authenticated_admin(built.client)
    real = _phone()
    cid = _seed(real)
    before = _count(cid)

    r = built.client.post("/api/sandbox/clear", json={"phone": real})

    assert r.status_code == 403, r.text
    assert _count(cid) == before


def test_clear_wipes_a_flagged_number_and_audits_without_content(build_app, authenticated_admin):
    built = build_app(["gowa"])
    authenticated_admin(built.client)
    test_phone, other = _phone(), _phone()
    test_cid, other_cid = _seed(test_phone, 4), _seed(other, 2)
    config_repo.set(f"{SANDBOX_CONTACT_PREFIX}{test_phone}", True)
    test_before, other_before = _count(test_cid), _count(other_cid)

    r = built.client.post("/api/sandbox/clear", json={"phone": test_phone})

    assert r.status_code == 200, r.text
    assert test_before >= 4 and r.json()["data"]["removed"] == test_before
    assert _count(test_cid) == 0
    assert _count(other_cid) == other_before, "limpar um número de teste não pode tocar em outro contato"
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT resource_type, resource_id, after_json FROM audit_log "
            "WHERE action = 'sandbox.clear' ORDER BY id DESC LIMIT 1")).mappings().first()
    assert row is not None and row["resource_id"] == test_phone
    assert json.loads(row["after_json"]) == {"messages_removed": test_before}
    assert "mensagem" not in row["after_json"], "a trilha não leva conteúdo de conversa"


def _backdate(conv_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(conversations.c.id == conv_id)
                     .values(created_at=time.time() - 3600))


def test_ghost_sweep_keeps_a_conversation_that_has_a_private_note(build_app):
    build_app(["gowa"], settings_overrides={"auto_reply": False})
    cutoff = time.time() - 1800

    used = ContactMemory(_phone())
    used_id = used.ensure_conversation_live("user")
    used.add_message("private_note", "nota do atendente: cliente pediu retorno")
    _backdate(used_id)

    tooled = ContactMemory(_phone())
    tooled_id = tooled.ensure_conversation_live("user")
    tooled.add_message("tool_call", "save_contact_info")
    _backdate(tooled_id)

    ghost = ContactMemory(_phone())
    ghost_id = ghost.ensure_conversation_live("user")  # só o card de criação do t=0
    _backdate(ghost_id)

    found = {g["id"] for g in conversation_repo.find_empty_inbound_ghosts(cutoff)}
    assert ghost_id in found, "o fantasma de verdade (só o card de criação) segue sendo varrido"
    assert used_id not in found, "nota privada prova que a conversa foi usada"
    assert tooled_id not in found, "card de tool prova que a conversa foi usada"


def test_delete_all_reports_how_many_rows_it_removed(build_app):
    build_app(["gowa"])
    cid = _seed(_phone(), 5)
    before = _count(cid)
    assert before >= 5
    assert message_repo.delete_all(cid) == before
    assert message_repo.delete_all(cid) == 0
