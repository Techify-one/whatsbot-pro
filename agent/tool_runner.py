"""Isolated one-shot runner for code-in-DB tools (retrofit P62/P67).

Runs the Python source persisted in an ``ai_tools`` row inside a SEPARATE,
short-lived subprocess instead of ``exec``-ing it in the host process (the
SECURITY DEBT P62 in :mod:`agent.ai_tool_installer`). The parent (the
``AgentHandler``'s ``_dispatch_tool``) spawns ``python -m agent.tool_runner``
through :func:`runtime.subprocess_service.run_oneshot`, which adds process-group
isolation, die-with-parent, a wall-clock timeout, and ``RLIMIT_CPU``/``RLIMIT_AS``.

Because the child is isolated, it does NOT receive the live ``ToolContext``
(handler, DB engine, LLM key, ``ContactMemory``). That is the whole point of the
retrofit: the tool can compute/transform over its ``args`` plus a *serializable
snapshot* of the contact, but cannot reach back into the host's DB/network/LLM
credentials with full privileges. A tool that genuinely needs DB/network access
should be a core or plugin tool (trusted code on disk), not a code-in-DB tool.

────────────────────────────────────────────────────────────────────────────
I/O CONTRACT
────────────────────────────────────────────────────────────────────────────
INPUT  — a single JSON object on **stdin**:
    {
      "mode":   "execute" | "describe",  # optional, default "execute"
      "name":   "<tool name>",          # required, str
      "code":   "<python source>",      # required, str — defines execute(ctx, args)
      "args":   { ... },                # tool arguments (JSON object), default {}  (execute mode)
      "context": {                      # serializable context snapshot, optional (execute mode)
          "phone":     "<contact phone>",
          "is_group":  false,
          "contact":   { ... },         # name/email/profession/company/address/tags
          "plugin_id": null
      }
    }

OUTPUT — a single JSON object on **stdout**:
  execute mode:
    {"ok": true,  "result": "<string-or-null>"}       # execute() returned (str|None)
    {"ok": false, "error": "<message>"}               # tool raised / contract broke
  describe mode (used by the installer to discover schemas WITHOUT running the
  DB-stored code in the host process):
    {"ok": true,  "schemas": [ {<openai tool schema>}, ... ]}
    {"ok": false, "error": "<message>"}               # no schema / bad contract

EXIT CODES:
    0  → a result was produced (``ok`` may be true OR false — a tool exception is
         a *handled* outcome reported as ``ok:false`` on exit 0).
    1  → the runner itself failed before/around execution (bad JSON in, code did
         not compile, no ``execute`` callable, result not JSON-serializable).
         A human-readable reason is written to **stderr**.

The runner writes ONLY the JSON envelope to stdout — anything the tool prints to
stdout is captured and discarded so it cannot corrupt the envelope (tool prints
are redirected to stderr).
"""

from __future__ import annotations

import io
import json
import sys
import types
from contextlib import redirect_stdout


# A minimal, read-only stand-in for the host ``ToolContext``. The tool code
# expects ``ctx`` as the first positional arg; we give it a namespace with the
# serializable snapshot only — no handler, no DB, no LLM client. Attribute or
# item access to anything not provided returns None rather than raising, so a
# tool written against the rich ToolContext degrades instead of crashing the
# runner (it simply won't find ``ctx.handler`` etc.).
class _IsolatedContext:
    """Read-only context shim handed to an isolated tool's ``execute(ctx, args)``."""

    __slots__ = ("phone", "is_group", "contact", "plugin_id", "_data")

    def __init__(self, data: dict):
        self._data = data or {}
        self.phone = self._data.get("phone", "")
        self.is_group = bool(self._data.get("is_group", False))
        self.contact = self._data.get("contact") or {}
        self.plugin_id = self._data.get("plugin_id")

    def __getattr__(self, name):  # only hit for attrs not in __slots__
        # Unknown attributes (handler, plugin_db, tag_registry, ...) are absent
        # in isolation. Return None so attribute reads degrade gracefully.
        return None

    def get(self, key, default=None):
        return self._data.get(key, default)


def _fail(message: str) -> int:
    """Write a runner-level failure to stderr and signal exit code 1."""
    sys.stderr.write(message.rstrip() + "\n")
    sys.stderr.flush()
    return 1


# --------------------------------------------------------------------------- #
# Contract validation (kept in lock-step with agent.ai_tool_installer)
# --------------------------------------------------------------------------- #
def _validate_schema(schema, expected_name):
    if not isinstance(schema, dict):
        raise ValueError("tool schema must be a dict")
    fn = schema.get("function")
    if not isinstance(fn, dict) or not fn.get("name"):
        raise ValueError("tool schema must be {'type':'function','function':{'name':...}}")
    fname = fn["name"]
    if expected_name is not None and fname != expected_name:
        raise ValueError(
            f"schema function name '{fname}' must equal the row name '{expected_name}'"
        )
    return fname


