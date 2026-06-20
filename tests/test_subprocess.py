"""Real-process tests for the managed subprocess service (plano 09 Fase 4).

These spawn ACTUAL OS processes (not mocks) to validate the dangerous bits the
endpoint suite can't (GOWA is mocked there): start/readiness/stop, group-kill,
stale-kill with a signature guard, and die-with-parent (Linux PR_SET_PDEATHSIG).

Run: venv/bin/python tests/test_subprocess.py   (Linux/macOS)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime._proc_platform import IS_LINUX, IS_WINDOWS, is_pid_alive
from runtime.subprocess_service import ManagedProcess, SubprocessSpec

_passed = 0
_failed = 0
_tmp = Path("/tmp/_wb_subproc_test")
_tmp.mkdir(exist_ok=True)


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ FAIL: {label}")


def _wait_dead(pid, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.05)
    return not is_pid_alive(pid)


def test_start_stop():
    print("Start / readiness / stop")
    ready = {"called": 0}

    def probe():
        ready["called"] += 1
        return True

    mp = ManagedProcess(SubprocessSpec(
        name="sleeper", cmd=["sleep", "60"], signature="sleep",
        readiness=probe, readiness_timeout=3.0,
        pid_file=_tmp / "sleeper.pid",
    ))
    mp.start()
    pid = mp._process.pid
    check("process is running after start", mp.is_running())
    check("readiness probe was called", ready["called"] >= 1)
    check("pid file written", (_tmp / "sleeper.pid").exists())
    mp.stop()
    check("process dead after stop", _wait_dead(pid))
    check("pid file removed after stop", not (_tmp / "sleeper.pid").exists())


def test_stale_kill_matching():
    print("Stale-kill (matching signature)")
    # Spawn a leftover process directly and write a matching PID-file.
    leftover = subprocess.Popen(["sleep", "120"], start_new_session=True)
    pidf = _tmp / "stale.pid"
    pidf.write_text(f"{leftover.pid}\nsleep\n", encoding="utf-8")
    check("leftover alive before stale-kill", is_pid_alive(leftover.pid))

    # A new ManagedProcess with the same pid_file + signature must kill it on start.
    mp = ManagedProcess(SubprocessSpec(
        name="stale", cmd=["sleep", "120"], signature="sleep",
        readiness=lambda: True, pid_file=pidf,
    ))
    mp.start()
    # The leftover is OUR child, so after the service kills it it lingers as a
    # zombie until we reap it (real stale GOWA is reaped by init — no zombie).
    # wait() reaps it; a non-None return code means it was terminated.
    try:
        rc = leftover.wait(timeout=5)
        killed = rc is not None
    except subprocess.TimeoutExpired:
        killed = False
    check("leftover killed by stale-kill", killed)
    new_pid = mp._process.pid
    mp.stop()
    check("new process also stopped", _wait_dead(new_pid))


def test_stale_kill_guard():
    print("Stale-kill guard (non-matching cmdline → NOT killed)")
    if not IS_LINUX:
        print("  ~ skipped (cmdline check is Linux-only)")
        return
    # A live process whose cmdline does NOT contain the expected signature.
    other = subprocess.Popen(["sleep", "120"], start_new_session=True)
    pidf = _tmp / "guard.pid"
    pidf.write_text(f"{other.pid}\nthis_signature_does_not_match\n", encoding="utf-8")

    mp = ManagedProcess(SubprocessSpec(
        name="guard", cmd=["sleep", "120"], signature="this_signature_does_not_match",
        readiness=lambda: True, pid_file=pidf,
    ))
    mp.start()
    # The guard must have SPARED the recycled PID (cmdline is "sleep 120", not the sig).
    check("non-matching PID was NOT killed (recycled-PID defense)", is_pid_alive(other.pid))
    mp.stop()
    other.terminate()
    try:
        other.wait(timeout=2)
    except Exception:
        os.kill(other.pid, 9)


def test_die_with_parent():
    print("Die-with-parent (PR_SET_PDEATHSIG)")
    if not IS_LINUX:
        print("  ~ skipped (PR_SET_PDEATHSIG is Linux-only)")
        return
    root = Path(__file__).resolve().parent.parent
    # A parent python process that starts a managed `sleep` child, prints the
    # child pid, then idles. When we SIGKILL the parent, the child must die too.
    parent_code = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from runtime.subprocess_service import ManagedProcess, SubprocessSpec\n"
        "import tempfile, os\n"
        "pf = os.path.join(tempfile.gettempdir(), '_wb_dwp_child.pid')\n"
        "mp = ManagedProcess(SubprocessSpec(name='dwp', cmd=['sleep','120'], "
        "signature='sleep', readiness=lambda: True, pid_file=__import__('pathlib').Path(pf)))\n"
        "mp.start()\n"
        "print(mp._process.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE)
    child_pid = int(parent.stdout.readline().decode().strip())
    check("managed child started under parent", is_pid_alive(child_pid))
    # Hard-kill the parent (simulates the toggle's os._exit / a crash).
    parent.kill()
    parent.wait(timeout=3)
    check("child dies with parent (PR_SET_PDEATHSIG)", _wait_dead(child_pid, timeout=6.0))
    if is_pid_alive(child_pid):
        try:
            os.kill(child_pid, 9)
        except Exception:
            pass


def main():
    if IS_WINDOWS:
        print("Subprocess service real-process tests are POSIX-only; skipping on Windows.")
        return 0
    test_start_stop()
    test_stale_kill_matching()
    test_stale_kill_guard()
    test_die_with_parent()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
