"""Sandbox (debug chat) endpoints."""

import asyncio
import logging

from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings

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
            result = await asyncio.to_thread(agent_handler.process_message, phone, message)
        except Exception as e:
            logger.error("[Sandbox] Error processing message: %s", e)
            return _err(f"Erro ao processar mensagem: {e}", status=500)

        if result.tool_calls:
            await deps.broadcast_tool_calls(phone, result.tool_calls, result.contact_info)

        state.msg_count += 1

        await ws_manager.broadcast("status", {
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
            "bot_phone": state.bot_phone,
            "bot_name": state.bot_name,
        })

        logger.info("[Sandbox] Reply to %s: %s", phone, result.reply[:80] if result.reply else "")
        return _ok({"reply": result.reply, "phone": phone})

    @app.post("/api/sandbox/clear")
    async def sandbox_clear(body: dict):
        """Clear conversation history for a sandbox phone number."""
        phone = body.get("phone", "").strip()
        if phone:
            agent_handler.clear_conversation(phone)
        else:
            agent_handler.clear_all_conversations()
        return _ok({"message": "Conversa limpa."})