def _extract_schemas(module: types.ModuleType, name: str) -> list:
    """Pull validated tool schema dict(s) from the imported module.

    Mirrors ``ai_tool_installer._extract_tools`` but returns ONLY the schemas
    (the executor lives in the same subprocess and is invoked per call, not
    serialized back to the host).
    """
    schema = module.__dict__.get("SCHEMA") or module.__dict__.get("TOOL")
    execute = module.__dict__.get("execute")
    if schema is not None and callable(execute):
        _validate_schema(schema, name)
        return [schema]

    core = module.__dict__.get("CORE_TOOLS")
    if core:
        schemas = []
        for entry in core:
            if not (isinstance(entry, tuple) and len(entry) == 2 and callable(entry[1])):
                raise ValueError("CORE_TOOLS entries must be (schema, executor) tuples")
            _validate_schema(entry[0], None)
            schemas.append(entry[0])
        names = {(s.get("function") or {}).get("name") for s in schemas}
        if name not in names:
            raise ValueError(f"CORE_TOOLS must define a tool named '{name}' (matching the row)")
        return schemas

    raise ValueError(
        "module must define SCHEMA (dict) + execute(ctx, args), "
        "or CORE_TOOLS=[(schema, executor), ...]"
    )


def _resolve_executor(module: types.ModuleType, name: str):
    """Find the executor callable for ``name`` in the imported module.

    Supports the single ``execute`` form and the ``CORE_TOOLS`` multi-tool form
    (picks the executor whose schema function name matches ``name``).
    """
    execute = module.__dict__.get("execute")
    schema = module.__dict__.get("SCHEMA") or module.__dict__.get("TOOL")
    if callable(execute) and schema is not None:
        return execute

    core = module.__dict__.get("CORE_TOOLS")
    if core:
        for entry in core:
            if isinstance(entry, tuple) and len(entry) == 2 and callable(entry[1]):
                fn = (entry[0].get("function") or {}) if isinstance(entry[0], dict) else {}
                if fn.get("name") == name:
                    return entry[1]
    return execute if callable(execute) else None


def _emit(payload: dict) -> None:
    """Write the JSON result envelope to the real stdout."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def main() -> int:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        return _fail(f"tool_runner: invalid JSON request on stdin: {e}")

    if not isinstance(request, dict):
        return _fail("tool_runner: request must be a JSON object")

    mode = request.get("mode") or "execute"
    if mode not in ("execute", "describe"):
        return _fail(f"tool_runner: unknown mode {mode!r}")

    name = request.get("name") or "ai_tool"
    code = request.get("code")
    if not isinstance(code, str) or not code.strip():
        return _fail(f"tool_runner: tool '{name}' has empty/invalid code")

    # Compile + exec the tool source in a fresh module namespace. The module's
    # globals are a plain dict — the tool gets the normal builtins (json, re,
    # math, etc. are import-able), but no reference to the host process state.
    module = types.ModuleType(f"whatsbot_ai_tool_isolated_{name}")
    try:
        compiled = compile(code, f"<ai_tool:{name}>", "exec")
    except SyntaxError as e:
        return _fail(f"tool_runner: tool '{name}' failed to compile: {e}")

    # Tool prints (at import time AND at execution time) must not corrupt the
    # stdout envelope → capture them and forward to stderr.
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            exec(compiled, module.__dict__)  # noqa: S102 — this IS the isolated executor
    except Exception as e:  # noqa: BLE001
        _flush_captured(captured)
        return _fail(f"tool_runner: tool '{name}' raised at import time: {type(e).__name__}: {e}")

    # ── describe: validate + emit schema(s), without running execute() ───────
    if mode == "describe":
        try:
            schemas = _extract_schemas(module, name)
        except Exception as e:  # noqa: BLE001
            _flush_captured(captured)
            return _fail(f"tool_runner: tool '{name}' contract error: {e}")
        _flush_captured(captured)
        _emit({"ok": True, "schemas": schemas})
        return 0

    # ── execute: run the tool against args + the isolated context ────────────
    args = request.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _fail(f"tool_runner: tool '{name}' args must be a JSON object")

    ctx = _IsolatedContext(request.get("context") or {})
    execute = _resolve_executor(module, name)
    if not callable(execute):
        return _fail(f"tool_runner: tool '{name}' does not define a callable execute(ctx, args)")

    try:
        with redirect_stdout(captured):
            result = execute(ctx, args)
    except Exception as e:  # noqa: BLE001 — a tool exception is a HANDLED outcome
        # Forward any captured tool output to stderr for debugging, then report
        # the exception as a structured (ok:false) result on exit 0.
        _flush_captured(captured)
        _emit({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 0

    _flush_captured(captured)

    # The tool contract is ``execute(ctx, args) -> str | None``. Anything else
    # is coerced/rejected so the envelope round-trips cleanly.
    if result is None:
        _emit({"ok": True, "result": None})
        return 0
    if not isinstance(result, str):
        try:
            result = str(result)
        except Exception:  # noqa: BLE001
            return _fail(f"tool_runner: tool '{name}' returned a non-stringifiable value")

    _emit({"ok": True, "result": result})
    return 0


def _flush_captured(buf: io.StringIO) -> None:
    """Send anything the tool printed to stderr (never to the stdout envelope)."""
    text = buf.getvalue()
    if text:
        sys.stderr.write(text)
        sys.stderr.flush()


if __name__ == "__main__":
    sys.exit(main())
