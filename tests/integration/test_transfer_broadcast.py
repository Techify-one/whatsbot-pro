"""Plano 33 · F1 — o handoff da IA (``transferir_agente``) emite
``conversation_updated`` com ``active_agent_key``, dando paridade com o handoff
MANUAL (que já atualiza o badge do agente ao vivo). Regressão: antes o handoff da
IA só emitia o evento de domínio ``conversation.agent_changed``, que está FORA do
mapa de projeção WS — o badge só atualizava no refresh.

    venv/bin/python -m pytest tests/integration/test_transfer_broadcast.py -q
"""

from __future__ import annotations

import pytest

from agent import agent_factory
from agent.tools import transferir_agente
from db.repositories import agent_repo, contact_repo, conversation_repo
from ai_engine import dynamic_registry

PHONE = "5511777770099"


class _Contact:
    def __init__(self, cid: int, phone: str):
        self.id = cid
        self.phone = phone
        self.channel_id = "default"
        self.is_group = False


class _Ctx:
    def __init__(self, contact):
        self.contact = contact


@pytest.fixture
def transfer_world(_engine_ready):
    """Roteador + comercial no DB; contato com conversa aberta atendida pelo roteador."""
    agent_factory.seed_default_agent()
    agent_repo.save("roteador33", display_name="Roteador",
                    prompt="Você roteia.", model_config={"model": "test/model"},
                    tool_names=None, enabled=True,
                    is_router=True, routing_targets=["comercial33"])
    agent_repo.save("comercial33", display_name="Comercial",
                    prompt="Você vende.", model_config={"model": "test/model"},
                    tool_names=None, enabled=True)
    dynamic_registry.invalidate()

    c = contact_repo.get_or_create(PHONE)
    conversation_repo.resolve_for_contact(
        c["id"], f"{PHONE}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(c["id"])
    conversation_repo.set_agent(conv["id"], "roteador33")
    dynamic_registry.invalidate()
    yield _Contact(c["id"], PHONE), conv
    conversation_repo.set_agent(conv["id"], None)
    dynamic_registry.invalidate()


def test_handoff_ia_emite_conversation_updated(transfer_world, monkeypatch):
    """O handoff persiste E emite conversation_updated com active_agent_key=target."""
    contact, conv = transfer_world
    events: list = []
    monkeypatch.setattr("plugins.context.broadcast",
                        lambda event, data: events.append((event, data)))

    out = transferir_agente.execute(_Ctx(contact), {"agente": "comercial33"})

    # o handoff persistiu…
    now = conversation_repo.get_open_for_contact(contact.id)
    assert now["active_agent_key"] == "comercial33"
    assert "Transferência registrada" in out

    # …e emitiu conversation_updated com o payload que o front consome (F1).
    updates = [d for e, d in events if e == "conversation_updated"]
    assert len(updates) == 1, f"esperava 1 conversation_updated, veio {len(updates)}"
    payload = updates[0]
    assert payload["active_agent_key"] == "comercial33"
    assert payload["conversation_id"] == conv["id"]
    assert payload["contact_id"] == contact.id


def test_handoff_broadcast_falho_nao_derruba(transfer_world, monkeypatch):
    """Best-effort (D-princípio): um broadcast que levanta não quebra o handoff."""
    contact, conv = transfer_world

    def _boom(event, data):
        raise RuntimeError("ws down")

    monkeypatch.setattr("plugins.context.broadcast", _boom)

    out = transferir_agente.execute(_Ctx(contact), {"agente": "comercial33"})
    assert conversation_repo.get_open_for_contact(contact.id)["active_agent_key"] == "comercial33"
    assert "Transferência registrada" in out
