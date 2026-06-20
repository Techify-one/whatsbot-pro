"""Focused tests for the runtime foundation (plano 09 Fases 1-3).

Standalone (no server needed): exercises the task supervisor's classified
restart + backoff and the plugin lifecycle setup/teardown + disposables, which
the endpoint suite (mocked GOWA) does not stress.

Run: venv/bin/python tests/test_runtime.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


# ── Supervisor ────────────────────────────────────────────────────────────
async def test_supervisor():
    from runtime.supervisor import TaskSupervisor, TaskSpec, RestartPolicy

    print("Supervisor")

    # 1) TEMPORARY task that completes normally → state completed, runs once.
    runs = {"n": 0}

    async def run_once():
        runs["n"] += 1

    sup = TaskSupervisor()
    sup.register(TaskSpec("once", run_once, policy=RestartPolicy.TEMPORARY))
    await sup.start_all()
    await asyncio.sleep(0.05)
    st = {s["name"]: s for s in sup.status()}
    check("TEMPORARY runs exactly once", runs["n"] == 1)
    check("TEMPORARY ends 'completed'", st["once"]["state"] == "completed")

    # 2) PERMANENT task that always raises → restarts with backoff, then crashes
    #    after exceeding max_restarts/window, and emits task.crashed.
    crashes = {"n": 0}

    async def always_raise():
        crashes["n"] += 1
        raise RuntimeError("boom")

    crashed_events = []
    import plugins.events as ev
    ev.KNOWN_EVENTS.add("task.crashed")
    ev._handlers.setdefault("task.crashed", [])
    ev._handlers["task.crashed"].append(("_test", lambda ctx, p: crashed_events.append(p)))
    ev.set_runtime(asyncio.get_running_loop(), None)

    sup2 = TaskSupervisor()
    sup2.register(TaskSpec("crasher", always_raise, policy=RestartPolicy.PERMANENT,
                           max_restarts=2, window_sec=60.0, backoff_base=1.0, backoff_cap=0.02))
    await sup2.start_all()
    # backoff_base=1.0 → sleeps ~0.01-0.02s between restarts; 3 attempts then crash.
    await asyncio.sleep(0.5)
    st2 = {s["name"]: s for s in sup2.status()}
    check("PERMANENT crasher restarted multiple times", crashes["n"] >= 3)
    check("PERMANENT crasher ends 'crashed'", st2["crasher"]["state"] == "crashed")
    check("crasher records last_error", "boom" in st2["crasher"]["last_error"])
    await asyncio.sleep(0.05)
    check("task.crashed emitted on bus", len(crashed_events) >= 1)

    # 3) stop_all cancels + awaits a long-running task.
    stopped = {"cancelled": False}

    async def forever():
        try:
            while True:
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            stopped["cancelled"] = True
            raise

    sup3 = TaskSupervisor()
    sup3.register(TaskSpec("forever", forever, policy=RestartPolicy.PERMANENT))
    await sup3.start_all()
    await asyncio.sleep(0.05)
    await asyncio.wait_for(sup3.stop_all(), timeout=2.0)
    check("stop_all cancels the running task", stopped["cancelled"] is True)
    check("stopped task state == 'stopped'",
          {s["name"]: s for s in sup3.status()}["forever"]["state"] == "stopped")


# ── Lifecycle ─────────────────────────────────────────────────────────────
class _FakeLoaded:
    def __init__(self, pid, setup_fn=None, teardown_fn=None):
        self.id = pid
        self.setup_fn = setup_fn
        self.teardown_fn = teardown_fn


async def test_lifecycle():
    from plugins.lifecycle import PluginLifecycleManager

    print("Lifecycle")
    loop = asyncio.get_running_loop()
    order = []

    def setup(ctx):
        ctx.on_unload(lambda: order.append("unload_a"))
        ctx.on_unload(lambda: order.append("unload_b"))
        order.append("setup")

    def teardown(ctx):
        order.append("teardown")

    mgr = PluginLifecycleManager()
    err = await mgr.run_setup(_FakeLoaded("p1", setup, teardown), None, loop)
    check("setup runs without error", err is None and order == ["setup"])
    await mgr.run_teardown("p1")
    # teardown first, then disposables in reverse (b before a).
    check("teardown + disposables run in reverse (LIFO)",
          order == ["setup", "teardown", "unload_b", "unload_a"])

    # Failing setup → error returned, disposables registered before failure still run.
    cleaned = []

    def bad_setup(ctx):
        ctx.on_unload(lambda: cleaned.append("cleaned"))
        raise ValueError("nope")

    mgr2 = PluginLifecycleManager()
    err2 = await mgr2.run_setup(_FakeLoaded("p2", bad_setup), None, loop)
    check("failing setup returns error string", isinstance(err2, str) and "nope" in err2)
    check("cleanups registered before failure still ran", cleaned == ["cleaned"])

    # async setup + async teardown
    aorder = []

    async def asetup(ctx):
        aorder.append("asetup")
        ctx.on_unload(lambda: aorder.append("acleanup"))

    async def ateardown(ctx):
        aorder.append("ateardown")

    mgr3 = PluginLifecycleManager()
    await mgr3.run_setup(_FakeLoaded("p3", asetup, ateardown), None, loop)
    await mgr3.run_teardown("p3")
    check("async setup/teardown + cleanup order",
          aorder == ["asetup", "ateardown", "acleanup"])


async def main():
    await test_supervisor()
    await test_lifecycle()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
