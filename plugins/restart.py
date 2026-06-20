"""Schedule an in-process restart so plugin state changes take effect.

Strategy:

- In Docker (``WHATSBOT_DOCKER=1``) we rely on the container's ``restart: unless-stopped``
  policy: we exit with status 0 after a small delay, the container respawns.
- Locally we touch a ``.py`` trigger file inside a watched dir so
  ``uvicorn --reload`` restarts the worker cleanly. The exit below is a
  belt-and-suspenders fallback for hosts where the file watcher misses the
  event (Windows network drives, etc).
- PyInstaller EXE: ``update.py`` is the supervisor and relaunches on exit.

The trigger lives at ``server/_reload_trigger.py`` because:
- ``server/`` is always passed via ``--reload-dir`` in dev;
- uvicorn's default include pattern is ``*.py``, so a ``.py`` extension is
  required for watchfiles to fire on the change;
- the leading underscore makes the intent explicit and keeps it out of any
  package-discovery scans.

The delay gives the current HTTP response time to flush before the process dies.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_RESTART_DELAY_SECONDS = 1.5
# Max time we wait for the pre-exit hook (e.g. plugin teardown) before hard-exit (P31).
_PRE_EXIT_TIMEOUT_SECONDS = 10.0
_RESTART_PENDING = False
_LOCK = threading.Lock()

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Watched by uvicorn --reload (server/ is in --reload-dir, .py matches default include).
_RELOAD_TRIGGER = _REPO_ROOT / "server" / "_reload_trigger.py"


def schedule_restart(
    reason: str = "",
    on_before_exit: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    """Touch the reload trigger and schedule ``os._exit(0)`` shortly after.

    Idempotent: multiple concurrent calls only restart once.

    ``on_before_exit`` (plano 09 Fase 2): an async callable run on the event loop
    and awaited (with a fixed timeout) BEFORE the hard exit — so a plugin's
    ``teardown`` runs instead of being skipped by ``os._exit``. Guarded against
    a stopped/unresponsive loop: on timeout or error we fall through to exit.
    """
    global _RESTART_PENDING
    with _LOCK:
        if _RESTART_PENDING:
            logger.info("Restart already pending; ignoring extra request: %s", reason)
            return
        _RESTART_PENDING = True

    try:
        # First touch creates the file; subsequent touches bump mtime, which
        # is what watchfiles uses to detect changes.
        if not _RELOAD_TRIGGER.exists():
            _RELOAD_TRIGGER.write_text(
                "# Touched by plugins.restart.schedule_restart() to trigger uvicorn --reload.\n",
                encoding="utf-8",
            )
        else:
            _RELOAD_TRIGGER.touch()
    except Exception as e:
        logger.warning("Could not touch reload trigger: %s", e)

    logger.warning("Scheduling restart in %.1fs: %s", _RESTART_DELAY_SECONDS, reason)

    def _exit_later():
        time.sleep(_RESTART_DELAY_SECONDS)
        if on_before_exit is not None:
            _run_pre_exit_hook(on_before_exit, reason)
        logger.warning("Restarting now (%s)", reason)
        # Some environments need a hard exit so background tasks don't block;
        # ``os._exit`` skips finalizers but is the only reliable way out of an
        # asyncio event loop holding sockets open. In dev with --reload, the
        # uvicorn parent has typically already started replacing this worker
        # because of the trigger touch above; this just ensures the old one
        # is gone if the watcher missed it.
        os._exit(0)

    threading.Thread(target=_exit_later, daemon=True).start()


def _run_pre_exit_hook(
    on_before_exit: Callable[[], Awaitable[None]], reason: str
) -> None:
    """Run an async pre-exit hook on the live event loop, awaited with timeout.

    The loop is the one the bus/context already hold. If it is gone or not
    running, or the hook times out, we log and fall through to ``os._exit``.
    """
    try:
        from plugins import context as _ctx
        loop = getattr(_ctx, "_loop", None)
        if loop is None or not loop.is_running():
            logger.warning("Pre-exit hook skipped (%s): event loop unavailable", reason)
            return
        logger.info("Running pre-exit hook before restart (%s)", reason)
        fut = asyncio.run_coroutine_threadsafe(on_before_exit(), loop)
        fut.result(timeout=_PRE_EXIT_TIMEOUT_SECONDS)
    except Exception as e:  # timeout, loop stopping, hook error — never block exit
        logger.warning("Pre-exit hook failed/timeout (%s): %s", reason, e)
