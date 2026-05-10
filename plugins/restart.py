"""Schedule an in-process restart so plugin state changes take effect.

Strategy:

- In Docker (``WHATSBOT_DOCKER=1``) we rely on the container's ``restart: unless-stopped``
  policy: we exit with status 0 after a small delay, the container respawns.
- Locally we exit too; a wrapper such as ``run_dev.bat`` or PyInstaller's
  ``update.py`` is responsible for relaunching. ``uvicorn --reload`` watches a
  sentinel file we touch, so the process re-execs without exiting.

The delay gives the current HTTP response time to flush before the process dies.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_RESTART_DELAY_SECONDS = 1.5
_RESTART_PENDING = False
_LOCK = threading.Lock()


def schedule_restart(reason: str = "") -> None:
    """Touch the reload sentinel and schedule ``os._exit(0)`` shortly after.

    Idempotent: multiple concurrent calls only restart once.
    """
    global _RESTART_PENDING
    with _LOCK:
        if _RESTART_PENDING:
            logger.info("Restart already pending; ignoring extra request: %s", reason)
            return
        _RESTART_PENDING = True

    sentinel = Path(__file__).resolve().parent.parent / ".reload_sentinel"
    try:
        sentinel.touch()
    except Exception as e:
        logger.warning("Could not touch reload sentinel: %s", e)

    logger.warning("Scheduling restart in %.1fs: %s", _RESTART_DELAY_SECONDS, reason)

    def _exit_later():
        time.sleep(_RESTART_DELAY_SECONDS)
        logger.warning("Restarting now (%s)", reason)
        # Some environments need a hard exit so background tasks don't block;
        # ``os._exit`` skips finalizers but is the only reliable way out of an
        # asyncio event loop holding sockets open.
        os._exit(0)

    threading.Thread(target=_exit_later, daemon=True).start()
