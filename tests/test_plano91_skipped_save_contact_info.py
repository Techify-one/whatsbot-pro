"""Plano 91 · F5 — chamada BLOQUEADA de ``save_contact_info`` não reporta sucesso.

Bug latente que a F4 (ligar ``ai_tool_call_limit_per_tool``) ativaria: a detecção
em ``agent_run_service`` não filtrava ``skipped``, então uma chamada recusada pelo
limite (ou por um ``filter.tool.args`` que devolveu ``None``) fazia o turno devolver
``contact_info`` populado — dado que NÃO foi salvo — e isso vaza para o payload de
``llm.after`` que os plugins leem.

Hoje o marcador ``skipped`` é posto em ``agent/agno_engine.py`` nos dois pontos de
bloqueio (filter e limite de tool); o molde do filtro certo já existia em
``messaging_service`` (set_custom_attribute).

    venv/bin/python -m pytest tests/test_plano91_skipped_save_contact_info.py -q
"""

from __future__ import annotations

import asyncio

from agent import agent_factory, agno_engine
from agent.agno_engine import EngineResult
from app.services import agent_run_service

PHONE = "5511665559100"


def _run(built, monkeypatch, executed_tools):
    handler = built.agent_handler
    handler.api_key = "test-key"
    agent_factory.seed_default_agent()
    # O contato precisa existir com algum dado para que um falso-positivo seja
    # visível (se o filtro falhar, ``contact_info`` vem preenchido, não vazio).
    contact = handler._get_contact(PHONE, channel_id="default")
    contact.update_info(name="Cliente Teste")

    async def _fake_run_async(handler_, contact_, sender_, messages_, tools_, **kw):
        return EngineResult(reply="ok", executed_tools=list(executed_tools), usage=None)

    monkeypatch.setattr(agno_engine, "run_async", _fake_run_async)
    return asyncio.run(agent_run_service.run_turn(
        handler, PHONE, "quero informacoes", channel_id="default"))


def test_save_contact_info_bloqueado_nao_reporta_info_salva(build_app, monkeypatch):
    built = build_app(["gowa"])
    result = _run(built, monkeypatch, [{
        "tool": "save_contact_info",
        "args": {"name": "Fulano"},
        "skipped": True,
        "blocked": "Limite de chamadas de 'save_contact_info' atingido.",
    }])
    assert result.contact_info is None


def test_save_contact_info_executado_continua_reportando(build_app, monkeypatch):
    """Regressao: o caminho feliz nao pode ter mudado."""
    built = build_app(["gowa"])
    result = _run(built, monkeypatch, [{
        "tool": "save_contact_info", "args": {"name": "Fulano"}, "result": "ok",
    }])
    assert result.contact_info is not None
    assert result.contact_info.get("name") == "Cliente Teste"


def test_uma_bloqueada_e_uma_executada_ainda_reporta(build_app, monkeypatch):
    """O modelo pode reinvocar depois de um bloqueio; se UMA rodou, houve save."""
    built = build_app(["gowa"])
    result = _run(built, monkeypatch, [
        {"tool": "save_contact_info", "args": {}, "skipped": True, "blocked": "x"},
        {"tool": "save_contact_info", "args": {"name": "Fulano"}, "result": "ok"},
    ])
    assert result.contact_info is not None
