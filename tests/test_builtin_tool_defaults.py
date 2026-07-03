"""Plano 30 · F3 (WS2) — ``transferir_agente`` nasce DESLIGADA (instalação nova).

Os DOIS seeds consultam o mesmo conjunto ``OFF_BY_DEFAULT_TOOLS``:
``seed_builtin_tools`` (→ ``ai_tools.enabled``) e ``default_override_enabled``
via ``ToolRegistry.register_tool``/``ensure`` (→ ``tool_overrides.enabled``).
Rows existentes nunca regridem (``ensure`` só atualiza ``plugin_id`` no
conflito; o seed pula rows presentes) — bancos existentes ficam intocados (D7).

Rodar: venv/bin/python -m pytest tests/test_builtin_tool_defaults.py -q
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete

from agent import ai_builtin_tools
from agent.tools.transferir_agente import TRANSFERIR_AGENTE_TOOL, execute
from db.engine import get_engine
from db.repositories import tool_override_repo, tool_repo
from db.tables import tool_overrides


def _wipe_tool_state(name: str) -> None:
    """Simula instalação nova para UMA tool: remove ai_tools + tool_overrides."""
    tool_repo.delete(name)
    with get_engine().begin() as conn:
        conn.execute(sa_delete(tool_overrides).where(tool_overrides.c.name == name))


def _restore_enabled(name: str) -> None:
    """Volta a tool ao estado ligado (não vazar estado OFF pros outros testes)."""
    if tool_repo.get(name) is not None:
        tool_repo.set_enabled(name, True)
    tool_override_repo.ensure(name, None, default_enabled=True)
    tool_override_repo.upsert(name, enabled=True)


def test_transferir_agente_nasce_off_nos_dois_seeds(_engine_ready):
    name = "transferir_agente"
    try:
        _wipe_tool_state(name)

        # Seed 1 — ai_tools: nasce desligada (só ela).
        ai_builtin_tools.seed_builtin_tools()
        row = tool_repo.get(name)
        assert row is not None and row.get("kind") == "builtin"
        assert not row.get("enabled"), "ai_tools: transferir_agente deve nascer OFF"

        # Com a row ai_tools desligada, o default do override segue OFF.
        assert ai_builtin_tools.default_override_enabled(name) is False

        # Seed 2 — tool_overrides via registro no registry (caminho real do boot).
        from agent.tool_registry import ToolRegistry
        registry = ToolRegistry()
        registry.register_tool(TRANSFERIR_AGENTE_TOOL, execute)
        ov = tool_override_repo.get(name)
        assert ov is not None
        assert not ov["enabled"], "tool_overrides: transferir_agente deve nascer OFF"

        # Registrar de novo (boot seguinte) não regride nem re-liga nada.
        tool_override_repo.upsert(name, enabled=True)   # operador ligou na UI
        tool_override_repo.ensure(name, None, default_enabled=False)
        assert tool_override_repo.get(name)["enabled"], (
            "ensure em row existente não pode regredir o enabled do operador")
    finally:
        _restore_enabled(name)


def test_demais_builtins_continuam_nascendo_on(_engine_ready):
    for name in ("save_contact_info", "transfer_to_human", "set_custom_attribute"):
        assert name not in ai_builtin_tools.OFF_BY_DEFAULT_TOOLS
        assert ai_builtin_tools.default_override_enabled(name) is True


def test_default_override_enabled_respeita_intencao_do_operador(_engine_ready):
    """Operador ligou em ai_tools (UI unificada) → override recriado nasce ON.

    Cobre o ciclo: nasce OFF → operador liga via ai_tools → restart (a row de
    override foi limpa pelo delete_orphans enquanto desregistrada) → a recriação
    segue a intenção do operador, senão o "ligar" não sobreviveria ao restart.
    """
    name = "transferir_agente"
    try:
        _wipe_tool_state(name)
        ai_builtin_tools.seed_builtin_tools()          # nasce OFF
        assert ai_builtin_tools.default_override_enabled(name) is False

        tool_repo.set_enabled(name, True)              # operador ligou na UI
        assert ai_builtin_tools.default_override_enabled(name) is True

        from agent.tool_registry import ToolRegistry
        registry = ToolRegistry()
        registry.register_tool(TRANSFERIR_AGENTE_TOOL, execute)
        assert tool_override_repo.get(name)["enabled"], (
            "override recriado no boot deve espelhar ai_tools.enabled=1")
    finally:
        _restore_enabled(name)
