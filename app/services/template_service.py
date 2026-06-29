"""WhatsApp Cloud templates + 24h session-window service (Plano 23 · Fase B6).

Owns the template + session-window logic previously inline in
``server/routes/channels.py`` for STARTING a brand-new conversation (no
conversation row yet, plano 21): the 24h free-text window resolution
(``session-state``), the channel-scoped template list / create / delete, and the
``send-template`` flow (send → save operator message → WS broadcast → bus emit).

Branch by Abstraction — the routes resolve permission + body validation + 404,
then delegate here. Behavior preserved byte-for-byte (legacy suite is the
contract). Capability-driven: never branches on provider name — it asks the
``deps.outbound_router`` whether the channel ``supports("templates")`` and whether
its session is open.
"""

from __future__ import annotations

import asyncio
import time

from db.repositories import (contact_repo, conversation_repo, inbox_repo,
                             message_repo)
from plugins.events import emit_with_filter

TEMPLATE_CATEGORIES = {"UTILITY", "MARKETING", "AUTHENTICATION"}


def session_state(deps, channel_id: str, phone: str) -> dict:
    """Session/window state for starting a conversation on ``channel_id`` (plano 21).

    Resolves the contact's latest conversation IN this channel's inbox (if any) and
    computes the 24h free-text window from its last inbound. Synchronous DB work —
    run via ``asyncio.to_thread`` from the route.
    """
    outbound = deps.outbound_router
    templates_supported = outbound.supports(channel_id, "templates")
    contact = contact_repo.get_by_phone(phone)
    conv = None
    if contact:
        inbox = inbox_repo.get_by_channel(channel_id)
        if inbox:
            conv = conversation_repo.get_latest_for_contact_inbox(
                contact["id"], inbox["id"])
    conv_id = conv["id"] if conv else None
    last_ts = (message_repo.last_inbound_ts(conversation_id=conv_id)
               if conv_id else None)
    return {
        "templates_supported": templates_supported,
        "session_open": outbound.session_open(channel_id, last_ts),
        "has_conversation": conv is not None,
        "conversation_id": conv_id,
        "last_inbound_ts": last_ts,
    }


def supports_templates(deps, channel_id: str) -> bool:
    return deps.outbound_router.supports(channel_id, "templates")


async def list_templates(deps, channel_id: str) -> list[dict]:
    return await asyncio.to_thread(deps.outbound_router.list_templates, channel_id)


async def send_template(deps, channel_id: str, *, phone: str, template_name: str,
                        language: str, components, preview_text: str):
    """Send an approved template and persist the operator message (plano 21).

    Returns one of:
      * ``("send_failed", error)`` — the provider send failed (route → 502);
      * ``("save_failed", error)`` — sent but saving the message failed (→ 500);
      * ``("ok", {message, msg_id, phone})`` — success.

    Saving the operator message creates the contact + conversation in this
    channel's inbox, so the new thread appears in the sidebar like a normal first
    send; then it broadcasts ``new_message`` and emits ``message.sent``.
    """
    outbound = deps.outbound_router
    result = await asyncio.to_thread(
        outbound.send_template, channel_id, phone, template_name,
        lang=language, components=components or None)
    if not result.ok:
        return ("send_failed", result.error)

    msg_id = result.external_msg_id or None
    preview = (preview_text or "").strip() or f"📋 Template: {template_name}"
    try:
        msg_data = await asyncio.to_thread(
            deps.agent_handler.save_operator_message, phone, preview,
            status="operator", msg_id=msg_id, channel_id=channel_id)
    except Exception as e:  # noqa: BLE001
        return ("save_failed", str(e))

    try:
        await deps.ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": channel_id, "message": msg_data})
    except Exception:
        pass
    await emit_with_filter("message.sent", {
        "phone": phone, "text": preview, "msg_id": msg_id,
        "media_type": None, "media_path": None,
        "source": "template", "status": "operator",
        "template_name": template_name, "ts": time.time(),
    })
    return ("ok", {"message": "Template enviado.", "msg_id": msg_id, "phone": phone})


async def create_template(deps, channel_id: str, *, name: str, category: str,
                          language: str, body_text: str, header_text: str | None,
                          footer_text: str | None, body_examples, header_examples):
    """Create a template on ``channel_id``. Returns ``("ok", data)`` or
    ``("failed", error)``."""
    result = await asyncio.to_thread(
        deps.outbound_router.create_template, channel_id, name,
        category=category, language=language, body_text=body_text,
        header_text=header_text, footer_text=footer_text,
        body_examples=body_examples or None,
        header_examples=header_examples or None)
    if not result.get("ok"):
        return ("failed", result.get("error"))
    return ("ok", {
        "message": "Template enviado para aprovação da Meta.",
        "id": result.get("id"), "status": result.get("status"),
        "category": result.get("category"), "name": name,
    })


async def delete_template(deps, channel_id: str, name: str):
    """Delete a template (all languages). Returns ``("ok", data)`` or
    ``("failed", error)``."""
    result = await asyncio.to_thread(
        deps.outbound_router.delete_template, channel_id, name)
    if not result.get("ok"):
        return ("failed", result.get("error"))
    return ("ok", {"message": "Template apagado.", "name": name})
