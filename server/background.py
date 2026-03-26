"""Background tasks for the WhatsBot server (GOWA startup, status/QR polling)."""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def start_gowa_task(deps):
    """Start GOWA subprocess and register device."""
    gowa_manager = deps.gowa_manager
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
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


async def status_poll_loop(deps):
    """Poll WhatsApp connection status every 5 seconds."""
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings
    while not state.stop_event.is_set():
        try:
            connected = await asyncio.to_thread(gowa_client.is_connected)
            state.connected = connected
            await ws_manager.broadcast("status", {
                "connected": state.connected,
                "msg_count": state.msg_count,
                "auto_reply_running": state.auto_reply_running,
                "bot_phone": state.bot_phone,
                "bot_name": state.bot_name,
            })
            # Auto-reply is handled via GOWA webhook (POST /api/webhook)
            state.auto_reply_running = connected and settings.get("auto_reply", True)
            # Populate bot identity for @mention detection in groups
            if connected and not state.bot_phone:
                # Try config first
                state.bot_phone = settings.get("bot_phone", "").split(":")[0]
                state.bot_name = settings.get("bot_name", "")
                # Auto-detect: fetch recent chat messages to find our own JID
                if not state.bot_phone:
                    try:
                        chats = await asyncio.to_thread(gowa_client.get_chats, 5)
                        for chat in chats:
                            cjid = chat.get("jid", "")
                            if "@s.whatsapp.net" in cjid:
                                msgs = await asyncio.to_thread(
                                    gowa_client.get_chat_messages, cjid, 5)
                                for m in msgs:
                                    if m.get("is_from_me") or m.get("from_me"):
                                        own_jid = (m.get("sender_jid", "")
                                                   or m.get("sender", "")
                                                   or m.get("from", ""))
                                        if own_jid and "@s.whatsapp.net" in own_jid:
                                            state.bot_phone = own_jid.split("@")[0].split(":")[0]
                                            logger.info("[Status] Bot phone auto-detected: %s",
                                                        state.bot_phone)
                                            break
                                if state.bot_phone:
                                    break
                    except Exception:
                        pass
                if state.bot_phone:
                    logger.info("[Status] Bot phone: %s, name: %s",
                                state.bot_phone, state.bot_name or "(empty)")
        except Exception as e:
            logger.error("Status poll error: %s", e)
        await asyncio.sleep(5)


async def qr_poll_loop(deps):
    """Poll QR availability and cache QR image.

    QR codes from WhatsApp are valid for ~20s. We fetch a new one
    only when the cache is older than QR_CACHE_TTL, so the frontend
    always shows a stable image the user can actually scan.
    """
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    QR_CACHE_TTL = 120  # seconds — WhatsApp QRs valid ~160s; refresh conservatively
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
