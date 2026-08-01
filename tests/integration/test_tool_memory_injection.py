"""Plano 37 · Fase A3 (barreira) — verificação integrada da memória de tool.

Prova, ponta-a-ponta (``agent_run_service.run_turn`` com o motor AGNO mockado),
que o bloco de memória do A1 REALMENTE chega ao array de ``messages`` enviado ao
motor num turno subsequente. É o "pronto quando" da Fase A1 ("um teste que
inspeciona ... o que vai ao motor") e a barreira automatizável do A3: o modelo do
turno 2 passa a ENXERGAR que ``pesquisar_ofertas`` já rodou e que o atributo já
foi definido — logo, não repete.

A medição de tokens/latência por turno numa conversa real (execs 129–132) é um
passo manual (exige a instância viva + Nexus configurado) — fora do harness.

    venv/bin/python -m pytest tests/integration/test_tool_memory_injection.py -q
"""

from __future__ import annotations

import asyncio

import pytest

from agent import agno_engine, agent_factory
from agent.agno_engine import EngineResult
from app.services import agent_run_service
from db.repositories import config_repo, conversation_repo, custom_attribute_repo
from db.tables import conversations as conv_tbl

PHONE = "5511665550001"


def _run_capturing_messages(built, monkeypatch, *, second_text="segunda mensagem"):
    """Semeia 'turno 1 já rodou pesquisar_ofertas' e roda o turno 2, capturando os
    ``messages`` que chegam ao motor. Retorna a lista de messages."""
    handler = built.agent_handler
    handler.api_key = "test-key"
    agent_factory.seed_default_agent()

    contact = handler._get_contact(PHONE, channel_id="default")
    # Turno 1 (simulado): a IA pesquisou uma oferta e gravou o atributo.
    contact.add_message("user", "quero curso de failover")
    contact.add_message(
        "tool_call",
        "\U0001f527 pesquisar_ofertas\ntermo: failover\n→ 1 oferta: SCRIPTS (O06C57F42)")
    conv = conversation_repo.get_open_for_contact(contact.id)
    custom_attribute_repo.set_values(conv_tbl, conv["id"], {"codigo_oferta": "O06C57F42"})

    captured = {}

    async def _fake_run_async(handler_, contact_, sender_, messages_, tools_, **kw):
        captured["messages"] = messages_
        return EngineResult(reply="ok", executed_tools=[], usage=None)

    monkeypatch.setattr(agno_engine, "run_async", _fake_run_async)
    asyncio.run(agent_run_service.run_turn(
        handler, PHONE, second_text, channel_id="default"))
    return captured.get("messages") or []


def _system_blob(messages) -> str:
    return "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")


def test_a3_memoria_chega_ao_motor_no_turno_seguinte(build_app, monkeypatch):
    config_repo.set("ai_tool_memory_enabled", True)
    built = build_app(["gowa"])
    messages = _run_capturing_messages(built, monkeypatch)
    blob = _system_blob(messages)
    # O turno 2 carrega o bloco de memória: o modelo vê que pesquisar_ofertas já
    # rodou e que codigo_oferta já foi definido → não repete.
    assert "Memória deste atendimento" in blob
    assert "pesquisar_ofertas(termo: failover)" in blob
    assert "codigo_oferta=O06C57F42" in blob
    # A linha "→ resultado" NÃO vaza para o contexto.
    assert "1 oferta" not in blob


def test_a3_kill_switch_off_remove_do_motor(build_app, monkeypatch):
    config_repo.set("ai_tool_memory_enabled", False)
    try:
        built = build_app(["gowa"])
        messages = _run_capturing_messages(
            built, monkeypatch, second_text="terceira mensagem")
        blob = _system_blob(messages)
        assert "Memória deste atendimento" not in blob
    finally:
        config_repo.set("ai_tool_memory_enabled", True)
