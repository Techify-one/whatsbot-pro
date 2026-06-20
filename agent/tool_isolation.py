"""Bridge that runs a code-in-DB tool in an isolated subprocess (retrofit P62/P67).

This is the host-side counterpart of :mod:`agent.tool_runner`. Given a tool's
name + source + the live ``ToolContext`` of the current tool call, it:

1. Serializes a SAFE snapshot of the context (contact phone/info, plugin id) —
   the handler/DB/LLM client are deliberately NOT forwarded.
2. Spawns ``python -m agent.tool_runner`` via
   :func:`runtime.subprocess_service.run_oneshot` (process group + die-with-parent
   + ``RLIMIT_CPU``/``RLIMIT_AS`` + wall-clock timeout).
3. Parses the JSON envelope from the child's stdout and returns a feedback
   string for the LLM (the same shape ``_dispatch_tool`` expects: ``str | None``).

All failure modes (timeout, non-zero exit, malformed envelope, tool exception)
collapse into a short feedback string so the agent loop keeps working — never a
host-process crash. Defaults are conservative (10s wall clock, 5s CPU, 512 MiB
address space) and overridable via env for a trusted host.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from runtime.subprocess_service import OneShotResult, run_oneshot

logger = logging.getLogger(__name__)

# Repo root (parent of the ``agent`` package) — the cwd the runner is spawned
# from so ``python -m agent.tool_runner`` resolves regardless of caller cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


# Conservative defaults. The feature is OFF by default (ai_tools_code_enabled);
# when a trusted operator turns it on they can widen these via env.
DEFAULT_TIMEOUT = _env_float("WHATSBOT_AI_TOOL_TIMEOUT", 10.0)            # wall clock, seconds
DEFAULT_CPU_SECONDS = _env_int("WHATSBOT_AI_TOOL_CPU_SECONDS", 5)         # RLIMIT_CPU
DEFAULT_AS_BYTES = _env_int(
    "WHATSBOT_AI_TOOL_MEM_BYTES", 512 * 1024 * 1024                       # RLIMIT_AS (512 MiB)
)


def _serialize_context(ctx: Any) -> dict:
    """Build a JSON-serializable snapshot of the live ToolContext.

    Only safe, read-only fields cross the process boundary. The handler, DB
    opener, tag registry and LLM client are intentionally dropped.
    """
    snapshot: dict = {"phone": "", "is_group": False, "contact": {}, "plugin_id": None}
    if ctx is None:
        return snapshot
    snapshot["plugin_id"] = getattr(ctx, "plugin_id", None)
    contact = getattr(ctx, "contact", None)
    if contact is not None:
        snapshot["phone"] = getattr(contact, "phone", "") or ""
        snapshot["is_group"] = bool(getattr(contact, "is_group", False))
        info = getattr(contact, "info", None)
        if isinstance(info, dict):
            # ``info`` already holds name/email/profession/company/address/
            # observations — all plain JSON types.
            snapshot["contact"] = _json_safe(info)
        tags = getattr(contact, "tags", None)
        if isinstance(tags, list):
            snapshot["contact"]["tags"] = [str(t) for t in tags]
    return snapshot


def _json_safe(value: Any) -> Any:
    """Best-effort coercion of a value into JSON-serializable form."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return str(value)


def _runner_cmd() -> list:
    """Argv to launch the isolated runner: ``python -m agent.tool_runner``."""
    return [sys.executable, "-m", "agent.tool_runner"]


# Schema discovery (describe mode) runs untrusted code at import time too, so it
# is also isolated. It only imports + reads the schema, so it gets a slightly
# longer wall clock but the same memory cap and no in-process exec.
DESCRIBE_TIMEOUT = _env_float("WHATSBOT_AI_TOOL_DESCRIBE_TIMEOUT", 30.0)
DESCRIBE_CPU_SECONDS = _env_int("WHATSBOT_AI_TOOL_DESCRIBE_CPU_SECONDS", 15)


class IsolatedToolError(RuntimeError):
    """Raised by :func:`describe_isolated_tool` when discovery fails.

    The installer catches this to mark the row ``install_status='failed'`` with
    the reason — same fail-closed behavior as the old in-process import path.
    """


