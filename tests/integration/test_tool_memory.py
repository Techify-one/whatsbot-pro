"""Plano 37 · Fase A1 — memória compacta de tool no contexto do turno.

Cobre ``agent.tool_memory.build_block``: a partir dos cards ``tool_call`` já
persistidos + os ``custom_attributes`` da conversa, monta um bloco ``system``
curto que lista o que já rodou — sem base64, truncado, escopado por conversa,
desligável pelo kill-switch ``ai_tool_memory_enabled``.

    venv/bin/python -m pytest tests/integration/test_tool_memory.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import tool_memory
from db.repositories import (
    config_repo, contact_repo, conversation_repo, custom_attribute_repo, message_repo,
)
from db.tables import conversations as conv_tbl

PHONE = "5511666660001"


class _Contact:
    """Minimal stand-in for ContactMemory (id + inbox_id drive the scoped resolver)."""

    def __init__(self, cid: int, inbox_id: int | None):
        self.id = cid
        self.inbox_id = inbox_id


@pytest.fixture
def conv_with_tools(_engine_ready):
    """Contato com conversa aberta, dois cards de tool e atributos definidos."""
    config_repo.set("ai_tool_memory_enabled", True)
    c = contact_repo.get_or_create(PHONE)
    conversation_repo.resolve_for_contact(
        c["id"], f"{PHONE}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(c["id"])
    # Dois cards tool_call no formato do painel (🔧 nome / args / → resultado).
    message_repo.add(
        c["id"], "tool_call",
        "\U0001f527 pesquisar_ofertas\ntermo: failover\n→ 1 oferta: SCRIPTS (O06C57F42)",
        conversation_id=conv["id"])
    message_repo.add(
        c["id"], "tool_call",
        "\U0001f527 set_custom_attribute\nkey: codigo_oferta\nvalue: O06C57F42\n→ salvo",
        conversation_id=conv["id"])
    # Atributo definido na conversa.
    custom_attribute_repo.set_values(conv_tbl, conv["id"], {"codigo_oferta": "O06C57F42"})
    yield _Contact(c["id"], conv["inbox_id"]), conv


def test_block_lista_tools_e_atributos(conv_with_tools):
    contact, _conv = conv_with_tools
    block = tool_memory.build_block(contact)
    assert block is not None
    # Ferramentas já executadas (nome + args), sem a linha de resultado.
    assert "pesquisar_ofertas(termo: failover)" in block
    assert "set_custom_attribute" in block
    assert "1 oferta" not in block  # a linha "→ resultado" foi descartada
    # Atributos já definidos.
    assert "codigo_oferta=O06C57F42" in block


def test_kill_switch_off_remove_bloco(conv_with_tools):
    contact, _conv = conv_with_tools
    config_repo.set("ai_tool_memory_enabled", False)
    try:
        assert tool_memory.build_block(contact) is None
    finally:
        config_repo.set("ai_tool_memory_enabled", True)


def test_sem_conversa_aberta_retorna_none(_engine_ready):
    c = contact_repo.get_or_create("5511666660099")
    conv = conversation_repo.get_open_for_contact(c["id"])
    if conv:
        conversation_repo.set_status(conv["id"], "closed")
    assert tool_memory.build_block(_Contact(c["id"], None)) is None


def test_base64_e_removido(_engine_ready):
    c = contact_repo.get_or_create("5511666660055")
    conversation_repo.resolve_for_contact(
        c["id"], "5511666660055@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(c["id"])
    blob = "A" * 300  # simula um base64 gigante vazado num arg
    message_repo.add(
        c["id"], "tool_call",
        f"\U0001f527 enviar_arquivo\ndata: {blob}\n→ ok",
        conversation_id=conv["id"])
    block = tool_memory.build_block(_Contact(c["id"], conv["inbox_id"]))
    assert block is not None
    assert blob not in block
    assert "[…]" in block


def test_signature_ignora_resultado_gigante():
    # _tool_signature é pura: nunca inclui a linha "→ resultado".
    huge = "X" * 5000
    sig = tool_memory._tool_signature(
        f"\U0001f527 minha_tool\narg: 1\n→ {huge}")
    assert sig == "minha_tool(arg: 1)"
    assert huge not in sig
