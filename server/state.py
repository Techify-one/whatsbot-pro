"""Shared state classes for the WhatsBot server."""

import asyncio
import json
import logging
import threading
import time
from collections import deque

from fastapi import WebSocket


# ── In-memory log capture ────────────────────────────────────────────────

class MemoryLogHandler(logging.Handler):
    """Stores recent log records in a bounded deque for the debug UI."""

    IGNORED_LOGGERS = {"uvicorn.access", "uvicorn.error", "watchfiles.main", "httpx", "gowa.manager"}

    def __init__(self, max_entries: int = 500):
        super().__init__()
        self.records: deque[dict] = deque(maxlen=max_entries)

    def emit(self, record: logging.LogRecord):
        try:
            if record.name in self.IGNORED_LOGGERS:
                return
            self.records.append({
                "ts": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
            })
        except Exception:
            pass

    def get_logs(self, limit: int = 100) -> list[dict]:
        entries = list(self.records)
        return entries[-limit:]

    def clear(self):
        self.records.clear()


# ── WebSocket Connection Manager ─────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    # Per-client send timeout. A broadcast fans out to every socket CONCURRENTLY
    # (asyncio.gather) so one slow/half-open client can't stall delivery to the
    # others (head-of-line blocking). A send that exceeds this is treated as a
    # dead connection and the socket is pruned — bounding worst-case latency to
    # this timeout instead of the OS TCP timeout (dezenas de segundos).
    SEND_TIMEOUT = 5.0

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, event: str, data: dict):
        message = json.dumps({"event": event, "data": data})
        targets = list(self.active)
        if not targets:
            return

        async def _send(ws: WebSocket):
            # Returns the ws to prune on failure/timeout, else None. Never raises.
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=self.SEND_TIMEOUT)
                return None
            except Exception:
                return ws

        # Concurrent fan-out: a stuck client is bounded by SEND_TIMEOUT and does
        # not delay the others. gather never raises (each _send swallows).
        dead = await asyncio.gather(*(_send(ws) for ws in targets))
        for ws in dead:
            if ws is not None:
                self.disconnect(ws)
                # Plano 33 F3: a timed-out/errored send means this socket is
                # half-open — disconnect() only drops it from the fan-out list, so
                # the CLIENT still believes it is connected (no close frame reached
                # it) and never reconnects. Actively close it here so the client's
                # onclose fires and it reconnects (→ F2 thread resync). Best-effort
                # with a short timeout: a stuck close must not hold up the fan-out.
                # NOTE: closing lives ONLY in this prune loop, NOT in disconnect()
                # (which also runs on a clean WebSocketDisconnect, where an extra
                # close is redundant / can raise).
                try:
                    await asyncio.wait_for(ws.close(), timeout=1.0)
                except Exception:
                    pass


# ── Messaging State ─────────────────────────────────────────────────────────

class MessagingState:
    """Per-conversation batch / orchestrator / echo state (Plano 23 · Fase B3).

    Encapsulates the loose dicts the inbound→batch→reply pipeline mutates, all of
    them keyed by ``(channel_id, phone)`` (or ``channel_id`` / wire-text for the
    two cross-cutting ones). The semantics are IDENTICAL to the bare dicts that
    lived on :class:`AppState` — this is a Branch-by-Abstraction grouping, not a
    behavior change. ``AppState`` still exposes each dict as a direct attribute
    (delegating here) so existing call sites — and the characterization suite,
    which reads ``state.processing_tasks.get((channel_id, phone))`` — keep working
    byte-for-byte.
    """

    def __init__(self):
        # Idempotency: set of "<channel_id>:<external_msg_id>" already ingested.
        self.processed_messages: set[str] = set()
        # Message batching — accumulate messages per (channel_id, phone) before
        # responding. Each item: {"text": str, "image_path": str|None,
        # "audio_path": str|None, ...}.
        self.pending_messages: dict[tuple, list[dict]] = {}
        # Typing-aware orchestrator state, keyed by (channel_id, phone):
        # {"active": bool, "media": "text"|"audio", "last_ts": float}.
        self.typing_state: dict[tuple, dict] = {}
        # Active orchestrator task per (channel_id, phone) (typing-aware flow).
        self.processing_tasks: dict[tuple, asyncio.Task] = {}
        # Per-channel AI serialization lock (plano 21 — modo sequencial): quando o
        # canal tem ``ai_sequential`` ligado, a IA processa um contato por vez nesse
        # canal (evita bloqueios da Meta por enviar a vários clientes em paralelo).
        # Keyed by channel_id; criado sob demanda dentro do loop async.
        self.channel_ai_locks: dict[str, asyncio.Lock] = {}
        # True while a reply is mid-flight — webhook must NOT cancel during this phase.
        self.sending: dict[tuple, bool] = {}
        # True while an orchestrator is MID-CYCLE — i.e. it has already POPPED its
        # batch from pending_messages and is running the LLM/send cycle (plano 33
        # F6). Broader than ``sending`` (which only covers the final SEND phase):
        # a message arriving in the pop→persist window used to cancel the task and
        # DISCARD the already-popped items (message lost). While this flag is set
        # the webhook must NOT cancel — it leaves the new message in pending and the
        # running orchestrator's tail spawns a follow-up cycle for it.
        self.processing: dict[tuple, bool] = {}
        # Track recently sent replies to filter webhook echo-backs.
        # "<channel_id>:<phone>:<wire_text[:120]>" -> timestamp.
        self.recently_sent: dict[str, float] = {}


