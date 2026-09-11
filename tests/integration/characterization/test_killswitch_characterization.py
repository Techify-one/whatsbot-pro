"""Golden-master characterization of the CODE-IN-DB KILL-SWITCH P62
(Plano 23 · Fase A2b — BLOCKING; guards Wave-2 block B5).

Locks the CURRENT behavior of the ``ai_tools_code_enabled`` security gate BEFORE
B5 touches the AI-engine / code-in-DB installer wiring. This is safety-critical:
the flag must keep arbitrary Python persisted in the ``ai_tools`` table from being
installed, schema-discovered, REGISTERED in the tool registry, or EXECUTED in the
host process — unless an operator has explicitly opted in.

THE SEAM (precise)
──────────────────
The gate is a single ``if`` in the synchronous body of ``create_app``
(``server/app.py``), BEFORE the lifespan:

    if settings.get("ai_tools_code_enabled", False):
        ai_tool_installer.install_and_register(agent_handler, settings.data_dir)
    else:
        # skip the installer entirely

``ai_tool_installer.install_and_register`` is the ONLY path that (a) runs the
DB-stored code's import (isolated schema discovery, ``describe_isolated_tool``)
and (b) calls ``handler.register_ai_tools`` to add the ``kind='code'`` tool to the
registry. So "gate OFF" == "the installer is never called" == "the row's code
never runs and the tool name never enters ``known_tool_names()``". ``build_test_app``
runs ``create_app`` for real, so the gate is exercised end-to-end.

The four core tools (``save_contact_info`` / ``transfer_to_human`` /
``set_custom_attribute`` / ``transferir_agente``) are registered at AgentHandler
construction from the on-disk ``CORE_TOOLS`` and re-seeded as ``kind='builtin'``
rows by ``ai_builtin_tools`` — they are NOT gated by the kill-switch (they run
in-process, are core functionality). They are therefore present in EVERY cell;
the golden's "registered set" view intersects with a fixed fixture vocabulary so
the assertion is deterministic regardless of what other suite tests put in the
shared ``ai_tools`` table.

CHARACTERIZED BUG / DOCS-vs-REALITY GAP (captured, not fixed)
────────────────────────────────────────────────────────────
CLAUDE.md (table of ``ai_*`` tables) and the Plano-23 brief describe ``ai_tools_code_enabled`` as
"default OFF". The CALL SITE reads ``settings.get("ai_tools_code_enabled", False)``
(fallback False) which LOOKS off-by-default. But ``DEFAULT_CONFIG`` in
``config/settings.py`` seeds the key to ``True`` and ``Settings.get`` falls back to
``DEFAULT_CONFIG`` (NOT to the ``default=`` arg) when the row is absent — so on a
fresh install the resolved value is **True** and the installer RUNS by default.
The only ways to actually disable it are an explicit ``ai_tools_code_enabled=False``
row or the env ``WHATSBOT_AI_TOOLS_CODE=0``. Cell 3 captures this exact gap; this
file ASSERTS the real default, so a future "make it truly OFF by default" change
is a deliberate golden refresh.

Discipline: ADDITIVE only. We never change app/source and never "fix" behavior.
Likely bugs are CAPTURED and flagged with ``# CHARACTERIZED BUG:`` comments.

Distinct tool names + phones per cell (the suite shares one process DB).
"""

from __future__ import annotations

import os

import pytest

from tests.golden import normalize, golden_assert


# ── Tool source fixtures ─────────────────────────────────────────────────────
# A DANGEROUS code-in-DB tool: its module-level body writes a sentinel file at
# IMPORT time. If the killed-switch ever lets this code reach the (isolated)
# importer, the sentinel proves the dangerous body ran. With the gate OFF the
# installer is never called, so neither the isolated describe NOR the host ever
# imports it — the sentinel must NOT exist. (We point the write at a path we
# control via an env var the tool reads, so we can detect a leak deterministically
# without depending on the isolated process's cwd.)
_DANGER_TOOL_CODE = """
import os, pathlib
_sentinel = os.environ.get("CHARZ_KILLSWITCH_SENTINEL")
if _sentinel:
    pathlib.Path(_sentinel).write_text("DANGEROUS_CODE_RAN", encoding="utf-8")
SCHEMA = {
    "type": "function",
    "function": {
        "name": "charz_ks_danger",
        "description": "Dangerous code-in-DB tool (writes a sentinel at import).",
        "parameters": {"type": "object", "properties": {}},
    },
}
def execute(ctx, args):
    return "danger-result"
"""