def describe_isolated_tool(name: str, code: str) -> list:
    """Import the tool code in an isolated subprocess and return its schema(s).

    Returns a list of validated OpenAI tool-schema dicts. Raises
    :class:`IsolatedToolError` on any failure (bad code, missing/invalid schema,
    timeout) — the DB-stored code never runs in the host process.
    """
    if getattr(sys, "frozen", False):
        raise IsolatedToolError(
            "isolated execution unavailable in a frozen build "
            "(code-in-DB requires a standalone interpreter)"
        )
    try:
        request = {"mode": "describe", "name": name, "code": code or ""}
        stdin_data = json.dumps(request, ensure_ascii=False).encode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise IsolatedToolError(f"could not serialize describe request: {e}") from e

    env = dict(os.environ)
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{_REPO_ROOT}{os.pathsep}{existing_path}" if existing_path else str(_REPO_ROOT)
    )

    result = run_oneshot(
        _runner_cmd(),
        stdin_data=stdin_data,
        timeout=DESCRIBE_TIMEOUT,
        env=env,
        cwd=str(_REPO_ROOT),
        cpu_seconds=DESCRIBE_CPU_SECONDS,
        address_space_bytes=DEFAULT_AS_BYTES,
    )

    if result.timed_out:
        raise IsolatedToolError("schema discovery timed out")
    if result.returncode != 0:
        reason = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise IsolatedToolError(reason.splitlines()[-1] if reason else f"exit {result.returncode}")

    stdout_text = (result.stdout or b"").decode("utf-8", "replace").strip()
    try:
        envelope = json.loads(stdout_text)
    except (json.JSONDecodeError, ValueError) as e:
        raise IsolatedToolError(f"malformed describe output: {e}") from e
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        raise IsolatedToolError(envelope.get("error") if isinstance(envelope, dict) else "unknown error")

    schemas = envelope.get("schemas")
    if not isinstance(schemas, list) or not schemas:
        raise IsolatedToolError("no tool schema returned")
    return schemas


def run_isolated_tool(
    name: str,
    code: str,
    ctx: Any,
    args: dict,
    *,
    timeout: Optional[float] = None,
    cpu_seconds: Optional[int] = None,
    address_space_bytes: Optional[int] = None,
) -> Optional[str]:
    """Execute a code-in-DB tool in an isolated subprocess; return LLM feedback.

    Returns the tool's string result (``execute(ctx, args) -> str | None``) on
    success, a short diagnostic string on any failure, or ``None`` when the tool
    legitimately returned ``None``. Never raises.
    """
    if getattr(sys, "frozen", False):
        # No standalone interpreter in a frozen build → cannot isolate. Refuse
        # rather than silently fall back to in-process exec (that would defeat
        # the security purpose of the retrofit).
        logger.error(
            "AI tool '%s': isolated execution is unavailable in a frozen build; refusing.",
            name,
        )
        return f"[tool '{name}' indisponível: execução isolada não suportada neste build]"

    try:
        request = {
            "name": name,
            "code": code or "",
            "args": _json_safe(args or {}),
            "context": _serialize_context(ctx),
        }
        stdin_data = json.dumps(request, ensure_ascii=False).encode("utf-8")
    except Exception as e:  # noqa: BLE001 — non-serializable args, etc.
        logger.warning("AI tool '%s': could not serialize request: %s", name, e)
        return f"[tool '{name}' falhou: argumentos não serializáveis ({e})]"

    # Inherit the parent env but force the repo root onto PYTHONPATH so
    # ``-m agent.tool_runner`` always resolves.
    env = dict(os.environ)
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{_REPO_ROOT}{os.pathsep}{existing_path}" if existing_path else str(_REPO_ROOT)
    )

    result: OneShotResult = run_oneshot(
        _runner_cmd(),
        stdin_data=stdin_data,
        timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
        env=env,
        cwd=str(_REPO_ROOT),
        cpu_seconds=DEFAULT_CPU_SECONDS if cpu_seconds is None else cpu_seconds,
        address_space_bytes=(
            DEFAULT_AS_BYTES if address_space_bytes is None else address_space_bytes
        ),
    )

    return _interpret_result(name, result)


def _interpret_result(name: str, result: OneShotResult) -> Optional[str]:
    """Turn a :class:`OneShotResult` into an LLM feedback string (or None)."""
    stderr_text = (result.stderr or b"").decode("utf-8", "replace").strip()

    if result.timed_out:
        logger.warning("AI tool '%s' timed out after %dms and was killed", name, result.duration_ms)
        return f"[tool '{name}' excedeu o tempo limite e foi interrompida]"

    if result.returncode != 0:
        # Runner-level failure (bad code, no execute, non-serializable result):
        # the reason is on stderr.
        reason = stderr_text or f"exit code {result.returncode}"
        logger.warning("AI tool '%s' runner failed: %s", name, reason)
        return f"[tool '{name}' falhou: {reason.splitlines()[-1] if reason else 'erro desconhecido'}]"

    stdout_text = (result.stdout or b"").decode("utf-8", "replace").strip()
    if not stdout_text:
        logger.warning("AI tool '%s' produced no output", name)
        return f"[tool '{name}' não produziu saída]"

    try:
        envelope = json.loads(stdout_text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("AI tool '%s' produced a malformed envelope: %s", name, e)
        return f"[tool '{name}' retornou saída inválida]"

    if not isinstance(envelope, dict):
        return f"[tool '{name}' retornou saída inválida]"

    if envelope.get("ok"):
        return envelope.get("result")  # str | None — both valid

    # Tool raised an exception (handled outcome, exit 0).
    err = envelope.get("error") or "erro desconhecido"
    logger.info("AI tool '%s' raised: %s", name, err)
    return f"[tool '{name}' erro: {err}]"