# ── App State ─────────────────────────────────────────────────────────────

class AppState:
    """Shared mutable state for background tasks."""

    def __init__(self):
        self.msg_count: int = 0
        self.connected: bool = False
        self.auto_reply_running: bool = False
        self.stop_event: threading.Event = threading.Event()
        # Runtime task supervisor + subprocess service (plano 09); set in lifespan.
        self.task_supervisor: object | None = None
        self.subprocess_service: object | None = None
        # Per-conversation messaging pipeline state (Plano 23 · Fase B3). The
        # batch/lock/echo dicts below are owned by ``self.messaging`` and exposed
        # as direct attributes for backward compatibility (existing call sites and
        # the characterization suite read them straight off ``state``).
        self.messaging = MessagingState()
        self.notification: str = "Iniciando..."
        # QR cache — avoid regenerating on every request
        self.qr_data: bytes | None = None
        self.qr_fetched_at: float = 0.0
        self.qr_version: int = 0  # bumped when QR changes
        # Cache phone -> (conversation_id|None, expires_at) for the GOWA presence
        # broadcast, so the "digitando" indicator can target the exact conversation
        # without a DB hit on every (frequent) chat_presence event.
        self.presence_conv_cache: dict[str, tuple[int | None, float]] = {}
        # Bot's own identity for @mention detection in groups
        self.bot_phone: str = ""
        self.bot_name: str = ""
        # First-run setup wizard — Techify API key provisioning
        self.setup_key_number: str = ""  # connected number the key was requested for
        self.setup_key_requested_at: float = 0.0
        # Last 50 raw webhook payloads for debugging
        self.webhook_payloads: deque[dict] = deque(maxlen=50)
        # Login attempts per IP for brute-force protection
        self.login_attempts: dict[str, deque[float]] = {}

    # ── MessagingState delegation (Plano 23 · Fase B3) ──────────────────────
    #
    # The per-conversation batch/lock/echo dicts physically live on
    # ``self.messaging`` now, but every existing reader/writer uses ``state.<dict>``
    # — and so does the characterization suite. These properties forward to the
    # single owning instance so the identity (and therefore mutation semantics) is
    # preserved: ``state.processing_tasks`` IS ``state.messaging.processing_tasks``.

    @property
    def processed_messages(self) -> set:
        return self.messaging.processed_messages

    @property
    def pending_messages(self) -> dict:
        return self.messaging.pending_messages

    @property
    def typing_state(self) -> dict:
        return self.messaging.typing_state

    @property
    def processing_tasks(self) -> dict:
        return self.messaging.processing_tasks

    @property
    def channel_ai_locks(self) -> dict:
        return self.messaging.channel_ai_locks

    @property
    def sending(self) -> dict:
        return self.messaging.sending

    @property
    def processing(self) -> dict:
        return self.messaging.processing

    @property
    def recently_sent(self) -> dict:
        return self.messaging.recently_sent
