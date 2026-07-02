"""Plano 29 · Fase A8 — teto global de tool calls por run do Agent AGNO.

Resolução: env WHATSBOT_TOOL_CALL_LIMIT > config ai_tool_call_limit_total >
DEFAULT_TOOL_CALL_LIMIT; 0/não-positivo desliga o teto.

    venv/bin/python -m pytest tests/test_tool_call_limit.py -q
"""

from __future__ import annotations

from agent import agno_engine
from db.repositories import config_repo


def test_config_dirige_o_teto(_engine_ready, monkeypatch):
    monkeypatch.delenv("WHATSBOT_TOOL_CALL_LIMIT", raising=False)
    try:
        config_repo.set("ai_tool_call_limit_total", 7)
        assert agno_engine._resolve_tool_call_limit() == 7

        config_repo.set("ai_tool_call_limit_total", 0)
        assert agno_engine._resolve_tool_call_limit() is None

        config_repo.set("ai_tool_call_limit_total", "lixo")
        assert agno_engine._resolve_tool_call_limit() \
            == agno_engine.DEFAULT_TOOL_CALL_LIMIT

        # Env tem precedência sobre a config.
        config_repo.set("ai_tool_call_limit_total", 7)
        monkeypatch.setenv("WHATSBOT_TOOL_CALL_LIMIT", "3")
        assert agno_engine._resolve_tool_call_limit() == 3
    finally:
        config_repo.set("ai_tool_call_limit_total", 25)


def test_default_sem_config(_engine_ready, monkeypatch):
    monkeypatch.delenv("WHATSBOT_TOOL_CALL_LIMIT", raising=False)
    config_repo.delete_prefix("ai_tool_call_limit_total")
    assert agno_engine._resolve_tool_call_limit() \
        == agno_engine.DEFAULT_TOOL_CALL_LIMIT
