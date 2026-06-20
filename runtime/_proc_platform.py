"""Platform-specific process control for the subprocess service (plano 09 Fase 4).

Reference target is Linux/Docker (P29); macOS is POSIX-equivalent minus
``PR_SET_PDEATHSIG``. Windows job-objects are DEFERRED — on Windows these
helpers degrade to no-ops / plain ``kill`` and the service relies on
stale-kill + explicit stop.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# prctl(PR_SET_PDEATHSIG, sig): deliver `sig` to this process when its parent dies.
_PR_SET_PDEATHSIG = 1


def pdeathsig_preexec():
    """Return a ``preexec_fn`` that makes the child die with the parent, or None.

    Linux only (via ``prctl`` in libc). On macOS/Windows there is no clean
    equivalent, so we return None and rely on stale-kill + explicit stop.

    NOTE: ``preexec_fn`` runs in the child between fork and exec; keep it tiny
    and signal-safe. There is an inherent race — if the parent dies before
    ``prctl`` runs, the child won't get the signal; stale-kill on next boot
    covers that gap.
    """
    if not IS_LINUX:
        return None

    def _set_pdeathsig():  # pragma: no cover — runs in the forked child
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
        except Exception:
            pass

    return _set_pdeathsig


def rlimit_preexec(cpu_seconds: int | None = None, address_space_bytes: int | None = None):
    """Return a ``preexec_fn`` that applies POSIX resource limits, or None.

    Used by the one-shot tool runner (retrofit P62/P67) to bound an isolated
    code-in-DB subprocess: ``RLIMIT_CPU`` caps CPU time (the kernel sends
    ``SIGXCPU`` then ``SIGKILL``) and ``RLIMIT_AS`` caps the virtual address
    space (an absurd allocation then fails with ``MemoryError`` instead of
    OOM-killing the host).

    POSIX only — the ``resource`` module is Unix-only. On Windows we return None
    and degrade safely; the wall-clock timeout in the one-shot runner is the
    remaining guard there. Each limit is best-effort: if the platform rejects a
    given ``setrlimit`` we swallow it rather than abort the spawn.

    NOTE: the returned closure runs in the forked child between fork and exec —
    keep it tiny and signal-safe (no logging, no allocation beyond ``resource``).
    """
    if IS_WINDOWS:
        return None
    try:
        import resource  # noqa: F401 — probe availability before building the closure
    except ImportError:
        return None
    if cpu_seconds is None and address_space_bytes is None:
        return None

    def _apply_limits():  # pragma: no cover — runs in the forked child
        import resource as _res
        if cpu_seconds is not None and cpu_seconds > 0:
            try:
                # Soft = the requested cap; hard = soft + 1s grace so a SIGXCPU
                # handler could run before the hard SIGKILL.
                _res.setrlimit(_res.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
            except (ValueError, OSError):
                pass
        if address_space_bytes is not None and address_space_bytes > 0:
            try:
                _res.setrlimit(_res.RLIMIT_AS, (address_space_bytes, address_space_bytes))
            except (ValueError, OSError):
                pass

    return _apply_limits


def combine_preexec(*fns):
    """Chain several ``preexec_fn`` closures into one, skipping ``None``.

    ``subprocess.Popen`` accepts a single ``preexec_fn``; the one-shot runner
    needs both die-with-parent (``pdeathsig_preexec``) and resource limits
    (``rlimit_preexec``). Returns None if every input is None.
    """
    active = [fn for fn in fns if fn is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def _chained():  # pragma: no cover — runs in the forked child
        for fn in active:
            try:
                fn()
            except Exception:
                pass

    return _chained


def kill_process_group(pid: int, sig: int) -> None:
    """Send ``sig`` to the whole process group of ``pid`` (POSIX), else to the pid.

    Children spawned with ``start_new_session=True`` are their own session/group
    leader, so ``getpgid(pid) == pid`` and killing the group kills only that tree.
    """
    if IS_WINDOWS:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            pass
        return
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, sig)
    except (ProcessLookupError, OSError):
        # Fall back to the bare pid if the group is already gone.
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            pass


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def process_cmdline(pid: int) -> str:
    """Best-effort command line of ``pid`` (Linux ``/proc``), else ''.

    Used to verify a PID-file entry still points at OUR process before a
    stale-kill — the defense against killing a recycled PID.
    """
    if not IS_LINUX:
        return ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except (FileNotFoundError, ProcessLookupError, OSError):
        return ""
