"""WhatsBot — FastAPI backend with REST API, WebSocket and background tasks."""

import asyncio
import json
import logging
import random
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

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


_memory_log_handler = MemoryLogHandler()
_memory_log_handler.setLevel(logging.DEBUG)
_root = logging.getLogger()
_root.addHandler(_memory_log_handler)
# Ensure root logger level allows INFO+ through to handlers
if _root.level == logging.NOTSET or _root.level > logging.INFO:
    _root.setLevel(logging.INFO)


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_web_dir() -> Path:
    """Locate the web/ directory, handling both dev and PyInstaller paths."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "web"
    return Path(__file__).resolve().parent.parent / "web"


def _ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _mask_key(key: str) -> str:
    """Mask an API key for display (show first 8 + last 4 chars)."""
    if len(key) <= 12:
        return "*" * len(key)
    return key[:8] + "*" * (len(key) - 12) + key[-4:]


# ── WebSocket Connection Manager ─────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

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
        for ws in list(self.active):
            try:
                await ws.send_text(message)
            except Exception:
                self.active.remove(ws)


# ── App State ─────────────────────────────────────────────────────────────

class AppState:
    """Shared mutable state for background tasks."""

    def __init__(self):
        self.msg_count: int = 0
        self.connected: bool = False
        self.auto_reply_running: bool = False
        self.stop_event: threading.Event = threading.Event()
        self.processed_messages: set[str] = set()
        self.notification: str = "Iniciando..."
        # QR cache — avoid regenerating on every request
        self.qr_data: bytes | None = None
        self.qr_fetched_at: float = 0.0
        self.qr_version: int = 0  # bumped when QR changes
        # Message batching — accumulate messages per contact before responding
        # Each item: {"text": str, "image_path": str|None, "audio_path": str|None}
        self.pending_messages: dict[str, list[dict]] = {}  # phone -> [msg_dict, ...]
        self.batch_tasks: dict[str, asyncio.Task] = {}  # phone -> scheduled task
        # Track recently sent replies to filter GOWA webhook echo-backs
        self.recently_sent: dict[str, float] = {}  # "phone:content_hash" -> timestamp


# ── Factory ───────────────────────────────────────────────────────────────

def create_app(
    settings,
    gowa_manager,
    gowa_client,
    agent_handler,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    ws_manager = ConnectionManager()
    state = AppState()
    web_dir = _get_web_dir()

    # ── Background Tasks ──────────────────────────────────────────────

    async def _start_gowa_task():
        """Start GOWA subprocess and register device."""
        try:
            await asyncio.to_thread(gowa_manager.start)
            for _ in range(10):
                await asyncio.sleep(1)
                if await asyncio.to_thread(gowa_client.health_check):
                    break
            if await asyncio.to_thread(gowa_client.ensure_device):
                state.notification = "GOWA pronto, aguardando QR..."
                await ws_manager.broadcast("gowa_status", {"message": state.notification})
            else:
                state.notification = "Erro ao registrar device no GOWA"
                await ws_manager.broadcast("gowa_status", {"message": state.notification})
        except FileNotFoundError:
            state.notification = "GOWA não encontrado — modo sandbox disponível"
            logger.info("GOWA binary not found, sandbox-only mode.")
            await ws_manager.broadcast("gowa_status", {"message": state.notification})
        except Exception as e:
            state.notification = "GOWA indisponível — modo sandbox disponível"
            logger.info("GOWA failed to start (%s), sandbox-only mode.", e)
            await ws_manager.broadcast("gowa_status", {"message": state.notification})

    async def _status_poll_loop():
        """Poll WhatsApp connection status every 5 seconds."""
        while not state.stop_event.is_set():
            try:
                connected = await asyncio.to_thread(gowa_client.is_connected)
                state.connected = connected
                await ws_manager.broadcast("status", {
                    "connected": state.connected,
                    "msg_count": state.msg_count,
                    "auto_reply_running": state.auto_reply_running,
                })
                # Auto-reply is handled via GOWA webhook (POST /api/webhook)
                state.auto_reply_running = connected and settings.get("auto_reply", True)
            except Exception as e:
                logger.error("Status poll error: %s", e)
            await asyncio.sleep(5)

    async def _qr_poll_loop():
        """Poll QR availability and cache QR image.

        QR codes from WhatsApp are valid for ~20s. We fetch a new one
        only when the cache is older than QR_CACHE_TTL, so the frontend
        always shows a stable image the user can actually scan.
        """
        QR_CACHE_TTL = 25  # seconds — slightly under WhatsApp's ~30s QR lifetime
        while not state.stop_event.is_set():
            try:
                if not state.connected:
                    age = time.time() - state.qr_fetched_at
                    if state.qr_data is None or age >= QR_CACHE_TTL:
                        qr_data = await asyncio.to_thread(gowa_client.get_qr_code)
                        if qr_data and isinstance(qr_data, bytes):
                            state.qr_data = qr_data
                            state.qr_fetched_at = time.time()
                            state.qr_version += 1
                            await ws_manager.broadcast("qr_update", {
                                "available": True,
                                "version": state.qr_version,
                            })
                        elif state.qr_data is None:
                            await ws_manager.broadcast("qr_update", {"available": False})
                else:
                    if state.qr_data is not None:
                        state.qr_data = None
                        state.qr_fetched_at = 0
                    await ws_manager.broadcast("qr_update", {"available": False})
            except Exception as e:
                logger.error("QR poll error: %s", e)
            # Poll faster when we don't have a QR yet (waiting for first one)
            await asyncio.sleep(2 if state.qr_data is None and not state.connected else 5)

    # ── Lifespan ──────────────────────────────────────────────────────

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.stop_event.clear()
        tasks = [
            asyncio.create_task(_start_gowa_task()),
            asyncio.create_task(_status_poll_loop()),
            asyncio.create_task(_qr_poll_loop()),
        ]
        yield
        # Shutdown
        state.stop_event.set()
        for task in tasks:
            task.cancel()
        try:
            settings.save()
        except Exception:
            pass
        try:
            gowa_manager.stop()
        except Exception:
            pass
        logger.info("Server shutdown complete.")

    # ── FastAPI App ───────────────────────────────────────────────────

    app = FastAPI(title="WhatsBot", lifespan=lifespan)

    # Mount static files (frontend assets)
    static_dir = web_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount statics/ for GOWA media files (auto-downloaded images, audio, etc.)
    statics_dir = settings.data_dir / "statics"
    statics_media_dir = statics_dir / "media"
    statics_senditems_dir = statics_dir / "senditems"
    statics_media_dir.mkdir(parents=True, exist_ok=True)
    statics_senditems_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/statics", StaticFiles(directory=str(statics_dir)), name="statics")

    # ── Routes ────────────────────────────────────────────────────────

    @app.get("/")
    @app.get("/dashboard")
    @app.get("/sandbox")
    @app.get("/costs")
    async def index():
        index_file = web_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"error": "Frontend not found"}, status_code=404)

    @app.get("/api/config")
    async def get_config():
        return _ok({
            "openrouter_api_key": _mask_key(settings.get("openrouter_api_key", "")),
            "model": settings.get("model", "openai/gpt-4o-mini"),
            "audio_model": settings.get("audio_model", "google/gemini-2.0-flash-001"),
            "image_model": settings.get("image_model", "google/gemini-2.0-flash-001"),
            "system_prompt": settings.get("system_prompt", ""),
            "auto_reply": settings.get("auto_reply", True),
            "reply_to_all": settings.get("reply_to_all", True),
            "only_saved_contacts": settings.get("only_saved_contacts", False),
            "max_context_messages": settings.get("max_context_messages", 10),
            "message_batch_delay": settings.get("message_batch_delay", 3.0),
        })

    @app.put("/api/config")
    async def save_config(body: dict):
        allowed_keys = {
            "openrouter_api_key", "model", "audio_model", "image_model",
            "system_prompt", "auto_reply", "reply_to_all", "only_saved_contacts",
            "max_context_messages", "message_batch_delay",
        }
        for key, value in body.items():
            if key in allowed_keys:
                settings[key] = value
        settings.save()

        agent_handler.update_config(
            api_key=settings.get("openrouter_api_key", ""),
            system_prompt=settings.get("system_prompt", ""),
            model=settings.get("model", "openai/gpt-4o-mini"),
            audio_model=settings.get("audio_model", "google/gemini-2.0-flash-001"),
            image_model=settings.get("image_model", "google/gemini-2.0-flash-001"),
            max_context_messages=settings.get("max_context_messages", 10),
        )

        await ws_manager.broadcast("config_saved", {})
        logger.info("Config saved.")
        return _ok({"message": "Configurações salvas!"})

    @app.post("/api/config/test-key")
    async def test_api_key(body: dict):
        api_key = body.get("api_key", "").strip()
        if not api_key:
            return _err("Insira uma API key primeiro.")
        ok, msg = await asyncio.to_thread(agent_handler.test_api_key, api_key)
        # Auto-save valid key
        if ok:
            settings["openrouter_api_key"] = api_key
            settings.save()
            agent_handler.update_config(
                api_key=api_key,
                system_prompt=settings.get("system_prompt", ""),
                model=settings.get("model", "openai/gpt-4o-mini"),
                audio_model=settings.get("audio_model", "google/gemini-2.0-flash-001"),
                image_model=settings.get("image_model", "google/gemini-2.0-flash-001"),
                max_context_messages=settings.get("max_context_messages", 10),
            )
            logger.info("API key tested and auto-saved.")
        return _ok({"valid": ok, "message": msg})

    # ── Models cache ──────────────────────────────────────────────
    _models_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
    _MODELS_CACHE_TTL = 600  # 10 minutes

    @app.get("/api/models")
    async def list_models():
        """Return OpenRouter model list (cached for 10 min)."""
        now = time.time()
        if _models_cache["data"] and now - _models_cache["fetched_at"] < _MODELS_CACHE_TTL:
            return _ok(_models_cache["data"])
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get("https://openrouter.ai/api/v1/models")
                resp.raise_for_status()
                raw = resp.json()
            models = []
            for m in raw.get("data", []):
                arch = m.get("architecture", {})
                models.append({
                    "id": m.get("id", ""),
                    "name": m.get("name", ""),
                    "input_modalities": arch.get("input_modalities", ["text"]),
                    "pricing": m.get("pricing", {}),
                })
            models.sort(key=lambda x: x["name"].lower())
            _models_cache["data"] = models
            _models_cache["fetched_at"] = now
            return _ok(models)
        except Exception as e:
            logger.error("Failed to fetch OpenRouter models: %s", e)
            if _models_cache["data"]:
                return _ok(_models_cache["data"])
            return _err(f"Erro ao buscar modelos: {e}", status=502)

    @app.get("/api/status")
    async def get_status():
        return _ok({
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
            "notification": state.notification,
        })

    @app.get("/api/qr")
    async def get_qr():
        if state.connected or not state.qr_data:
            return Response(status_code=204)
        return Response(
            content=state.qr_data,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/whatsapp/reconnect")
    async def reconnect():
        await asyncio.to_thread(gowa_client.reconnect)
        state.notification = "Reconectando..."
        await ws_manager.broadcast("gowa_status", {"message": state.notification})
        return _ok({"message": "Reconectando..."})

    @app.post("/api/whatsapp/logout")
    async def logout():
        await asyncio.to_thread(gowa_client.logout)
        state.connected = False
        state.notification = "Desconectado."
        await ws_manager.broadcast("status", {
            "connected": False,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
        })
        await ws_manager.broadcast("gowa_status", {"message": state.notification})
        return _ok({"message": "Desconectado."})

    # ── Message Batch Processing ────────────────────────────────────

    async def _send_reply(phone: str, reply: str):
        """Send a text reply and broadcast to frontend."""
        delay_min = settings.get("response_delay_min", 1.0)
        delay_max = settings.get("response_delay_max", 3.0)
        await asyncio.sleep(random.uniform(delay_min, delay_max))

        # Track sent reply so webhook can filter GOWA echo-backs
        sent_key = f"{phone}:{reply[:120]}"
        state.recently_sent[sent_key] = time.time()

        await asyncio.to_thread(gowa_client.send_message, phone, reply)
        await asyncio.to_thread(gowa_client.stop_chat_presence, phone)
        state.msg_count += 1
        logger.info("[Batch] Replied to %s: %s", phone, reply[:80])

        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "message": {"role": "assistant", "content": reply, "ts": time.time()},
        })
        await ws_manager.broadcast("status", {
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
        })

    async def _process_batch(phone: str, delay: float):
        """Wait for batch delay, then process all accumulated messages."""
        await asyncio.sleep(delay)

        items = state.pending_messages.pop(phone, [])
        state.batch_tasks.pop(phone, None)

        if not items:
            return

        contact = agent_handler._get_contact(phone)

        # Separate plain text items from media items
        text_parts: list[str] = []
        media_items: list[dict] = []
        for item in items:
            if item.get("image_path") or item.get("audio_path"):
                media_items.append(item)
            else:
                text_parts.append(item.get("text", ""))

        # Process combined text messages first
        if text_parts:
            combined = "\n".join(t for t in text_parts if t)
            if combined:
                logger.info("[Batch] Processing %d text messages from %s: %s",
                            len(text_parts), phone, combined[:80])
                contact.add_message("user", combined)
                if contact.ai_enabled:
                    try:
                        await asyncio.to_thread(gowa_client.send_chat_presence, phone)
                        reply = await asyncio.to_thread(
                            agent_handler.process_message, phone, combined,
                            save_user_message=False)
                        if reply:
                            await _send_reply(phone, reply)
                    except Exception as e:
                        logger.error("[Batch] Agent error for %s: %s", phone, e)

        # Process each media item individually
        for item in media_items:
            text = item.get("text", "")
            image_path = item.get("image_path")
            audio_path = item.get("audio_path")

            media_label = "image" if image_path else "audio"
            logger.info("[Batch] Processing %s from %s", media_label, phone)

            # Save message to contact memory
            contact.add_message(
                "user", text or ("[Áudio recebido]" if audio_path else ""),
                media_type="image" if image_path else "audio",
                media_path=image_path or audio_path,
            )

            # Transcribe audio / describe image
            transcription = ""
            try:
                if audio_path:
                    transcription = await asyncio.to_thread(
                        agent_handler.transcribe_audio, audio_path, phone)
                elif image_path:
                    transcription = await asyncio.to_thread(
                        agent_handler.describe_image, image_path, phone)
            except Exception as e:
                logger.error("[Batch] Transcription error for %s: %s", phone, e)

            # Save transcription as private message and broadcast.
            # Also update the original user message so the LLM sees the
            # transcription instead of the placeholder "[Áudio recebido]".
            if transcription:
                contact.add_message("transcription", transcription)
                # Update the last user message content with the transcription
                for msg in reversed(contact.messages):
                    if msg.get("role") == "user" and msg.get("media_type") in ("audio", "image"):
                        if audio_path:
                            msg["content"] = f"[Transcrição do áudio]: {transcription}"
                        elif image_path:
                            prefix = f"[Descrição da imagem]: {transcription}"
                            msg["content"] = f"{prefix}\n{text}" if text else prefix
                        contact.save()
                        break
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "message": {
                        "role": "transcription",
                        "content": transcription,
                        "ts": time.time(),
                    },
                })

            if not contact.ai_enabled:
                continue

            # Build text for LLM: use transcription if available
            llm_text = text or ""
            if audio_path:
                if transcription:
                    llm_text = f"[Transcrição do áudio]: {transcription}"
                else:
                    llm_text = llm_text or "[Áudio recebido]"
            elif image_path and transcription:
                prefix = f"[Descrição da imagem]: {transcription}"
                llm_text = f"{prefix}\n{text}" if text else prefix

            try:
                await asyncio.to_thread(gowa_client.send_chat_presence, phone)
                reply = await asyncio.to_thread(
                    agent_handler.process_message, phone,
                    llm_text,
                    save_user_message=False,
                    image_path=image_path if not transcription else None,
                )
                if reply:
                    await _send_reply(phone, reply)
            except Exception as e:
                logger.error("[Batch] Agent error for %s (%s): %s", phone, media_label, e)

    # ── Webhook (real-time messages from GOWA) ──────────────────────

    @app.post("/api/webhook")
    async def webhook(body: dict):
        """Receive real-time message events from GOWA webhook."""
        event = body.get("event", "")
        # GOWA wraps message data inside "payload"
        data = body.get("payload", body.get("data", body))

        # Handle chat presence events (typing/recording indicators)
        if event == "chat_presence":
            from_jid = data.get("from", "")
            phone = from_jid.split("@")[0] if "@" in from_jid else from_jid
            presence_state = data.get("state", "")
            media = data.get("media", "")
            if phone and presence_state:
                logger.info("[Webhook] chat_presence %s from %s (media=%s)",
                            presence_state, phone, media or "text")
                await ws_manager.broadcast("chat_presence", {
                    "phone": phone,
                    "state": presence_state,
                    "media": media,
                })
            return _ok({"status": "presence"})

        # Only process incoming messages
        if event and event not in ("message", "message:received", ""):
            return _ok({"status": "ignored"})

        if not isinstance(data, dict):
            return _ok({"status": "ignored"})

        # Extract message fields (GOWA field names vary)
        is_from_me = data.get("is_from_me", data.get("from_me", data.get("FromMe", False)))
        if is_from_me:
            return _ok({"status": "ignored"})

        msg_id = data.get("id", data.get("Id", data.get("message_id", ""))
                         ) or str(uuid.uuid4())
        if msg_id in state.processed_messages:
            return _ok({"status": "duplicate"})

        # Extract body — try multiple known field names
        text = (data.get("content", "")
                or data.get("body", "")
                or data.get("Body", "")
                or data.get("message", "")
                or data.get("text", "")).strip()

        # Extract media paths from GOWA payload
        image_path: str | None = None
        audio_path: str | None = None

        raw_image = data.get("image")
        if raw_image:
            if isinstance(raw_image, str):
                image_path = raw_image
            elif isinstance(raw_image, dict):
                image_path = raw_image.get("path", "")
                if not text:
                    text = (raw_image.get("caption", "") or "").strip()

        raw_audio = data.get("audio")
        if raw_audio:
            if isinstance(raw_audio, str):
                audio_path = raw_audio
            elif isinstance(raw_audio, dict):
                audio_path = raw_audio.get("path", "")

        # Video notes (voice messages) are treated as audio
        raw_vn = data.get("video_note")
        if raw_vn and not audio_path:
            if isinstance(raw_vn, str):
                audio_path = raw_vn
            elif isinstance(raw_vn, dict):
                audio_path = raw_vn.get("path", "")

        # For audio without text, set a placeholder
        if audio_path and not text:
            text = "[Áudio recebido]"

        # Extract sender
        sender = (data.get("sender_jid", "")
                  or data.get("chat_jid", "")
                  or data.get("jid", "")
                  or data.get("from", "")
                  or data.get("sender", ""))

        if not sender or (not text and not image_path and not audio_path):
            logger.info("[Webhook] Skipping: text=%r sender=%r media=%s",
                        text[:50] if text else "", sender,
                        "image" if image_path else ("audio" if audio_path else "none"))
            return _ok({"status": "ignored"})

        state.processed_messages.add(msg_id)
        phone = sender.split("@")[0] if "@" in sender else sender

        # Filter GOWA echo-backs: ignore messages we recently sent
        if text:
            sent_key = f"{phone}:{text[:120]}"
            sent_at = state.recently_sent.pop(sent_key, None)
            if sent_at and (time.time() - sent_at) < 30:
                logger.info("[Webhook] Ignoring echo-back for %s", phone)
                return _ok({"status": "echo"})

        # Determine media metadata for broadcast
        media_type: str | None = None
        media_path: str | None = None
        if image_path:
            media_type = "image"
            media_path = image_path
        elif audio_path:
            media_type = "audio"
            media_path = audio_path

        logger.info("[Webhook] %s from %s: %s",
                    media_type.capitalize() if media_type else "Message",
                    phone, text[:80] if text else f"[{media_type}]")

        # Increment unread count for incoming user messages
        await asyncio.to_thread(lambda: agent_handler._get_contact(phone).increment_unread())

        # Broadcast incoming message to frontend in real-time
        broadcast_msg: dict = {"role": "user", "content": text, "ts": time.time()}
        if media_type:
            broadcast_msg["media_type"] = media_type
            broadcast_msg["media_path"] = media_path
        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "message": broadcast_msg,
        })

        # Batch messages — accumulate and wait before responding
        if phone not in state.pending_messages:
            state.pending_messages[phone] = []
        state.pending_messages[phone].append({
            "text": text,
            "image_path": image_path,
            "audio_path": audio_path,
        })

        # Cancel existing batch timer for this contact
        if phone in state.batch_tasks:
            state.batch_tasks[phone].cancel()

        # Schedule batch processing after delay
        batch_delay = settings.get("message_batch_delay", 3.0)
        state.batch_tasks[phone] = asyncio.create_task(
            _process_batch(phone, batch_delay)
        )

        # Prune processed set to avoid unbounded growth
        if len(state.processed_messages) > 5000:
            oldest = list(state.processed_messages)[:2500]
            for item in oldest:
                state.processed_messages.discard(item)

        # Prune stale recently_sent entries (older than 60s)
        now = time.time()
        stale = [k for k, v in state.recently_sent.items() if now - v > 60]
        for k in stale:
            del state.recently_sent[k]

        return _ok({"status": "batched"})

    # ── Sandbox (debug chat) ─────────────────────────────────────────

    @app.post("/api/sandbox/send")
    async def sandbox_send(body: dict):
        """Process a message through the same pipeline as WhatsApp, without GOWA."""
        phone = body.get("phone", "").strip()
        message = body.get("message", "").strip()
        if not phone:
            return _err("Campo 'phone' é obrigatório.")
        if not message:
            return _err("Campo 'message' é obrigatório.")

        logger.info("[Sandbox] Message from %s: %s", phone, message[:80])
        try:
            reply = await asyncio.to_thread(agent_handler.process_message, phone, message)
        except Exception as e:
            logger.error("[Sandbox] Error processing message: %s", e)
            return _err(f"Erro ao processar mensagem: {e}", status=500)

        state.msg_count += 1

        await ws_manager.broadcast("status", {
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
        })

        logger.info("[Sandbox] Reply to %s: %s", phone, reply[:80] if reply else "")
        return _ok({"reply": reply, "phone": phone})

    @app.post("/api/sandbox/clear")
    async def sandbox_clear(body: dict):
        """Clear conversation history for a sandbox phone number."""
        phone = body.get("phone", "").strip()
        if phone:
            agent_handler.clear_conversation(phone)
        else:
            agent_handler.clear_all_conversations()
        return _ok({"message": "Conversa limpa."})

    # ── Contacts ────────────────────────────────────────────────────────

    @app.get("/api/contacts")
    async def list_contacts(q: str = ""):
        """List all contacts with summary info."""
        def _list():
            contacts_dir = agent_handler.memory_dir
            results = []
            for f in contacts_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    phone = data.get("phone", f.stem)
                    info = data.get("info", {})
                    msgs = data.get("messages", [])
                    # Skip transcription messages for preview
                    visible = [m for m in msgs if m.get("role") != "transcription"]
                    last = visible[-1] if visible else None
                    # Build last message preview with media indicator
                    last_content = ""
                    if last:
                        lmt = last.get("media_type")
                        if lmt == "image":
                            last_content = last.get("content", "")[:80] or "📷 Imagem"
                        elif lmt == "audio":
                            last_content = "🎤 Áudio"
                        else:
                            last_content = (last.get("content") or "")[:80]
                    results.append({
                        "phone": phone,
                        "name": info.get("name", ""),
                        "last_message": last_content,
                        "last_message_role": last["role"] if last else "",
                        "last_message_ts": last.get("ts", 0) if last else 0,
                        "msg_count": len(msgs),
                        "unread_count": data.get("unread_count", 0),
                        "ai_enabled": data.get("ai_enabled", True),
                        "updated_at": data.get("updated_at", 0),
                    })
                except Exception:
                    continue
            results.sort(key=lambda c: c["updated_at"], reverse=True)
            if q:
                ql = q.lower()
                results = [c for c in results if ql in c["name"].lower() or ql in c["phone"]]
            return results
        return _ok(await asyncio.to_thread(_list))

    @app.get("/api/contacts/{phone}")
    async def get_contact(phone: str):
        """Return full contact data including conversation history."""
        def _load():
            fp = agent_handler.memory_dir / f"{phone}.json"
            if not fp.exists():
                return None
            data = json.loads(fp.read_text(encoding="utf-8"))
            # Mark as read when viewing contact
            if data.get("unread_count", 0) > 0:
                data["unread_count"] = 0
                fp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                if phone in agent_handler._contacts:
                    agent_handler._contacts[phone].unread_count = 0
            return data
        data = await asyncio.to_thread(_load)
        if data is None:
            return _err("Contato não encontrado.", status=404)
        return _ok(data)

    @app.post("/api/contacts/{phone}/send")
    async def send_to_contact(phone: str, body: dict):
        """Send a manual message to a contact (operator-initiated, no LLM)."""
        message = (body.get("message") or "").strip()
        if not message:
            return _err("Campo 'message' é obrigatório.")

        # Save to contact memory
        try:
            msg_data = await asyncio.to_thread(agent_handler.save_operator_message, phone, message)
        except Exception as e:
            logger.error("[Send] Failed to save message for %s: %s", phone, e)
            return _err(f"Erro ao salvar mensagem: {e}", status=500)

        # Track sent message to filter GOWA echo-backs
        state.recently_sent[f"{phone}:{message[:120]}"] = time.time()

        # Send via GOWA
        try:
            await asyncio.to_thread(gowa_client.send_message, phone, message)
        except Exception as e:
            logger.error("[Send] Failed to send message to %s: %s", phone, e)
            return _err(f"Erro ao enviar mensagem: {e}", status=500)

        logger.info("[Send] Manual message to %s: %s", phone, message[:80])

        # Broadcast to all WS clients
        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "message": msg_data,
        })

        return _ok({"message": "Mensagem enviada."})

    @app.post("/api/contacts/{phone}/send-image")
    async def send_image_to_contact(
        phone: str,
        image: UploadFile = File(...),
        caption: str = Form(""),
    ):
        """Send an image to a contact (operator-initiated)."""
        suffix = Path(image.filename or "img.png").suffix or ".png"
        dest = statics_senditems_dir / f"{int(time.time() * 1000)}{suffix}"
        content = await image.read()
        dest.write_bytes(content)

        try:
            await asyncio.to_thread(gowa_client.send_image, phone, str(dest), caption)
        except Exception as e:
            logger.error("[Send] Failed to send image to %s: %s", phone, e)
            return _err(f"Erro ao enviar imagem: {e}", status=500)

        # Relative path for storage and frontend
        rel_path = f"statics/senditems/{dest.name}"
        msg_data = {
            "role": "assistant",
            "content": caption,
            "ts": time.time(),
            "media_type": "image",
            "media_path": rel_path,
        }
        contact = agent_handler._get_contact(phone)
        contact.add_message("assistant", caption, media_type="image", media_path=rel_path)

        await ws_manager.broadcast("new_message", {"phone": phone, "message": msg_data})
        logger.info("[Send] Image sent to %s", phone)
        return _ok({"message": "Imagem enviada."})

    @app.post("/api/contacts/{phone}/send-audio")
    async def send_audio_to_contact(
        phone: str,
        audio: UploadFile = File(...),
    ):
        """Send an audio file to a contact (operator-initiated)."""
        suffix = Path(audio.filename or "voice.ogg").suffix or ".ogg"
        dest = statics_senditems_dir / f"{int(time.time() * 1000)}{suffix}"
        content = await audio.read()
        dest.write_bytes(content)

        try:
            await asyncio.to_thread(gowa_client.send_audio, phone, str(dest))
        except Exception as e:
            logger.error("[Send] Failed to send audio to %s: %s", phone, e)
            return _err(f"Erro ao enviar áudio: {e}", status=500)

        rel_path = f"statics/senditems/{dest.name}"
        msg_data = {
            "role": "assistant",
            "content": "[Áudio]",
            "ts": time.time(),
            "media_type": "audio",
            "media_path": rel_path,
        }
        contact = agent_handler._get_contact(phone)
        contact.add_message("assistant", "[Áudio]", media_type="audio", media_path=rel_path)

        await ws_manager.broadcast("new_message", {"phone": phone, "message": msg_data})
        logger.info("[Send] Audio sent to %s", phone)
        return _ok({"message": "Áudio enviado."})

    @app.post("/api/contacts/{phone}/presence")
    async def send_presence_to_contact(phone: str, body: dict):
        """Send typing/stop presence indicator to a contact (operator-initiated)."""
        action = body.get("action", "start")
        await asyncio.to_thread(gowa_client.send_chat_presence, phone, action)
        return _ok({"status": "ok"})

    @app.post("/api/contacts/{phone}/read")
    async def mark_contact_read(phone: str):
        """Mark all messages from this contact as read (reset unread_count)."""
        def _mark():
            contact = agent_handler._get_contact(phone)
            contact.mark_as_read()
        await asyncio.to_thread(_mark)
        return _ok({"message": "Marcado como lido."})

    @app.post("/api/contacts/{phone}/toggle-ai")
    async def toggle_contact_ai(phone: str, body: dict):
        """Enable or disable AI auto-reply for a specific contact."""
        enabled = body.get("enabled")
        if enabled is None:
            return _err("Campo 'enabled' é obrigatório.")
        def _toggle():
            contact = agent_handler._get_contact(phone)
            contact.set_ai_enabled(bool(enabled))
            return contact.ai_enabled
        result = await asyncio.to_thread(_toggle)
        await ws_manager.broadcast("contact_ai_toggled", {
            "phone": phone,
            "ai_enabled": result,
        })
        return _ok({"ai_enabled": result})

    @app.put("/api/contacts/{phone}/info")
    async def update_contact_info(phone: str, body: dict):
        """Update contact info fields (name, email, profession, company, observations)."""
        def _update():
            contact = agent_handler._get_contact(phone)
            # Update scalar fields via update_info
            contact.update_info(
                name=body.get("name", ""),
                email=body.get("email", ""),
                profession=body.get("profession", ""),
                company=body.get("company", ""),
            )
            # Observations: replace entire list (update_info only appends)
            if "observations" in body:
                contact.info["observations"] = [
                    o for o in body["observations"] if isinstance(o, str) and o.strip()
                ]
                contact.save()
            return contact.info
        info = await asyncio.to_thread(_update)
        return _ok(info)

    # ── Logs ───────────────────────────────────────────────────────────

    @app.get("/api/logs")
    async def get_logs(limit: int = 200):
        """Return recent log entries from the in-memory buffer."""
        return _ok(_memory_log_handler.get_logs(limit))

    @app.delete("/api/logs")
    async def clear_logs():
        _memory_log_handler.clear()
        return _ok({"message": "Logs limpos."})

    # ── WebSocket ─────────────────────────────────────────────────────

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await ws_manager.connect(websocket)
        # Send initial state
        try:
            await websocket.send_text(json.dumps({"event": "status", "data": {
                "connected": state.connected,
                "msg_count": state.msg_count,
                "auto_reply_running": state.auto_reply_running,
            }}))
            await websocket.send_text(json.dumps({"event": "gowa_status", "data": {
                "message": state.notification,
            }}))
            # Send current QR state so page refreshes show QR immediately
            if not state.connected and state.qr_data:
                await websocket.send_text(json.dumps({"event": "qr_update", "data": {
                    "available": True,
                    "version": state.qr_version,
                }}))
            else:
                await websocket.send_text(json.dumps({"event": "qr_update", "data": {
                    "available": False,
                }}))
        except Exception:
            pass
        # Keep alive
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                if msg.get("action") == "ping":
                    await websocket.send_text(json.dumps({"event": "pong", "data": {}}))
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)

    # ── Pricing lookup for usage tracking ──────────────────────────────

    def _get_model_pricing(model_id: str) -> tuple[float, float]:
        """Return (prompt_price_per_token, completion_price_per_token) from cache.

        If the cache is empty, fetches models synchronously (runs in to_thread).
        """
        if not _models_cache["data"]:
            try:
                resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=15)
                resp.raise_for_status()
                raw = resp.json()
                models = []
                for m in raw.get("data", []):
                    arch = m.get("architecture", {})
                    models.append({
                        "id": m.get("id", ""),
                        "name": m.get("name", ""),
                        "input_modalities": arch.get("input_modalities", ["text"]),
                        "pricing": m.get("pricing", {}),
                    })
                models.sort(key=lambda x: x["name"].lower())
                _models_cache["data"] = models
                _models_cache["fetched_at"] = time.time()
                logger.info("Models cache populated for pricing (%d models)", len(models))
            except Exception as e:
                logger.warning("Failed to fetch models for pricing: %s", e)
                return 0.0, 0.0
        for m in _models_cache["data"]:
            if m["id"] == model_id:
                p = m.get("pricing", {})
                return float(p.get("prompt", "0") or "0"), float(p.get("completion", "0") or "0")
        return 0.0, 0.0

    agent_handler.pricing_fn = _get_model_pricing

    # ── Usage / Cost endpoints ───────────────────────────────────────

    def _parse_period(period: str | None, start: float | None, end: float | None) -> tuple[float | None, float | None]:
        """Convert period shorthand or explicit timestamps to (start_ts, end_ts)."""
        if start is not None or end is not None:
            return start, end
        if not period:
            return None, None
        now = time.time()
        mapping = {"24h": 86400, "3d": 259200, "7d": 604800, "30d": 2592000}
        seconds = mapping.get(period)
        if seconds:
            return now - seconds, now
        return None, None

    def _load_all_contacts() -> list:
        """Load all contact files from disk (for usage aggregation)."""
        contacts_dir = agent_handler.memory_dir
        result = []
        if not contacts_dir.exists():
            return result
        for f in contacts_dir.glob("*.json"):
            phone = f.stem
            contact = agent_handler._get_contact(phone)
            result.append(contact)
        return result

    @app.get("/api/usage/summary")
    async def usage_summary(period: str | None = None, start: float | None = None, end: float | None = None):
        start_ts, end_ts = _parse_period(period, start, end)
        contacts = await asyncio.to_thread(_load_all_contacts)
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                  "cost_usd": 0.0, "call_count": 0, "by_type": {}}
        for c in contacts:
            s = c.get_usage_summary(start_ts, end_ts)
            totals["prompt_tokens"] += s["prompt_tokens"]
            totals["completion_tokens"] += s["completion_tokens"]
            totals["total_tokens"] += s["total_tokens"]
            totals["cost_usd"] += s["cost_usd"]
            totals["call_count"] += s["call_count"]
            for ct, bt in s["by_type"].items():
                agg = totals["by_type"].setdefault(ct, {
                    "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "call_count": 0,
                })
                agg["cost_usd"] += bt["cost_usd"]
                agg["prompt_tokens"] += bt["prompt_tokens"]
                agg["completion_tokens"] += bt["completion_tokens"]
                agg["total_tokens"] += bt["total_tokens"]
                agg["call_count"] += bt["call_count"]
        totals["period_start"] = start_ts
        totals["period_end"] = end_ts
        return _ok(totals)

    @app.get("/api/usage/by-contact")
    async def usage_by_contact(period: str | None = None, start: float | None = None, end: float | None = None):
        start_ts, end_ts = _parse_period(period, start, end)
        contacts = await asyncio.to_thread(_load_all_contacts)
        rows = []
        for c in contacts:
            s = c.get_usage_summary(start_ts, end_ts)
            if s["call_count"] == 0:
                continue
            s["phone"] = c.phone
            s["name"] = c.info.get("name", "") or ""
            rows.append(s)
        rows.sort(key=lambda r: r["cost_usd"], reverse=True)
        return _ok(rows)

    @app.get("/api/usage/contact/{phone}")
    async def usage_contact_detail(phone: str, period: str | None = None, start: float | None = None, end: float | None = None):
        start_ts, end_ts = _parse_period(period, start, end)
        contact = agent_handler._get_contact(phone)
        filtered = contact.usage
        if start_ts is not None:
            filtered = [u for u in filtered if u.get("ts", 0) >= start_ts]
        if end_ts is not None:
            filtered = [u for u in filtered if u.get("ts", 0) <= end_ts]
        return _ok(filtered)

    return app


