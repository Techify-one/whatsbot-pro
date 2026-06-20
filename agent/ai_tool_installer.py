"""Code-in-DB tool installer (ISOLATED — retrofit P62/P67).

Materialises ``ai_tools.code`` to ``storages/ai_tools/<name>.py`` (for inspection),
resolves the declared dependencies (check-before-install, so pip only touches the
network the first time a spec set changes), discovers + validates the WhatsBot
tool contract (``schema dict + execute(ctx, args)``) and registers it in the
handler's tool registry — but BOTH schema discovery AND per-call execution run in
an ISOLATED SUBPROCESS, not in the host process.

The DB-stored Python NEVER runs in the host process:
  • schema discovery → ``agent.tool_isolation.describe_isolated_tool`` spawns
    ``python -m agent.tool_runner`` in *describe* mode (import + read schema only);
  • each tool call → the registered executor is a thin wrapper that calls
    ``agent.tool_isolation.run_isolated_tool`` in *execute* mode (process group +
    die-with-parent + RLIMIT_CPU/RLIMIT_AS + wall-clock timeout). The wrapper
    forwards only a SAFE snapshot of the ToolContext (contact phone/info), not the
    handler/DB/LLM key.

Fail-closed: any problem (bad name, dep install failure, discovery error, contract
violation) marks the row ``install_status='failed'`` with the error and the tool
is NOT registered — the app still boots and the webhook keeps working.

Precedence: the installer runs AFTER core + plugin tools are registered, so the
registry's collision no-op gives code precedence over the DB (a DB tool can never
hijack a core/plugin tool name).

This whole path is still gated behind the ``ai_tools_code_enabled`` kill-switch
(OFF by default) at the call site in ``server/app.py``. The retrofit removes the
in-process ``exec_module`` RCE (former SECURITY DEBT P62); the remaining residual
is RBAC (plano 03) for *who* may author code-in-DB tools.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agent.tool_isolation import (
    IsolatedToolError,
    describe_isolated_tool,
    run_isolated_tool,
)
from db.repositories import tool_repo
from plugins import pkg_deps

logger = logging.getLogger(__name__)

# Tool name == schema function name == usage.call_type — keep it conservative.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


# --------------------------------------------------------------------------- #
# Filesystem
# --------------------------------------------------------------------------- #
def ai_tools_dir(data_dir) -> Path:
    d = Path(data_dir) / "storages" / "ai_tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Isolated executor factory
# --------------------------------------------------------------------------- #
def _make_isolated_executor(name: str, code: str):
    """Build a registry executor that runs the tool in an isolated subprocess.

    Signature matches a normal tool executor — ``execute(ctx, args) -> str|None``
    — so the handler's generic dispatch (and all plugin filters/events around it)
    works unchanged. The closure captures ``name``/``code`` so each call spawns a
    fresh isolated runner.
    """

    def _isolated_execute(ctx, args):
        return run_isolated_tool(name, code, ctx, args or {})

    return _isolated_execute


# --------------------------------------------------------------------------- #
# Dependency install
# --------------------------------------------------------------------------- #
def _ensure_deps(row: dict) -> None:
    name = row["name"]
    deps = row.get("dependencies") or []
    if not deps:
        return
    # Cache marker lives in the row; pip only runs when the spec set changed.
    if row.get("installed_deps") == deps:
        return
    tool_repo.set_status(name, "installing", None)
    ran = pkg_deps.ensure_pip_deps(deps, already=None, label=f"AI tool '{name}'")
    if ran:
        tool_repo.set_installed_deps(name, deps)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def _process_tool(handler, row: dict, tools_dir: Path) -> int:
    name = row["name"]
    if not NAME_RE.match(name or ""):
        raise ValueError(f"invalid tool name {name!r} (must match {NAME_RE.pattern})")

    _ensure_deps(row)

    code = row.get("code") or ""
    # Keep a copy on disk for inspection/debug (NOT imported in-process).
    path = tools_dir / f"{name}.py"
    path.write_text(code, encoding="utf-8")

    # Discover schema(s) in an isolated subprocess (import never runs in-process).
    try:
        schemas = describe_isolated_tool(name, code)
    except IsolatedToolError as e:
        raise ValueError(str(e)) from e

    # Each schema gets the SAME isolated executor — the runner dispatches to the
    # right ``execute``/``CORE_TOOLS`` entry by tool name on its side.
    pairs = [(schema, _make_isolated_executor(name, code)) for schema in schemas]
    registered = handler.register_ai_tools(pairs)
    tool_repo.set_status(name, "ok", None)
    return registered


def install_and_register(handler, data_dir) -> None:
    """Install + register every enabled code-in-DB tool. Best-effort per tool."""
    try:
        rows = tool_repo.list_enabled()
    except Exception as e:
        logger.warning("AI tools: cannot list enabled rows (%s)", e)
        return
    if not rows:
        return

    tools_dir = ai_tools_dir(data_dir)

    ok = 0
    for row in rows:
        name = row.get("name", "?")
        try:
            ok += _process_tool(handler, row, tools_dir)
        except Exception as e:
            logger.error("AI tool '%s' failed to install/register: %s", name, e)
            try:
                tool_repo.set_status(name, "failed", str(e))
            except Exception:
                pass
    if ok:
        logger.info("AI engine: registered %d isolated code-in-DB tool(s)", ok)
