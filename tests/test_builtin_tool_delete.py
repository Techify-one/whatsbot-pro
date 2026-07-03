"""Plano 30 · F4 (WS3) — delete REAL de builtin com tombstone (D2/D8).

``DELETE /api/ai/tools/transferir_agente`` grava o tombstone
(``config.deleted_builtin_tools``), apaga a row ``ai_tools`` e a row
``tool_overrides``, e agenda restart. No boot seguinte os DOIS caminhos de
re-seed pulam a tool tombada: ``seed_builtin_tools`` (row ``ai_tools``) e
``ToolRegistry.register_tool`` (row ``tool_overrides`` via ``ensure``). Sem
botão de reinstalar (D8) — recriação é manual (tool code-in-DB ou editar a
config no banco).

Rodar: venv/bin/python -m pytest tests/test_builtin_tool_delete.py -q
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import delete as sa_delete

from agent import ai_builtin_tools
from agent.tools.transferir_agente import TRANSFERIR_AGENTE_TOOL, execute
from db.engine import get_engine
from db.repositories import config_repo, tool_override_repo, tool_repo
from db.tables import tool_overrides

NAME = "transferir_agente"


def _clear_tombstones() -> None:
    config_repo.set(ai_builtin_tools.TOMBSTONE_CONFIG_KEY, [])


def _restore_seeded(name: str = NAME) -> None:
    """Deixa a tool de volta no estado seedado+ligado pros próximos testes."""
    _clear_tombstones()
    if tool_repo.get(name) is None:
        ai_builtin_tools.seed_builtin_tools()
    tool_repo.set_enabled(name, True)
    tool_override_repo.ensure(name, None, default_enabled=True)
    tool_override_repo.upsert(name, enabled=True)


def test_tombstone_roundtrip(_engine_ready):
    try:
        _clear_tombstones()
        assert ai_builtin_tools.deleted_builtin_tools() == set()
        ai_builtin_tools.tombstone_builtin(NAME)
        assert NAME in ai_builtin_tools.deleted_builtin_tools()
        # Idempotente — tombar duas vezes não duplica.
        ai_builtin_tools.tombstone_builtin(NAME)
        raw = config_repo.get(ai_builtin_tools.TOMBSTONE_CONFIG_KEY, [])
        assert raw.count(NAME) == 1
    finally:
        _restore_seeded()


def test_seed_e_registro_pulam_builtin_tombada(_engine_ready):
    try:
        # Estado: tool apagada + tombstone gravado (pós-DELETE, pré-restart).
        ai_builtin_tools.tombstone_builtin(NAME)
        tool_repo.delete(NAME)
        with get_engine().begin() as conn:
            conn.execute(sa_delete(tool_overrides).where(tool_overrides.c.name == NAME))

        # Caminho 1 do re-seed: a row ai_tools NÃO volta.
        ai_builtin_tools.seed_builtin_tools()
        assert tool_repo.get(NAME) is None, "seed re-inseriu builtin tombada"

        # Caminho 2 do re-seed: register_tool não registra nem recria override.
        from agent.tool_registry import ToolRegistry
        registry = ToolRegistry()
        registry.register_tool(TRANSFERIR_AGENTE_TOOL, execute)
        assert NAME not in registry.known_tool_names(), (
            "registry registrou builtin tombada")
        assert tool_override_repo.get(NAME) is None, (
            "register_tool recriou a row tool_overrides da builtin tombada")

        # As demais builtins seguem intactas.
        for other in ("save_contact_info", "transfer_to_human",
                      "set_custom_attribute"):
            assert tool_repo.get(other) is not None
    finally:
        _restore_seeded()


def test_delete_endpoint_builtin_deletavel(build_app):
    built = build_app(["gowa"])
    try:
        import server.routes.ai_engine as ai_routes
        with patch.object(ai_routes, "schedule_restart", lambda *a, **k: None):
            r = built.client.delete(f"/api/ai/tools/{NAME}")
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

        assert NAME in ai_builtin_tools.deleted_builtin_tools(), (
            "endpoint não gravou o tombstone")
        assert tool_repo.get(NAME) is None, "row ai_tools não foi apagada"
        assert tool_override_repo.get(NAME) is None, (
            "row tool_overrides não foi apagada")
        # Efeito imediato no processo vivo (antes do restart): fora do registry
        # e do schema efetivo do LLM.
        assert NAME not in built.agent_handler.known_tool_names()
    finally:
        _restore_seeded()


def test_delete_endpoint_builtin_nao_deletavel_segue_bloqueado(build_app):
    built = build_app(["gowa"])
    import server.routes.ai_engine as ai_routes
    with patch.object(ai_routes, "schedule_restart", lambda *a, **k: None):
        r = built.client.delete("/api/ai/tools/save_contact_info")
    assert r.status_code == 400
    assert tool_repo.get("save_contact_info") is not None
    assert "save_contact_info" not in ai_builtin_tools.deleted_builtin_tools()


def test_boot_nao_quebra_com_agente_referenciando_tool_tombada(build_app):
    """Agente com ``tool_names`` incluindo a tool deletada não quebra o boot:
    ``select_active_tools`` simplesmente não encontra o schema (interseção)."""
    from agent.agent_factory import AgentSpec

    built = build_app(["gowa"])
    try:
        import server.routes.ai_engine as ai_routes
        with patch.object(ai_routes, "schedule_restart", lambda *a, **k: None):
            r = built.client.delete(f"/api/ai/tools/{NAME}")
        assert r.status_code == 200, r.text

        spec = AgentSpec(agent_key="spoke_x", base_prompt="x",
                         tool_names=[NAME, "save_contact_info"])
        names = [(s.get("function") or {}).get("name")
                 for s in built.agent_handler._tools.select_active_tools(spec)]
        assert NAME not in names
        assert "save_contact_info" in names

        # Boot novo (app hermético) com o tombstone presente: sobe sem erro e
        # sem a tool.
        built2 = build_app(["gowa"])
        assert NAME not in built2.agent_handler.known_tool_names()
        assert tool_repo.get(NAME) is None
    finally:
        _restore_seeded()
