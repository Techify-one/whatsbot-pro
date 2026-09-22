"""Shared state classes for the WhatsBot server."""

import asyncio
import json
import logging
import threading
import time
from collections import deque

from fastapi import WebSocket

logger = logging.getLogger(__name__)


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


def _as_conversation_id(value) -> int | None:
    """Coerce a WS payload value to a conversation id, or ``None`` (plano 168 I2).

    Accepts ``int`` and digit-only ``str`` (``"123"``) — anything else (a uuid
    hex string like the ``melhorias`` plugin's chat ids, ``None``, a ``bool``)
    is NOT a conversation id. ``bool`` is excluded explicitly because
    ``isinstance(True, int)`` is ``True`` in Python. The caller decides what a
    ``None`` result means: resolve-or-discard for a conversation event, plain
    global fan-out for anything else (a non-numeric id on a non-conversation
    event is not this router's business).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


# ── WebSocket Connection Manager ─────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    # Per-client send timeout. A broadcast fans out to every socket CONCURRENTLY
    # (asyncio.gather) so one slow/half-open client can't stall delivery to the
    # others (head-of-line blocking). A send that exceeds this is treated as a
    # dead connection and the socket is pruned — bounding worst-case latency to
    # this timeout instead of the OS TCP timeout (dezenas de segundos).
    SEND_TIMEOUT = 5.0
    CONVERSATION_EVENTS = {
        "new_message", "message_status", "message_reaction", "message_edited",
        "message_revoked", "message_deleted", "chat_presence", "operator_typing",
        "ai_typing", "messages_read", "mention_created", "conversation_upsert",
        "conversation_created", "conversation_status_changed",
        "conversation_assigned", "conversation_archived", "conversation_pinned",
        "conversation_ai_toggled", "conversation_updated", "conversation_deleted",
        "conversation_labels_changed", "agent_transfer_alert", "human_transfer_alert",
    }

    # Plano 168 I1 — chave que carrega o id de conversa POR EVENTO, checada
    # ANTES da leitura genérica de ``conversation_id``. Mapa explícito e
    # fechado: nunca ler ``id`` de forma genérica — outros eventos do bus usam
    # essa mesma chave para outra coisa (``plugin_melhorias_changed {"id": sid}``,
    # ``agendamento_retorno_changed``, o ``message.reaction`` do bus).
    UPSERT_ID_KEYS = {"conversation_upsert": "id"}

    # Log de descarte com limite de taxa (I4): 1 linha/60s por nome de evento —
    # presença/recibo são alto volume.
    _DISCARD_LOG_INTERVAL = 60.0

    def __init__(self):
        self.active: list[WebSocket] = []
        self._user_ids: dict[WebSocket, int | None] = {}
        self._discard_log_ts: dict[str, float] = {}

    async def connect(self, websocket: WebSocket, user_id: int | None = None):
        await websocket.accept()
        self.active.append(websocket)
        self._user_ids[websocket] = user_id

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active:
            self.active.remove(websocket)
        self._user_ids.pop(websocket, None)

    async def broadcast(self, event: str, data: dict):
        """Route ``event`` to its conversation's authorized audience, or fan it
        out globally when it carries no conversation id at all.

        Never raises (plano 168 F1 / D4): a webhook that ``await``s this call
        must not lose the inbound message it is about to enqueue just because a
        resolver hit a bad id or the DB blipped (R1). Every discard is logged,
        rate-limited, instead of failing silently forever.
        """
        try:
            return await self._route_broadcast(event, data)
        except Exception:
            logger.exception("ws: broadcast de %s falhou", event)
            return None

    async def _route_broadcast(self, event: str, data: dict):
        conversation_id = None
        if isinstance(data, dict):
            id_key = self.UPSERT_ID_KEYS.get(event)
            if id_key is not None:
                conversation_id = _as_conversation_id(data.get(id_key))
            if conversation_id is None:
                conversation_id = _as_conversation_id(data.get("conversation_id"))
            if conversation_id is None:
                nested = data.get("message")
                if isinstance(nested, dict):
                    conversation_id = _as_conversation_id(nested.get("conversation_id"))
        if conversation_id is None and event in self.CONVERSATION_EVENTS:
            conversation_id = await self._resolve_conversation_id(data)
        if conversation_id is not None:
            return await self.broadcast_conversation(event, data, conversation_id)
        if event in self.CONVERSATION_EVENTS:
            # Fail closed: a conversation payload without provable ownership is
            # never fanned out globally.
            self._log_discarded(event, data)
            return
        return await self._broadcast_targets(event, data, list(self.active))

    def _log_discarded(self, event: str, data) -> None:
        now = time.time()
        last = self._discard_log_ts.get(event, 0.0)
        if now - last < self._DISCARD_LOG_INTERVAL:
            return
        self._discard_log_ts[event] = now
        keys = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
        logger.warning(
            "ws: %s descartado — sem conversa resolvível (chaves=%s)", event, keys)

    async def _resolve_conversation_id(self, data: dict) -> int | None:
        from db.repositories import contact_repo, conversation_repo, inbox_repo, message_repo

        if not isinstance(data, dict):
            return None
        db_id = data.get("db_id")
        row = None
        if db_id:
            row = await asyncio.to_thread(message_repo.get_by_db_id, db_id)
        if row is None:
            msg_id = data.get("msg_id")
            if not msg_id and isinstance(data.get("msg_ids"), list) and data["msg_ids"]:
                msg_id = data["msg_ids"][0]
            if msg_id:
                row = await asyncio.to_thread(message_repo.get_by_msg_id, msg_id)
                # Plano 168 I6 / R2: ``msg_id`` is only unique WITHIN a channel (a
                # small provider-issued int, e.g. Telegram, can collide across
                # channels). When the payload also names the channel, require the
                # match to actually belong to it — otherwise a status/reaction/
                # revoke lands on the wrong conversation's audience.
                channel_id = data.get("channel_id")
                if row and row.get("conversation_id") and channel_id:
                    conv = await asyncio.to_thread(
                        conversation_repo.get, row["conversation_id"])
                    inbox = await asyncio.to_thread(
                        inbox_repo.get_by_channel, str(channel_id))
                    if not conv or not inbox or conv.get("inbox_id") != inbox.get("id"):
                        row = None
        if row and row.get("conversation_id"):
            return int(row["conversation_id"])
        phone = data.get("phone")
        if not phone:
            return None
        contact = await asyncio.to_thread(contact_repo.get_by_phone, phone)
        if not contact:
            return None
        channel_id = data.get("channel_id")
        if channel_id:
            inbox = await asyncio.to_thread(inbox_repo.get_by_channel, str(channel_id))
            if inbox:
                conv = await asyncio.to_thread(
                    conversation_repo.get_latest_for_contact_inbox,
                    contact["id"], inbox["id"])
                return int(conv["id"]) if conv else None
        conversations_for_contact = await asyncio.to_thread(
            conversation_repo.list_for_contact, contact["id"])
        return (int(conversations_for_contact[0]["id"])
                if len(conversations_for_contact) == 1 else None)

    async def broadcast_conversation(self, event: str, data: dict,
                                     conversation_id: int):
        """Send conversation data only to its current authorized audience."""
        from db.repositories import conversation_repo
        from server.authz import ConversationAccessScope

        conversation = await asyncio.to_thread(conversation_repo.get, conversation_id)
        # Deletion events are projected after the row is gone, but their payload
        # still carries the authorization-relevant snapshot.
        if not conversation:
            conversation = data if isinstance(data, dict) else None
        if not conversation or conversation.get("inbox_id") is None:
            return
        scopes: dict[int | None, ConversationAccessScope] = {}
        targets = []
        for websocket in list(self.active):
            user_id = self._user_ids.get(websocket)
            if user_id not in scopes:
                scopes[user_id] = await asyncio.to_thread(
                    ConversationAccessScope.for_user, user_id)
            if scopes[user_id].allows(conversation, "direct"):
                targets.append(websocket)
        return await self._broadcast_targets(event, data, targets)

    async def _broadcast_targets(self, event: str, data: dict,
                                 targets: list[WebSocket]):
        message = json.dumps({"event": event, "data": data})
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
        # Plano 96 I7 — o ATENDENTE digitando também segura a IA (D3: segurar, não
        # cancelar — ele pode desistir do texto). Mesma chave e formato do dict do
        # cliente acima, escrito pela rota de presença do painel. Prazo de
        # obsolescência menor (15s): o painel reemite ``start`` a cada 10s, então a
        # ausência de heartbeat é sinal confiável de aba fechada.
        self.operator_typing_state: dict[tuple, dict] = {}
        # Active orchestrator task per (channel_id, phone) (typing-aware flow).
        self.processing_tasks: dict[tuple, asyncio.Task] = {}
        # Monotonic abort generation per (channel_id, phone) (plano 96).  A panel
        # takeover cannot always ``task.cancel()`` safely: once the orchestrator has
        # popped its batch, cancellation could lose an inbound message that has not
        # been persisted yet.  In that phase ``abort_ai_cycle`` increments this
        # generation instead.  The running cycle carries the generation it started
        # with and every wire-send guard rejects it after a mismatch.
        self.ai_abort_epochs: dict[tuple, int] = {}
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
        # Rate-limit das CHAVES DE API — bucket PRÓPRIO, por chave (plano
        # "Sistema de API com chave por usuário" §4.3). Nunca reaproveitar o do
        # login (uma integração legítima esgotaria o limite de um IP inteiro) e
        # nunca chavear por ``audit_ip`` (autodeclarado ⇒ forjável).
        self.api_key_calls: dict[int, deque[float]] = {}

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
    def operator_typing_state(self) -> dict:
        return self.messaging.operator_typing_state

    @property
    def processing_tasks(self) -> dict:
        return self.messaging.processing_tasks

    @property
    def ai_abort_epochs(self) -> dict:
        return self.messaging.ai_abort_epochs

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
