"""Real-process tests for the isolated code-in-DB tool runner (retrofit P62/P67).

These spawn ACTUAL ``python -m agent.tool_runner`` subprocesses (not mocks) to
validate the dangerous bits: a trivial tool round-trips, a runaway tool is killed
by the wall-clock timeout, a raising tool is reported (not a parent crash), JSON
args/result round-trip, and an absurd allocation is barred by RLIMIT_AS (or the
test degrades safely where rlimit is unavailable).

Run: venv/bin/python tests/test_tool_runner.py   (Linux/macOS)
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime._proc_platform import IS_WINDOWS  # noqa: E402
from runtime.subprocess_service import run_oneshot  # noqa: E402
from agent.tool_isolation import (  # noqa: E402
    IsolatedToolError,
    describe_isolated_tool,
    run_isolated_tool,
)

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ FAIL: {label}")


# A minimal stand-in for the live ToolContext (only the serializable subset is read).
class _FakeContact:
    def __init__(self):
        self.phone = "5511988887777"
        self.is_group = False
        self.info = {"name": "Maria", "email": "m@x.com", "company": "Acme"}
        self.tags = ["lead"]


class _FakeCtx:
    def __init__(self):
        self.plugin_id = None
        self.contact = _FakeContact()


# ── 1) Trivial tool round-trips ────────────────────────────────────────────
def test_trivial():
    print("Trivial tool")
    code = (
        "SCHEMA = {'type':'function','function':{'name':'ok_tool',"
        "'description':'d','parameters':{'type':'object','properties':{}}}}\n"
        "def execute(ctx, args):\n"
        "    return 'ok'\n"
    )
    schemas = describe_isolated_tool("ok_tool", code)
    check("describe returns one schema named ok_tool",
          len(schemas) == 1 and schemas[0]["function"]["name"] == "ok_tool")
    result = run_isolated_tool("ok_tool", code, _FakeCtx(), {})
    check("one-shot collects 'ok'", result == "ok")


# ── 2) JSON args/result round-trip + context passthrough ───────────────────
def test_roundtrip():
    print("JSON round-trip + context")
    code = (
        "import json\n"
        "SCHEMA = {'type':'function','function':{'name':'rt','description':'d',"
        "'parameters':{'type':'object','properties':{}}}}\n"
        "def execute(ctx, args):\n"
        "    payload = {'echo': args, 'phone': ctx.phone, 'name': ctx.contact.get('name')}\n"
        "    return json.dumps(payload, sort_keys=True)\n"
    )
    args = {"n": 7, "items": ["a", "b"], "nested": {"k": True}}
    out = run_isolated_tool("rt", code, _FakeCtx(), args)
    parsed = json.loads(out)
    check("args round-trip through JSON intact", parsed["echo"] == args)
    check("serialized contact phone reached the tool", parsed["phone"] == "5511988887777")
    check("serialized contact info reached the tool", parsed["name"] == "Maria")


# ── 3) A raising tool is reported, parent survives ─────────────────────────
def test_tool_exception():
    print("Tool raises → reported, not crash")
    code = (
        "SCHEMA = {'type':'function','function':{'name':'boom','description':'d',"
        "'parameters':{'type':'object','properties':{}}}}\n"
        "def execute(ctx, args):\n"
        "    raise ValueError('kaboom')\n"
    )
    result = run_isolated_tool("boom", code, _FakeCtx(), {})
    check("parent did not crash and got a string back", isinstance(result, str))
    check("error feedback mentions the exception", "kaboom" in result)
    # Sanity: the parent process is obviously still alive to assert this.
    check("parent still running after tool exception", True)


# ── 4) Timeout: a spinning tool is killed and reported ─────────────────────
def test_timeout():
    print("Timeout (while True) is killed")
    code = (
        "SCHEMA = {'type':'function','function':{'name':'spin','description':'d',"
        "'parameters':{'type':'object','properties':{}}}}\n"
        "def execute(ctx, args):\n"
        "    while True:\n"
        "        pass\n"
    )
    start = time.monotonic()
    # Short timeout so the test is fast; CPU limit also guards independently.
    result = run_isolated_tool("spin", code, _FakeCtx(), {}, timeout=1.5, cpu_seconds=2)
    elapsed = time.monotonic() - start
    check("spinning tool returned (did not hang the parent)", isinstance(result, str))
    check("timeout/limit feedback returned",
          "tempo limite" in result or "interrompida" in result or "erro" in result.lower())
    check("returned within a few seconds (not hung)", elapsed < 8.0)


# ── 5) RLIMIT_AS bars an absurd allocation (or degrades safely) ────────────
def test_rlimit_memory():
    print("RLIMIT_AS bars an absurd allocation")
    try:
        import resource  # noqa: F401
        has_rlimit = True
    except ImportError:
        has_rlimit = False
    if not has_rlimit:
        print("  ~ skipped (resource module unavailable — degrades to timeout-only)")
        return
    # Try to allocate ~2 GiB while capping address space at 256 MiB.
    code = (
        "SCHEMA = {'type':'function','function':{'name':'hog','description':'d',"
        "'parameters':{'type':'object','properties':{}}}}\n"
        "def execute(ctx, args):\n"
        "    x = bytearray(2 * 1024 * 1024 * 1024)\n"
        "    return 'allocated ' + str(len(x))\n"
    )
    result = run_isolated_tool(
        "hog", code, _FakeCtx(), {},
        timeout=10.0, cpu_seconds=5, address_space_bytes=256 * 1024 * 1024,
    )
    # The allocation must NOT succeed: either the tool hits MemoryError (reported
    # as an error feedback) or the child is killed — never "allocated ...".
    check("absurd allocation was barred (no success)",
          isinstance(result, str) and "allocated" not in result)


# ── 6) Runner-level failures (bad contract) are fail-closed ────────────────
def test_bad_contract():
    print("Bad contract → fail-closed")
    # No SCHEMA / no execute.
    try:
        describe_isolated_tool("nope", "x = 1\n")
        ok = False
    except IsolatedToolError as e:
        ok = "execute" in str(e) or "schema" in str(e).lower() or "CORE_TOOLS" in str(e)
    check("describe raises IsolatedToolError on missing contract", ok)

    # Syntax error.
    try:
        describe_isolated_tool("syn", "def execute(ctx, args)\n    return 1\n")
        ok2 = False
    except IsolatedToolError:
        ok2 = True
    check("describe raises on syntax error", ok2)


# ── 7) run_oneshot primitive: timeout + clean exit ─────────────────────────
def test_oneshot_primitive():
    print("run_oneshot primitive")
    r = run_oneshot([sys.executable, "-c", "print('hi')"], timeout=5.0)
    check("clean child exits 0", r.returncode == 0 and not r.timed_out)
    check("stdout captured", r.stdout.strip() == b"hi")

    r2 = run_oneshot(
        [sys.executable, "-c", "import time\nwhile True: time.sleep(1)"],
        timeout=1.0,
    )
    check("spinning child times out", r2.timed_out is True)
    check("timed-out result has no returncode", r2.returncode is None)


def main():
    if IS_WINDOWS:
        print("Tool runner tests are POSIX-only; skipping on Windows.")
        return 0
    if getattr(sys, "frozen", False):
        print("Isolated execution requires a standalone interpreter; skipping in frozen build.")
        return 0
    test_trivial()
    test_roundtrip()
    test_tool_exception()
    test_timeout()
    test_rlimit_memory()
    test_bad_contract()
    test_oneshot_primitive()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
