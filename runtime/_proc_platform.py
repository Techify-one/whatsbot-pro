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
