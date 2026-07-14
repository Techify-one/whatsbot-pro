"""Per-visitor WebSocket hub (plano 46 · 05.3 — the "coração").

This is where the ``Channel`` contract *bends*: for every other provider
``send_text`` calls an external API; for the website widget it **delivers to the
browser** over a WebSocket dedicated to that visitor's session. There is exactly
ONE hub for the process — a module singleton shared by ``routes.py`` (which
registers/unregisters the visitor sockets on the WS endpoint) and ``channels.py``
(whose ``send_text``/``send_presence`` call :meth:`WsHub.deliver`).

Isolation is the whole point (plano 46 riscos): messages are keyed by
``session_token`` and only ever pushed to the socket(s) of THAT session — a
visitor never sees another conversation, and the operator ``/ws`` is never
touched.

Threading: ``send_text`` runs in a worker thread (the ``OutboundRouter`` is
called via ``asyncio.to_thread`` on the async hot path), while the sockets live
on the main event loop. So :meth:`deliver` schedules the actual push onto the
loop with ``run_coroutine_threadsafe`` — the same bridge pattern as
``plugins.context.broadcast``. The loop is captured lazily the first time a
socket connects (an async context), so no wiring from the core is required.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class WsHub:
    def __init__(self) -> None:
        # session_token -> set of live WebSocket objects (a visitor may have the
        # widget open in several tabs; each is a separate socket for that session).
        self._sessions: dict[str, set] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Loop capture ─────────────────────────────────────────────────
    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _ensure_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                return None
        return self._loop

    # ── Registration (called from the WS handler, on the loop) ───────
    def register(self, session_token: str, ws: Any) -> None:
        if not session_token:
            return
        self._ensure_loop()  # we're inside the async WS handler → capture the loop
        self._sessions.setdefault(session_token, set()).add(ws)
        logger.info("[website] socket registered for session %s (%d open)",
                    _short(session_token), len(self._sessions[session_token]))

    def unregister(self, session_token: str, ws: Any) -> None:
        socks = self._sessions.get(session_token)
        if not socks:
            return
        socks.discard(ws)
        if not socks:
            self._sessions.pop(session_token, None)

    def has_session(self, session_token: str) -> bool:
        return bool(self._sessions.get(session_token))

    # ── Delivery (called from send_text, possibly off-loop) ──────────
    def deliver(self, session_token: str, payload: dict) -> bool:
        """Push ``payload`` (a JSON-able dict) to every socket of ``session_token``.

        Returns True when at least one socket was targeted. Never raises — a closed
        tab simply has no socket, and the message is already persisted by the
        pipeline (re-synced on reconnect via GET config). Safe to call from any
        thread.
        """
        socks = self._sessions.get(session_token)
        if not socks:
            return False
        loop = self._ensure_loop()
        if loop is None:
            logger.debug("[website] deliver skipped (no loop) for %s", _short(session_token))
            return False
        try:
            asyncio.run_coroutine_threadsafe(
                self._push(session_token, dict(payload)), loop)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("[website] deliver failed for %s: %s", _short(session_token), e)
            return False

    async def _push(self, session_token: str, payload: dict) -> None:
        import json
        socks = list(self._sessions.get(session_token) or ())
        dead = []
        data = json.dumps(payload, ensure_ascii=False)
        for ws in socks:
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001 — a broken socket is dropped
                dead.append(ws)
        for ws in dead:
            self.unregister(session_token, ws)

    async def close_all(self) -> None:
        """Close every open socket (plugin teardown). Best-effort."""
        for token, socks in list(self._sessions.items()):
            for ws in list(socks):
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
            self._sessions.pop(token, None)


def _short(token: str) -> str:
    return (token or "")[:10]


# Process-wide singleton shared by routes.py and channels.py.
hub = WsHub()
