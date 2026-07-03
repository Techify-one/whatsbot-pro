"""Plano 29 · Fase A5 — gate de humano desacoplado do ``ai_active``.

Cobre ``messaging_service._conversation_ai_active``: a IA não responde quando a
conversa aberta tem um humano atribuído (mesmo com ``ai_active`` dessincronizado
em 1) nem quando o contato carrega a tag ``transferido_atendente``; e a
reativação da IA (``conversation_service._transfer`` / ``toggle_contact_ai``)
limpa a tag.

    venv/bin/python -m pytest tests/test_human_gate.py -q
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.messaging_service import _conversation_ai_active
from agent.tools.transfer_to_human import TRANSFER_TAG
from db.repositories import contact_repo, conversation_repo, tag_repo

PHONE = "5511777770001"


class _Contact:
    def __init__(self, cid: int, phone: str):
        self.id = cid
        self.phone = phone


@pytest.fixture
def gate_contact(_engine_ready):
    """Contato com conversa aberta em estado neutro (IA on, sem humano, sem tag)."""
    c = contact_repo.get_or_create(PHONE)
    conversation_repo.resolve_for_contact(
        c["id"], f"{PHONE}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(c["id"])
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=None, active_agent_key=None, ai_active=1)
    tag_repo.remove_contact_tag(c["id"], TRANSFER_TAG)
    yield _Contact(c["id"], PHONE), conv
    # Estado neutro de volta (o engine é global à sessão de testes).
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=None, active_agent_key=None, ai_active=1)
    tag_repo.remove_contact_tag(c["id"], TRANSFER_TAG)


def test_conversa_normal_responde(gate_contact):
    contact, _conv = gate_contact
    assert _conversation_ai_active(contact) is True


def test_ai_active_zero_bloqueia(gate_contact):
    contact, conv = gate_contact
    conversation_repo.set_ai_active(conv["id"], 0)
    assert _conversation_ai_active(contact) is False


def test_humano_atribuido_bloqueia_mesmo_com_flag_dessincronizado(gate_contact):
    """O caso do plano: assignee humano + ai_active preso em 1 → IA cala."""
    contact, conv = gate_contact
    conversation_repo.set_assignee(conv["id"], 42)
    conversation_repo.set_ai_active(conv["id"], 1)  # dessincronizado de propósito
    assert _conversation_ai_active(contact) is False


def test_agente_ia_vinculado_nao_e_bloqueado_pelo_assignee(gate_contact):
    """Com um agente de IA vinculado, o assignee não silencia a IA."""
    contact, conv = gate_contact
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=42, active_agent_key="default", ai_active=1)
    assert _conversation_ai_active(contact) is True


def test_tag_transferido_atendente_bloqueia(gate_contact):
    contact, _conv = gate_contact
    tag_repo.create(TRANSFER_TAG, "#ef4444")
    tag_repo.add_contact_tag(contact.id, TRANSFER_TAG)
    assert _conversation_ai_active(contact) is False
    tag_repo.remove_contact_tag(contact.id, TRANSFER_TAG)
    assert _conversation_ai_active(contact) is True


def test_reativar_ia_limpa_a_tag(gate_contact):
    """``_transfer`` com ai_active=1 (set_ai ON / atribuir à IA) remove a trava."""
    from app.services import conversation_service as conv_svc

    contact, conv = gate_contact
    tag_repo.create(TRANSFER_TAG, "#ef4444")
    tag_repo.add_contact_tag(contact.id, TRANSFER_TAG)
    assert _conversation_ai_active(contact) is False

    deps = SimpleNamespace(agent_handler=None)
    asyncio.run(conv_svc._transfer(
        deps, conv, assignee_user_id=None, active_agent_key="default",
        ai_active=1, mirror_contact_ai=None, contact_id=contact.id))

    assert TRANSFER_TAG not in tag_repo.get_contact_tags(contact.id)
    assert _conversation_ai_active(contact) is True


def test_sem_conversa_fail_open(_engine_ready):
    c = contact_repo.get_or_create("5511777770099")
    # Garante que não há conversa aberta nem tag pendente.
    conv = conversation_repo.get_open_for_contact(c["id"])
    if conv:
        conversation_repo.set_status(conv["id"], "closed")
    tag_repo.remove_contact_tag(c["id"], TRANSFER_TAG)
    assert _conversation_ai_active(_Contact(c["id"], "5511777770099")) is True