# An INERT code-in-DB tool: no import-time side effects, returns a fixed string.
# Safe to actually install (isolated subprocess) in the gate-ON cell.
_INERT_TOOL_CODE = """
SCHEMA = {
    "type": "function",
    "function": {
        "name": "charz_ks_inert",
        "description": "Inert code-in-DB tool (no side effects).",
        "parameters": {"type": "object", "properties": {}},
    },
}
def execute(ctx, args):
    return "inert-result"
"""

# A second inert tool used by the disabled-row cell.
_INERT_DISABLED_CODE = _INERT_TOOL_CODE.replace("charz_ks_inert", "charz_ks_disabled")

# Fixed vocabulary the golden's "registered set" view intersects with, so the
# assertion is independent of unrelated rows other suite tests may add. The four
# builtins are ALWAYS registered (kill-switch does not gate them); the three
# code-in-DB names appear only when the installer registers them.
_FIXTURE_TOOLS = (
    "save_contact_info", "transfer_to_human",
    "set_custom_attribute", "transferir_agente",
    "charz_ks_danger", "charz_ks_inert", "charz_ks_disabled",
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _insert_tool(name: str, code: str, *, enabled: bool = True) -> None:
    """Insert/replace a ``kind='code'`` ai_tools row carrying ``code``."""
    from db.repositories import tool_repo
    tool_repo.save(name, description="charz", code=code,
                   dependencies=[], enabled=enabled, kind="code")


def _drop_tool(name: str) -> None:
    from db.repositories import tool_repo
    try:
        tool_repo.delete(name)
    except Exception:
        pass


def _set_flag(value) -> None:
    """Write the kill-switch config row (or delete it to test the true default)."""
    from db.repositories import config_repo
    if value is _UNSET:
        config_repo.delete_prefix("ai_tools_code_enabled")
    else:
        config_repo.set("ai_tools_code_enabled", value)


def _snapshot_flag():
    """Capture the raw ``config`` row value (``_UNSET`` if absent) so finally can
    restore the EXACT pre-test state — the suite shares one process DB and the
    seeded default is ``True``, so hard-resetting to ``False`` would leave the DB
    in a non-default state for whatever test runs next."""
    from db.repositories import config_repo
    val = config_repo.get("ai_tools_code_enabled", _UNSET)
    return val


_UNSET = object()


def _registered_view(handler) -> dict:
    """The kill-switch-relevant slice of the live tool registry.

    Intersects the registered names with the fixed fixture vocabulary (so the
    golden is stable under shared-DB pollution), and records each fixture tool's
    registration status + its executor ``plugin_id`` (None == core/code-in-DB,
    a str == a plugin tool — never the case here). Sorted for determinism.
    """
    known = handler.known_tool_names()
    executors = getattr(handler, "_tool_executors", {})
    registered = sorted(n for n in _FIXTURE_TOOLS if n in known)
    plugin_ids = {
        n: executors.get(n, (None, "<MISSING>"))[1]
        for n in registered
    }
    return {
        "registered_fixture_tools": registered,
        "plugin_ids": plugin_ids,
    }


def _capture(built, *, code_tool_names: tuple[str, ...], sentinel_path=None) -> dict:
    """Build the normalized golden facet for one flag state.

    Captures: the resolved gate value the call site sees, the registered-tool set
    view, whether each code-in-DB tool we inserted is registered, and whether the
    dangerous sentinel file exists (proving the dangerous code did/didn't run).
    """
    resolved = built.settings.get("ai_tools_code_enabled", False)
    view = _registered_view(built.agent_handler)
    return {
        # What the gate at server/app.py actually evaluates.
        "gate_resolved_value": resolved,
        "registered": view["registered_fixture_tools"],
        "plugin_ids": view["plugin_ids"],
        "code_tools_registered": {
            n: (n in built.agent_handler.known_tool_names())
            for n in code_tool_names
        },
        # True only if the dangerous import body ran (sentinel written).
        "dangerous_code_ran": bool(sentinel_path and sentinel_path.exists()),
    }


# ── Cell 1: gate explicitly OFF — dangerous code is NOT installed/run ─────────

def test_killswitch_off_blocks_install(build_app, tmp_path):
    """With ``ai_tools_code_enabled=False`` the installer is never called: a
    ``kind='code'`` ai_tools row carrying dangerous import-time code is NOT
    registered and its code NEVER runs (no isolated describe, no host import).

    THE safety invariant — the registered fixture set is exactly the four
    builtins; ``charz_ks_danger`` is absent; the sentinel file does not exist.
    """
    sentinel = tmp_path / "danger_off.flag"
    prev_env = os.environ.get("CHARZ_KILLSWITCH_SENTINEL")
    os.environ["CHARZ_KILLSWITCH_SENTINEL"] = str(sentinel)
    orig = _snapshot_flag()
    try:
        _set_flag(False)
        _insert_tool("charz_ks_danger", _DANGER_TOOL_CODE, enabled=True)
        built = build_app(["gowa"])

        assert "charz_ks_danger" not in built.agent_handler.known_tool_names()
        assert not sentinel.exists(), "dangerous code ran with the gate OFF"

        golden_assert(
            "killswitch_off_blocks_install",
            normalize(_capture(built, code_tool_names=("charz_ks_danger",),
                               sentinel_path=sentinel)),
        )
    finally:
        _drop_tool("charz_ks_danger")
        _set_flag(orig)
        if prev_env is None:
            os.environ.pop("CHARZ_KILLSWITCH_SENTINEL", None)
        else:
            os.environ["CHARZ_KILLSWITCH_SENTINEL"] = prev_env


# ── Cell 2: gate explicitly ON — an INERT code-in-DB tool IS installed ────────

def test_killswitch_on_installs_inert_tool(build_app):
    """With ``ai_tools_code_enabled=True`` the installer runs: an INERT
    ``kind='code'`` tool is schema-discovered in an isolated subprocess and
    REGISTERED with ``plugin_id=None`` (same tag as core — identity is the name).

    The registered fixture set now includes ``charz_ks_inert`` alongside the four
    builtins. (We use an inert tool with no import side effects so it is safe to
    actually let the installer run it.)
    """
    orig = _snapshot_flag()
    _set_flag(True)
    _insert_tool("charz_ks_inert", _INERT_TOOL_CODE, enabled=True)
    try:
        built = build_app(["gowa"])

        assert "charz_ks_inert" in built.agent_handler.known_tool_names()
        # code-in-DB tools are tagged plugin_id=None (registered via register_ai_tools)
        assert built.agent_handler._tool_executors["charz_ks_inert"][1] is None

        golden_assert(
            "killswitch_on_installs_inert_tool",
            normalize(_capture(built, code_tool_names=("charz_ks_inert",))),
        )
    finally:
        _drop_tool("charz_ks_inert")
        _set_flag(orig)


# ── Cell 3: gate UNSET — captures the docs-vs-reality default ─────────────────

def test_killswitch_default_unset_resolves_true(build_app):
    """With NO ``ai_tools_code_enabled`` config row, the resolved gate value is
    ``True`` (from ``DEFAULT_CONFIG``) — NOT ``False`` as the call site's
    ``default=False`` arg and the docs ("default OFF") suggest.

    # CHARACTERIZED BUG: ``ai_tools_code_enabled`` is documented as "default OFF"
    # (CLAUDE.md + Plano 23) and the gate reads ``settings.get(..., False)``, but
    # DEFAULT_CONFIG seeds it True and ``Settings.get`` falls back to DEFAULT_CONFIG
    # (not to the ``default=`` arg). So on a fresh install the installer RUNS by
    # default — code-in-DB is effectively ON unless explicitly disabled. The inert
    # tool below IS installed in this state, proving the gate is open by default.
    """
    orig = _snapshot_flag()
    _set_flag(_UNSET)  # delete the row entirely
    _insert_tool("charz_ks_inert", _INERT_TOOL_CODE, enabled=True)
    try:
        built = build_app(["gowa"])

        # The real default: True → installer runs → inert tool registered.
        assert built.settings.get("ai_tools_code_enabled", False) is True
        assert "charz_ks_inert" in built.agent_handler.known_tool_names()

        golden_assert(
            "killswitch_default_unset_resolves_true",
            normalize(_capture(built, code_tool_names=("charz_ks_inert",))),
        )
    finally:
        _drop_tool("charz_ks_inert")
        # Restore the EXACT pre-test flag state (re-seeds the row if it existed).
        _set_flag(orig)


# ── Cell 4: env override forces OFF even when the DB says ON ──────────────────

def test_killswitch_env_override_forces_off(build_app, tmp_path, monkeypatch):
    """``WHATSBOT_AI_TOOLS_CODE=0`` forces the gate OFF even when the DB row says
    ``True`` — the env override wins in ``Settings.get`` (checked before the DB).
    The dangerous tool is NOT installed and its code never runs.

    This is the documented kill path ("Para travar … setar a env
    WHATSBOT_AI_TOOLS_CODE=0").
    """
    sentinel = tmp_path / "danger_env.flag"
    monkeypatch.setenv("CHARZ_KILLSWITCH_SENTINEL", str(sentinel))
    monkeypatch.setenv("WHATSBOT_AI_TOOLS_CODE", "0")

    orig = _snapshot_flag()
    _set_flag(True)  # DB says ON …
    _insert_tool("charz_ks_danger", _DANGER_TOOL_CODE, enabled=True)
    try:
        built = build_app(["gowa"])

        # … but the env override forces the resolved gate to False.
        assert built.settings.get("ai_tools_code_enabled", False) is False
        assert "charz_ks_danger" not in built.agent_handler.known_tool_names()
        assert not sentinel.exists(), "dangerous code ran despite env kill-switch"

        golden_assert(
            "killswitch_env_override_forces_off",
            normalize(_capture(built, code_tool_names=("charz_ks_danger",),
                               sentinel_path=sentinel)),
        )
    finally:
        _drop_tool("charz_ks_danger")
        _set_flag(orig)


# ── Cell 5: gate ON but the ai_tools row is DISABLED — still not registered ───

def test_killswitch_on_disabled_row_not_registered(build_app):
    """Even with the gate ON, a ``kind='code'`` row with ``enabled=0`` is NOT
    registered: the installer iterates ``tool_repo.list_enabled()``, so a disabled
    tool is skipped entirely. Defense-in-depth on top of the kill-switch.
    """
    orig = _snapshot_flag()
    _set_flag(True)
    _insert_tool("charz_ks_disabled", _INERT_DISABLED_CODE, enabled=False)
    try:
        built = build_app(["gowa"])

        assert "charz_ks_disabled" not in built.agent_handler.known_tool_names()

        golden_assert(
            "killswitch_on_disabled_row_not_registered",
            normalize(_capture(built, code_tool_names=("charz_ks_disabled",))),
        )
    finally:
        _drop_tool("charz_ks_disabled")
        _set_flag(orig)


# ── Cell 6: EXECUTION isolation — the inert tool runs in a SUBPROCESS ─────────

def test_killswitch_on_execution_is_isolated(build_app):
    """STRUCTURAL completeness: when the gate is ON and an inert code-in-DB tool
    is registered, its executor is the isolated-subprocess wrapper (not an
    in-process closure over the live ToolContext). We assert the executor invokes
    ``run_isolated_tool`` — the dangerous DB code never runs in the host even at
    CALL time. We do NOT drive a full agent turn here (that is the agent-turn
    characterization's job); we assert the registered executor's isolation seam
    directly, which is the kill-switch-relevant fact.
    """
    from unittest.mock import patch

    orig = _snapshot_flag()
    _set_flag(True)
    _insert_tool("charz_ks_inert", _INERT_TOOL_CODE, enabled=True)
    try:
        built = build_app(["gowa"])
        assert "charz_ks_inert" in built.agent_handler.known_tool_names()
        executor, plugin_id = built.agent_handler._tool_executors["charz_ks_inert"]
        assert plugin_id is None

        # The registered executor must route through the ISOLATED runner, never
        # exec the DB code in this (host) process.
        with patch("agent.ai_tool_installer.run_isolated_tool",
                   return_value="isolated-ran") as spy:
            out = executor(None, {})
        assert out == "isolated-ran"
        assert spy.call_count == 1
        # First positional arg is the tool name; second is the DB code.
        call_name = spy.call_args.args[0]
        call_code = spy.call_args.args[1]
        assert call_name == "charz_ks_inert"
        assert "execute(ctx, args)" in call_code

        golden_assert(
            "killswitch_on_execution_is_isolated",
            normalize({
                "executor_plugin_id": plugin_id,
                "routes_through_isolated_runner": True,
                "isolated_call_count": spy.call_count,
                "isolated_tool_name": call_name,
            }),
        )
    finally:
        _drop_tool("charz_ks_inert")
        _set_flag(orig)
