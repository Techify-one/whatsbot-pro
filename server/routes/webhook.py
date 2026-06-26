"""Webhook endpoint — receives real-time messages from GOWA.

Inbound also enters here from OTHER channels (WhatsApp Cloud, Telegram, …) via the
provider-agnostic ``ingest_event(InboundEvent)`` funnel (plano 11): a parsed event
is broadcast/saved and fed into the SAME typing-aware batch orchestrator the GOWA
webhook uses, then the reply is routed back out through the channel's own adapter
(``OutboundRouter``). No ``if provider ==`` anywhere in the pipeline.
"""

import asyncio
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path

from channels.events import InboundEvent
from channels import jid as jid_classifier
from channels import ai_settings
from db.repositories import channel_repo, contact_repo, conversation_repo, message_repo
from agent import group_mentions
# Media/reply parsing helpers live in gowa.inbound (plano 13 Fase 0); re-imported
# here so the legacy /api/webhook handler keeps behaving identically while the
# generic /api/webhook/gowa/{channel_id} path is validated.
from gowa.inbound import (_extract_media, _extract_reply_to, _coerce_path,
                          _deep_find_reply_id, _PATHED_MEDIA,
                          _REPLY_ID_KEYS, _REPLY_CTX_KEYS)
from server import system_notices
from server.execution import astart_execution, aend_execution, atrack_step, prune_executions
from server.helpers import _ok, parse_split_reply
from server.transcription import maybe_transcribe
from plugins.events import emit as emit_event, apply_filter, emit_with_filter

logger = logging.getLogger(__name__)


def _conversation_ai_active(contact) -> bool:
    """Per-conversation AI gate (plano 01 Fase 2, fatia 2).

    Returns the active conversation's ``ai_active`` flag, defaulting to True
    (fail-open) — um erro de resolução ou ausência de conversa NUNCA silencia o
    bot. Permite pausar a IA numa conversa específica sem mexer no contato.
    """
    try:
        conv = conversation_repo.get_open_for_contact(contact.id)
        return bool(conv["ai_active"]) if conv else True
    except Exception:
        logger.exception("Falha no gate ai_active para %s", getattr(contact, "phone", "?"))
        return True


# The GOWA webhook is single-channel today: every GOWA device POSTs to the same
# ``/api/webhook`` and is processed as the seeded ``default`` channel (batch key,
# orchestrator and contacts are all hardcoded to "default"). So the JID-type
# filter reads ``default``'s ``config.allowed_jid_types``. Cached briefly — this
# runs on every inbound message; channel config changes rarely.
_GOWA_CHANNEL_ID = "default"
_ALLOWED_JID_CACHE: dict = {"types": None, "ts": 0.0}
_ALLOWED_JID_TTL = 30.0


def _read_gowa_allowed_jid_types() -> list[str]:
    """Read the GOWA channel's allowed JID types from its ``config`` JSON.

    Falls back to :data:`channels.jid.DEFAULT_ALLOWED_JID_TYPES` when unset or
    malformed. Synchronous (DB read) — call via ``asyncio.to_thread``.
    """
    try:
        row = channel_repo.get(_GOWA_CHANNEL_ID)
        cfg = row.get("config") if row else None
        if isinstance(cfg, str) and cfg:
            cfg = json.loads(cfg)
        if isinstance(cfg, dict) and "allowed_jid_types" in cfg:
            return jid_classifier.normalize_allowed_types(cfg.get("allowed_jid_types"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Webhook] allowed_jid_types read failed: %s", e)
    return list(jid_classifier.DEFAULT_ALLOWED_JID_TYPES)


def reset_allowed_jid_cache() -> None:
    """Invalidate the cached allowed-JID-types (call after a channel config edit)."""
    _ALLOWED_JID_CACHE["types"] = None
    _ALLOWED_JID_CACHE["ts"] = 0.0


async def _gowa_allowed_jid_types() -> list[str]:
    now = time.time()
    cached = _ALLOWED_JID_CACHE["types"]
    if cached is not None and (now - _ALLOWED_JID_CACHE["ts"]) < _ALLOWED_JID_TTL:
        return cached
    types = await asyncio.to_thread(_read_gowa_allowed_jid_types)
    _ALLOWED_JID_CACHE["types"] = types
    _ALLOWED_JID_CACHE["ts"] = now
    return types


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings
    # Channel routing (plano 11): every outbound leg goes through the router so a
    # reply lands on the conversation's channel; ``registry`` resolves the live
    # provider instance for inbound media download.
    outbound = deps.outbound_router
    channel_registry = deps.channel_registry
    # Cache dir for inbound media downloaded from push-based providers (Cloud P16).
    media_dir = settings.data_dir / "statics" / "media"

    def _channel_ai_enabled(channel_id: str) -> bool:
        """Master AI gate (plano 21), checked BEFORE the per-conversation flag.

        Two layers: the GLOBAL ``auto_reply`` switch (the panel-wide on/off button)
        AND the channel's own ``ai_enabled`` override. Both must be on for the AI
        to reply on this channel. The per-conversation ``ai_active`` flag is checked
        separately by ``_conversation_ai_active``.
        """
        if not settings.get("auto_reply", True):
            return False
        return bool(ai_settings.value(channel_id, "ai_enabled", True))

    def _resolve_presence_conv_id(phone: str) -> int | None:
        """Resolve the GOWA conversation_id for a chat_presence event so the
        frontend can scope the "digitando" indicator to that exact conversation
        (a contact may have conversations on several channels — plano 11).

        GOWA is always the ``default`` channel (its legacy webhook). Cached per
        phone with a short TTL since presence events are frequent. Best-effort:
        returns ``None`` on any failure (frontend falls back to channel::phone)."""
        now = time.time()
        cached = state.presence_conv_cache.get(phone)
        if cached and cached[1] > now:
            return cached[0]
        conv_id: int | None = None
        try:
            contact = contact_repo.get_by_phone(phone)
            if contact:
                conv = conversation_repo.get_latest_for_contact_inbox(
                    contact["id"], conversation_repo.DEFAULT_INBOX_ID)
                if conv:
                    conv_id = conv["id"]
        except Exception:
            logger.debug("presence conv_id resolution failed for %s", phone,
                         exc_info=True)
        state.presence_conv_cache[phone] = (conv_id, now + 30.0)
        return conv_id

    # ── Group Mention Helpers ──────────────────────────────────────

    def _is_bot_mentioned(text: str, data: dict) -> bool:
        """Check if the bot is mentioned in a group message."""
        if not text:
            return False
        text_lower = text.lower()
        # Check @phone mention
        bot_phone = state.bot_phone
        if bot_phone and f"@{bot_phone}" in text:
            return True
        # Check @name mention (case-insensitive)
        bot_name = state.bot_name
        if bot_name and f"@{bot_name.lower()}" in text_lower:
            return True
        # Check mentioned_jids from GOWA payload (if present)
        mentioned = data.get("mentioned_jids", data.get("mentioned", []))
        if mentioned and bot_phone:
            for jid in mentioned:
                if bot_phone in str(jid):
                    return True
        return False

    def _strip_bot_mention(text: str) -> str:
        """Remove bot @mention from message text."""
        bot_phone = state.bot_phone
        bot_name = state.bot_name
        if bot_phone:
            text = text.replace(f"@{bot_phone}", "").strip()
        if bot_name:
            text = re.sub(rf"@{re.escape(bot_name)}", "", text, flags=re.IGNORECASE).strip()
        return text

    # ── Reply Splitting & Sending ─────────────────────────────────

    async def _send_reply(channel_id: str, phone: str, reply: str):
        """Send reply (possibly split into multiple parts) and broadcast.

        Channel-aware (plano 11): every leg goes through ``OutboundRouter`` so the
        reply lands on the conversation's own channel. Presence / @mentions are
        gated by ``ChannelCapabilities`` — a Cloud channel skips them silently.
        """
        caps = outbound.capabilities(channel_id)
        is_group_target = caps.groups and "@g.us" in phone

        # Plugin filter: full raw reply before split
        reply = await apply_filter("filter.reply.raw", reply, {"phone": phone})
        if reply is None:
            logger.info("[Batch] reply for %s aborted by filter.reply.raw", phone)
            return

        split_enabled = ai_settings.value(
            channel_id, "split_messages", settings.get("split_messages", True))

        if split_enabled:
            parts = parse_split_reply(reply)
        else:
            parts = [reply]

        # Plugin filter: list of parts (can add/remove/reorder)
        parts = await apply_filter("filter.reply.parts", parts, {"phone": phone})
        if parts is None or not parts:
            logger.info("[Batch] reply for %s aborted by filter.reply.parts", phone)
            return

        # Initial response delay (simulates typing)
        delay_min = settings.get("response_delay_min", 1.0)
        delay_max = settings.get("response_delay_max", 3.0)
        await asyncio.sleep(random.uniform(delay_min, delay_max))

        sent_parts = []  # collect (part_text, msg_id) for saving after send
        for i, part in enumerate(parts):
            # Plugin filter: each part right before send (signature, formatting, redact)
            part = await apply_filter(
                "filter.reply.part", part,
                {"phone": phone, "index": i, "total": len(parts)},
            )
            if part is None:
                logger.info("[Batch] part %d for %s skipped by filter.reply.part", i + 1, phone)
                continue

            if i > 0:
                # Inter-message delay with ±0.5s variation
                base_delay = ai_settings.value(
                    channel_id, "split_message_delay",
                    settings.get("split_message_delay", 2.0))
                if base_delay > 0:
                    await asyncio.sleep(base_delay + random.uniform(-0.5, 0.5))
                # Re-send typing indicator between parts (capability-gated)
                await asyncio.to_thread(outbound.send_presence, channel_id, phone, "composing")

            # Resolve @Name / @todos -> real mentions for group targets. We keep
            # `part` (friendly @Name) for save/broadcast and send `send_text`
            # (inline @<number>) + mentions on the wire. Only for channels that
            # support groups (Cloud/Telegram-1:1 skip this).
            send_text, mentions = part, None
            if is_group_target:
                send_text, mentions = await asyncio.to_thread(
                    group_mentions.resolve_outgoing, phone, part)

            # Track for echo-back filtering (key on the wire text we actually send)
            sent_key = f"{channel_id}:{phone}:{send_text[:120]}"
            state.recently_sent[sent_key] = time.time()

            send_result = await asyncio.to_thread(
                outbound.send_text, channel_id, phone, send_text,
                reply_to=None, mentions=mentions)
            if not send_result.ok:
                err = send_result.error or "envio falhou"
                logger.error("[Batch] Send failed for %s/%s (part %d/%d): %s",
                             channel_id, phone, i + 1, len(parts), err)
                await atrack_step("channel_send", {
                    "channel_id": channel_id, "phone": phone, "part": i + 1, "error": err,
                }, status="error")
                await asyncio.to_thread(outbound.send_presence, channel_id, phone, "paused")
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "message": {"role": "error", "content": f"Falha ao enviar: {err}", "ts": time.time()},
                })
                return
            await atrack_step("channel_send", {
                "channel_id": channel_id, "phone": phone,
                "part": i + 1, "total_parts": len(parts)})

            part_msg_id = send_result.external_msg_id or ""
            sent_parts.append((part, part_msg_id))

            # Broadcast each part to frontend individually
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "channel_id": channel_id,
                "message": {"role": "assistant", "content": part, "ts": time.time(),
                            "status": "sent", "msg_id": part_msg_id},
            })

            # Plugin event: AI reply leg
            await emit_with_filter("message.sent", {
                "phone": phone, "channel_id": channel_id, "text": part, "msg_id": part_msg_id,
                "media_type": None, "media_path": None,
                "source": "ai", "status": "sent",
                "ts": time.time(),
            })

        # Save each part as a separate message to preserve split across page refresh
        for part, part_msg_id in sent_parts:
            try:
                await asyncio.to_thread(agent_handler.save_assistant_message, phone, part,
                                        msg_id=part_msg_id, status="sent",
                                        channel_id=channel_id)
                # Increment unread AI count (operator hasn't seen this reply yet)
                contact = agent_handler._get_contact(phone, channel_id=channel_id)
                if contact:
                    await asyncio.to_thread(contact.increment_unread_ai)
            except Exception as e:
                logger.error("[Batch] Failed to save reply for %s: %s", phone, e)

        await asyncio.to_thread(outbound.send_presence, channel_id, phone, "paused")
        state.msg_count += 1
        full_reply = "\n".join(parts)
        await atrack_step("response_sent", {
            "phone": phone,
            "channel_id": channel_id,
            "parts": len(parts),
            "reply_preview": full_reply[:200],
        })
        logger.info("[Batch] Replied to %s/%s (%d parts): %s",
                    channel_id, phone, len(parts), full_reply[:80])

        await ws_manager.broadcast("status", {
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
            "bot_phone": state.bot_phone,
            "bot_name": state.bot_name,
        })

    async def _broadcast_tool_calls(phone: str, tool_calls: list[dict],
                                    contact_info: dict | None = None,
                                    *, channel_id: str = "default"):
        """Broadcast private messages for each tool call executed by the LLM."""
        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        for tc in tool_calls:
            tool_name = tc.get("tool", "unknown")
            args = tc.get("args", {})
            # Format: tool name + each arg on its own line
            lines = [f"\U0001f527 {tool_name}"]
            for key, value in args.items():
                lines.append(f"{key}: {value}")
            # Reflect the tool OUTCOME (success detail / error / where it saved)
            # instead of always implying success (plano 19). None-returning tools
            # (e.g. save_contact_info) add no line — the card stays as before.
            result = tc.get("result")
            if isinstance(result, str) and result.strip():
                lines.append(f"→ {result.strip()}")
            content = "\n".join(lines)

            contact.add_message("tool_call", content)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "tool_call",
                    "content": content,
                    "ts": time.time(),
                },
            })

        # Live-refresh the open info panel after a custom-attribute write (plano 19).
        # set_custom_attribute doesn't populate contact_info, so without this the
        # "Dados do contato" / conversation panel only updates on reopen.
        attr_scopes = {
            ((tc.get("args") or {}).get("scope") or "contact")
            for tc in tool_calls
            if tc.get("tool") == "set_custom_attribute" and not tc.get("skipped")
        }
        if "contact" in attr_scopes:
            full = await asyncio.to_thread(contact_repo.get_full_contact, phone)
            if full:
                await ws_manager.broadcast("contact_info_updated", {
                    "phone": phone, "info": full.get("info")})
        if "conversation" in attr_scopes:
            conv = await asyncio.to_thread(conversation_repo.get_open_for_contact, contact.id)
            if conv:
                await ws_manager.broadcast("conversation_updated", {
                    "conversation_id": conv["id"], "contact_id": contact.id,
                    "fields": {"custom_attributes": conv.get("custom_attributes")},
                    "ts": time.time()})

        # Broadcast updated contact info so the frontend refreshes name/details
        if contact_info:
            logger.info("[ToolCall] Broadcasting contact_info_updated for %s: %s", phone, contact_info)
            await ws_manager.broadcast("contact_info_updated", {
                "phone": phone,
                "info": contact_info,
            })

        # If transfer_to_human was called, broadcast alert + state updates
        if any(tc.get("tool") == "transfer_to_human" for tc in tool_calls):
            # Per-channel sound alert (plano 21): resolve the channel's setting and
            # ship it in the payload so the panel respects it even though the
            # toggle no longer lives in the global config.
            ta_enabled = bool(ai_settings.value(
                channel_id, "transfer_alert_enabled",
                settings.get("transfer_alert_enabled", True)))
            ta_duration = ai_settings.value(
                channel_id, "transfer_alert_duration",
                settings.get("transfer_alert_duration", 5))
            await ws_manager.broadcast("human_transfer_alert", {
                "phone": phone, "enabled": ta_enabled, "duration": ta_duration})
            await ws_manager.broadcast("contact_ai_toggled", {
                "phone": phone,
                "ai_enabled": False,
            })
            await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
            await ws_manager.broadcast("contact_tags_updated", {
                "phone": phone,
                "tags": list(contact.tags),
            })
            # The conversation was unassigned by the tool — push the change so the
            # row moves to the "Não atribuídas" inbox live (plano 10).
            conv = await asyncio.to_thread(
                conversation_repo.get_open_for_contact, contact.id)
            if conv:
                await ws_manager.broadcast("conversation_assigned", {
                    "conversation_id": conv["id"],
                    "contact_id": contact.id,
                    "status": conv.get("status"),
                    "assignee_user_id": None,
                    "active_agent_key": None,
                    "ai_active": conv.get("ai_active"),
                    "ts": time.time(),
                })

    # Expose broadcast_tool_calls for sandbox route
    deps.broadcast_tool_calls = _broadcast_tool_calls

    # ── Audio Transcription Delivery ──────────────────────────────

    async def _deliver_audio_transcription(phone: str, contact, transcription: str,
                                           *, channel_id: str = "default"):
        """Deliver an audio transcription based on the configured target.

        target=private → save as 'transcription' role (operator-only card in the panel)
        target=chat    → send a new WhatsApp message with the configured prefix
        """
        target = ai_settings.value(
            channel_id, "audio_transcription_target",
            settings.get("audio_transcription_target", "private"))

        if target == "chat":
            chat_prefix = ai_settings.value(
                channel_id, "audio_transcription_chat_prefix",
                settings.get("audio_transcription_chat_prefix", "")) or ""
            chat_message = f"{chat_prefix}{transcription}" if chat_prefix else transcription
            # Suppress echo-back for the message we're about to send
            sent_key = f"{channel_id}:{phone}:{chat_message[:120]}"
            state.recently_sent[sent_key] = time.time()
            send_result = await asyncio.to_thread(
                outbound.send_text, channel_id, phone, chat_message)
            if send_result.ok:
                sent_msg_id = send_result.external_msg_id or ""
                await asyncio.to_thread(
                    contact.add_message, "assistant", chat_message,
                    msg_id=sent_msg_id, status="operator")
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "channel_id": channel_id,
                    "message": {
                        "role": "assistant",
                        "content": chat_message,
                        "ts": time.time(),
                        "status": "operator",
                        "msg_id": sent_msg_id,
                    },
                })
                return
            logger.error("[Webhook] Failed to send transcription to chat for %s: %s",
                         phone, send_result.error)
            state.recently_sent.pop(sent_key, None)
            # Fall through to private so the transcription is not lost.

        # private target (or fallback after a failed chat send)
        await asyncio.to_thread(contact.add_message, "transcription", transcription)
        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "message": {
                "role": "transcription",
                "content": transcription,
                "ts": time.time(),
            },
        })

    async def _maybe_transcribe(
        media_kind: str,            # "audio" | "image" | "document"
        path: str,
        *,
        phone: str,
        source: str,                # "batch" | "echo" | "group_no_mention"
        is_group: bool = False,
        group_jid: str | None = None,
        file_name: str = "",        # document only — original filename
        mimetype: str = "",         # document only — best-effort mime hint
        channel_id: str = "default",
    ) -> str:
        """Inbound-path wrapper around the shared transcription helper.

        Delegates to ``server.transcription.maybe_transcribe`` so the gate +
        plugin hooks (``filter.transcription.should_run`` / ``.result``) live in
        one place, shared with the operator send routes. The transcription gates
        (describe image / read document / audio mode) are resolved PER CHANNEL
        (plano 21) via a settings view that overlays the channel's overrides.
        """
        return await maybe_transcribe(
            media_kind, path,
            settings=ai_settings.view(channel_id, settings),
            agent_handler=agent_handler,
            phone=phone, source=source, is_group=is_group, group_jid=group_jid,
            file_name=file_name, mimetype=mimetype,
        )

    # ── Batch Processing ──────────────────────────────────────────

    # ── Typing-Aware Orchestrator ─────────────────────────────────

    async def _wait_typing_paused(channel_id: str, phone: str, max_wait: float = 30.0):
        """Block while the contact is typing/recording. Defensive timeout to avoid hangs.

        WhatsApp emits a single `composing` event when the user starts typing and a
        `paused` event when they stop — there is no heartbeat in between. The stale
        check below is a fallback for cases where `paused` never arrives (dropped
        connection, app killed, etc.) — set generously so genuine long typing isn't cut.
        Keyed by (channel_id, phone); channels without presence simply never set it.
        """
        key = (channel_id, phone)
        start = time.time()
        while True:
            ts = state.typing_state.get(key)
            if not ts or not ts.get("active"):
                return
            # No event for 25s → assume paused (defensive)
            if time.time() - ts.get("last_ts", 0) > 25:
                logger.info("[Orchestrator] %s typing event stale, assuming paused", phone)
                state.typing_state[key] = {**ts, "active": False}
                return
            if time.time() - start > max_wait:
                logger.warning("[Orchestrator] %s typing wait timeout %.1fs", phone, max_wait)
                state.typing_state[key] = {**ts, "active": False}
                return
            await asyncio.sleep(0.3)

    async def _send_with_typing_guard(channel_id: str, phone: str, reply: str):
        """Wait for contact to stop typing, mark sending=True, then send (uncancellable phase)."""
        key = (channel_id, phone)
        await _wait_typing_paused(channel_id, phone)
        state.sending[key] = True
        try:
            await _send_reply(channel_id, phone, reply)
        finally:
            state.sending[key] = False

    async def _maybe_emit_ai_takeover(phone: str, channel_id: str):
        """Emit 'A IA assumiu o atendimento' once per conversation (plano 12 §3.3).

        Deduped por conversa: ``has_event`` checa se o card já existe no fio. Gateado
        pela config (grupo ``ai``). Best-effort — nunca quebra o envio da resposta.
        """
        def _emit():
            contact = agent_handler._get_contact(phone, channel_id=channel_id)
            if contact is None:
                return
            conv = conversation_repo.get_open_for_contact(contact.id)
            if conv is None or system_notices.has_event(conv["id"], "ai_takeover"):
                return
            system_notices.emit_conversation_notice(
                event_type="ai_takeover", conversation_id=conv["id"],
                contact_id=contact.id, phone=phone)
        try:
            await asyncio.to_thread(_emit)
        except Exception:
            logger.debug("[Webhook] ai_takeover notice failed for %s", phone)

    def _schedule_orchestrator(channel_id: str, phone: str):
        """Cancel existing orchestrator (unless mid-send) and spawn a new one."""
        key = (channel_id, phone)
        existing = state.processing_tasks.get(key)
        if existing and not existing.done():
            if state.sending.get(key):
                # Mid-send — don't cancel. The current orchestrator will spawn the next
                # cycle automatically when it finishes sending (sees pending_messages).
                return
            existing.cancel()
        state.processing_tasks[key] = asyncio.create_task(_orchestrate(channel_id, phone))

    async def _run_one_cycle(channel_id: str, phone: str, items: list[dict]):
        """One processing cycle: text batch (single LLM call) + each media item separately.

        Cancellable via task.cancel() up until the SEND phase, which is guarded by
        state.sending[(channel_id, phone)]=True so the webhook does not interrupt mid-send.
        """
        exec_id = await astart_execution(phone, "webhook")
        try:
            await atrack_step("webhook_received", {
                "phone": phone,
                "items": [
                    {k: v for k, v in it.items() if k != "audio_path" or v}
                    for it in items
                ],
            })

            contact = agent_handler._get_contact(phone, channel_id=channel_id)

            text_parts: list[str] = []
            text_msg_ids: list[str] = []
            text_reply_to: str | None = None
            media_items: list[dict] = []
            for item in items:
                if (item.get("image_path") or item.get("audio_path")
                        or item.get("media_type")):
                    media_items.append(item)
                else:
                    text_parts.append(item.get("text", ""))
                    if item.get("msg_id"):
                        text_msg_ids.append(item["msg_id"])
                    # Best-effort: the combined batch quotes the last quoted item.
                    if item.get("reply_to_msg_id"):
                        text_reply_to = item["reply_to_msg_id"]

            await atrack_step("batch_accumulated", {
                "text_count": len(text_parts),
                "media_count": len(media_items),
                "combined_preview": "\n".join(t for t in text_parts if t)[:200],
            })

            if _channel_ai_enabled(channel_id):
                msg_ids = await asyncio.to_thread(contact.mark_user_messages_as_read)
                if msg_ids:
                    for mid in msg_ids:
                        await asyncio.to_thread(outbound.mark_read, channel_id, phone, mid)
                    await ws_manager.broadcast("messages_read", {"phone": phone, "only_user": True})

            # ── Text batch ──────────────────────────────────
            if text_parts:
                combined = "\n".join(t for t in text_parts if t)
                if combined:
                    logger.info("[Batch] Processing %d text messages from %s: %s",
                                len(text_parts), phone, combined[:80])
                    last_msg_id = text_msg_ids[-1] if text_msg_ids else None
                    contact.add_message("user", combined, msg_id=last_msg_id,
                                        reply_to_msg_id=text_reply_to)
                    await emit_with_filter("message.saved", {
                        "phone": phone, "text": combined, "msg_id": last_msg_id,
                        "media_type": None, "media_path": None,
                        "is_group": contact.is_group,
                        "source": "batch_text",
                        "ts": time.time(),
                    })
                    if _channel_ai_enabled(channel_id) \
                            and _conversation_ai_active(contact):
                        if not agent_handler.api_key:
                            notice = "[WhatsBot] API key não configurada."
                            contact.add_message("system_notice", notice)
                            await ws_manager.broadcast("new_message", {
                                "phone": phone,
                                "message": {"role": "system_notice", "content": notice, "ts": time.time()},
                            })
                        else:
                            try:
                                await asyncio.to_thread(outbound.send_presence, channel_id, phone, "composing")
                                # Signal the panel the AI is working on this chat (cleared in the cycle's finally).
                                await ws_manager.broadcast("ai_typing", {"phone": phone, "channel_id": channel_id, "active": True})
                                # Cancellable LLM call
                                result = await agent_handler.aprocess_message(
                                    phone, combined,
                                    save_user_message=False, save_response=False,
                                    channel_id=channel_id)
                                if result.tool_calls:
                                    await _broadcast_tool_calls(phone, result.tool_calls, result.contact_info, channel_id=channel_id)
                                if result.reply:
                                    if result.reply.startswith("[WhatsBot]"):
                                        contact.add_message("system_notice", result.reply)
                                        await ws_manager.broadcast("new_message", {
                                            "phone": phone,
                                            "message": {"role": "system_notice", "content": result.reply, "ts": time.time()},
                                        })
                                    else:
                                        await _send_with_typing_guard(channel_id, phone, result.reply)
                                        await _maybe_emit_ai_takeover(phone, channel_id)
                            except asyncio.CancelledError:
                                raise
                            except Exception as e:
                                logger.error("[Batch] Agent error for %s: %s", phone, e)
                                await atrack_step("error", {"error": str(e), "phase": "text_processing"}, status="error")

            # ── Media items (each handled individually) ─────
            for item in media_items:
                text = item.get("text", "")
                image_path = item.get("image_path")
                audio_path = item.get("audio_path")
                document_path = (item.get("media_path")
                                 if item.get("media_type") == "document" else None)
                doc_extras = item.get("media_extras") or {}

                # media_type/media_path resolved by _extract_media; fall back to
                # the image/audio paths for items predating the typed fields.
                media_label = item.get("media_type") or ("image" if image_path else "audio")
                logger.info("[Batch] Processing %s from %s", media_label, phone)

                _saved_text = text or ("[Áudio recebido]" if audio_path else "")
                _saved_media_type = item.get("media_type") or ("image" if image_path else "audio")
                _saved_media_path = item.get("media_path") or image_path or audio_path
                contact.add_message(
                    "user", _saved_text,
                    media_type=_saved_media_type,
                    media_path=_saved_media_path,
                    msg_id=item.get("msg_id"),
                    reply_to_msg_id=item.get("reply_to_msg_id"),
                )
                await emit_with_filter("message.saved", {
                    "phone": phone, "text": _saved_text,
                    "msg_id": item.get("msg_id"),
                    "media_type": _saved_media_type,
                    "media_path": _saved_media_path,
                    "media_extras": item.get("media_extras"),
                    "is_group": contact.is_group,
                    "source": "batch_media",
                    "ts": time.time(),
                })

                transcription = ""
                if audio_path:
                    transcription = await _maybe_transcribe(
                        "audio", audio_path,
                        phone=phone, source="batch",
                        is_group=contact.is_group,
                        group_jid=phone if contact.is_group else None,
                        channel_id=channel_id,
                    )
                elif image_path:
                    transcription = await _maybe_transcribe(
                        "image", image_path,
                        phone=phone, source="batch",
                        is_group=contact.is_group,
                        group_jid=phone if contact.is_group else None,
                        channel_id=channel_id,
                    )
                elif document_path:
                    transcription = await _maybe_transcribe(
                        "document", document_path,
                        phone=phone, source="batch",
                        is_group=contact.is_group,
                        group_jid=phone if contact.is_group else None,
                        file_name=doc_extras.get("file_name") or "",
                        mimetype=doc_extras.get("mimetype") or "",
                        channel_id=channel_id,
                    )

                if transcription:
                    if audio_path:
                        new_content = f"[Transcrição do áudio]: {transcription}"
                    elif image_path:
                        desc_prefix = f"[Descrição da imagem]: {transcription}"
                        new_content = f"{desc_prefix}\n{text}" if text else desc_prefix
                    elif document_path:
                        doc_prefix = f"[Conteúdo do documento]: {transcription}"
                        new_content = f"{text}\n{doc_prefix}" if text else doc_prefix
                    else:
                        new_content = None
                    if new_content:
                        await asyncio.to_thread(
                            agent_handler.update_last_user_message_content, phone, new_content
                        )
                    if audio_path:
                        await _deliver_audio_transcription(phone, contact, transcription,
                                                           channel_id=channel_id)
                    else:
                        # Image/document content — delivered as a private panel card.
                        contact.add_message("transcription", transcription)
                        await ws_manager.broadcast("new_message", {
                            "phone": phone,
                            "message": {
                                "role": "transcription",
                                "content": transcription,
                                "ts": time.time(),
                            },
                        })

                if not _channel_ai_enabled(channel_id) \
                        or not _conversation_ai_active(contact):
                    continue

                if not agent_handler.api_key:
                    notice = "[WhatsBot] API key não configurada."
                    contact.add_message("system_notice", notice)
                    await ws_manager.broadcast("new_message", {
                        "phone": phone,
                        "message": {"role": "system_notice", "content": notice, "ts": time.time()},
                    })
                    continue

                llm_text = text or ""
                if audio_path:
                    if transcription:
                        llm_text = f"[Transcrição do áudio]: {transcription}"
                    else:
                        llm_text = llm_text or "[Áudio recebido]"
                elif image_path and transcription:
                    prefix = f"[Descrição da imagem]: {transcription}"
                    llm_text = f"{prefix}\n{text}" if text else prefix
                elif document_path and transcription:
                    doc_prefix = f"[Conteúdo do documento]: {transcription}"
                    llm_text = f"{text}\n{doc_prefix}" if text else doc_prefix

                try:
                    await asyncio.to_thread(outbound.send_presence, channel_id, phone, "composing")
                    await ws_manager.broadcast("ai_typing", {"phone": phone, "channel_id": channel_id, "active": True})
                    result = await agent_handler.aprocess_message(
                        phone,
                        llm_text,
                        save_user_message=False, save_response=False,
                        image_path=image_path if not transcription else None,
                        channel_id=channel_id,
                    )
                    if result.tool_calls:
                        await _broadcast_tool_calls(phone, result.tool_calls, result.contact_info, channel_id=channel_id)
                    if result.reply:
                        if result.reply.startswith("[WhatsBot]"):
                            contact.add_message("system_notice", result.reply)
                            await ws_manager.broadcast("new_message", {
                                "phone": phone,
                                "message": {"role": "system_notice", "content": result.reply, "ts": time.time()},
                            })
                        else:
                            await _send_with_typing_guard(channel_id, phone, result.reply)
                            await _maybe_emit_ai_takeover(phone, channel_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("[Batch] Agent error for %s (%s): %s", phone, media_label, e)
                    await atrack_step("error", {"error": str(e), "phase": f"{media_label}_processing"}, status="error")

            await aend_execution(exec_id)
        except asyncio.CancelledError:
            await aend_execution(exec_id, error="cancelled")
            raise
        except Exception as exc:
            await aend_execution(exec_id, error=str(exc))
        finally:
            # Always clear the "IA respondendo" hint (success, cancel, or error),
            # even if it was never set (AI off for this chat) — a no-op then.
            await ws_manager.broadcast("ai_typing", {"phone": phone, "channel_id": channel_id, "active": False})

        max_exec = settings.get("max_executions", 200)
        try:
            await asyncio.to_thread(prune_executions, max_exec)
        except Exception:
            pass

    async def _orchestrate(channel_id: str, phone: str):
        """Typing-aware batch orchestrator: wait → batch_delay → wait → cycle.

        Phases (each cancellable except the final SEND inside _run_one_cycle):
          1. Wait until contact stops typing (defensive 30s timeout)
          2. Sleep for the configured batch_delay
          3. Wait again (typing may have resumed during the sleep)
          4. Snapshot pending and run the LLM + send cycle

        Cancellation by the webhook (new message arrived) drops the current run; the
        webhook then schedules a fresh orchestrator that picks up the new pending list.
        Keyed by (channel_id, phone) so concurrent channels never collide.
        """
        key = (channel_id, phone)
        try:
            batch_delay = ai_settings.value(
                channel_id, "message_batch_delay",
                settings.get("message_batch_delay", 3.0))
            await _wait_typing_paused(channel_id, phone)
            await asyncio.sleep(batch_delay)
            await _wait_typing_paused(channel_id, phone)

            items = list(state.pending_messages.get(key, []))
            if not items:
                return
            # Consume now: a NEW message arriving during _run_one_cycle goes into a fresh batch
            state.pending_messages.pop(key, None)

            # Modo sequencial (plano 21): quando LIGADO no canal, só UM contato
            # roda o ciclo da IA por vez nesse canal. Um asyncio.Lock por canal
            # serializa o ciclo inteiro (LLM + envio) e espaça respostas
            # consecutivas por ``ai_sequential_delay`` segundos (mínimo 2s, sem
            # teto), reduzindo o risco de bloqueio da Meta quando o número
            # responde a vários clientes em paralelo. O toggle é per-canal
            # (``ai_sequential_enabled``); o default herdado é LIGADO, preservando
            # o comportamento legado sempre-ativo para canais sem o override.
            sequential_on = bool(ai_settings.value(
                channel_id, "ai_sequential_enabled", True))
            if sequential_on:
                lock = state.channel_ai_locks.get(channel_id)
                if lock is None:
                    lock = asyncio.Lock()
                    state.channel_ai_locks[channel_id] = lock
                async with lock:
                    delay = float(ai_settings.value(
                        channel_id, "ai_sequential_delay", 2.0) or 0)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await _run_one_cycle(channel_id, phone, items)
            else:
                await _run_one_cycle(channel_id, phone, items)

            # If new messages arrived during the SEND phase (when cancellation is blocked),
            # spawn another orchestrator so they get processed.
            if state.pending_messages.get(key):
                state.processing_tasks[key] = asyncio.create_task(_orchestrate(channel_id, phone))
        except asyncio.CancelledError:
            return
        finally:
            cur = asyncio.current_task()
            if state.processing_tasks.get(key) is cur:
                state.processing_tasks.pop(key, None)

    # ── Provider-agnostic ingress (plano 11 Fase 0/2) ─────────────

    async def _resolve_inbound_media(event: InboundEvent):
        """For push providers that only ship a media id (Cloud P16), download +
        cache the file so the existing transcription/render pipeline works.

        Returns ``(media_path, image_path, audio_path)`` — image/audio locals feed
        the per-kind branches in ``_run_one_cycle``. No-op when the event already
        carries a local ``media_path`` (GOWA auto-download)."""
        media_path = event.media_path
        if event.media_type and not media_path:
            extras = event.media_extras or {}
            media_id = extras.get("media_id")
            inst = channel_registry.get(event.channel_id) if channel_registry else None
            if media_id and inst is not None and hasattr(inst, "download_media"):
                try:
                    fname = await asyncio.to_thread(
                        inst.download_media, media_id, media_dir,
                        mime_type=extras.get("mime_type"))
                    if fname:
                        media_path = f"statics/media/{fname}"
                except Exception:
                    logger.warning("[Ingest] media download failed for %s", media_id,
                                   exc_info=True)
        image_path = media_path if event.media_type == "image" else None
        audio_path = media_path if event.media_type == "audio" else None
        return media_path, image_path, audio_path

    def _apply_contact_metadata(contact, event: InboundEvent) -> None:
        """Persist provider-resolved chat metadata onto the contact (plano 13 Fase 0).

        GOWA resolves a group's name / can-send / archive status from its client and
        carries them on the event; other providers leave them ``None`` (no-op).
        Sync — call via ``asyncio.to_thread``."""
        changed = False
        if event.is_group:
            if event.group_name and getattr(contact, "group_name", None) != event.group_name:
                contact.is_group = True
                contact.group_name = event.group_name
                changed = True
            if event.can_send is not None and getattr(contact, "can_send", None) != event.can_send:
                contact.can_send = event.can_send
                changed = True
        if (event.is_archived is not None
                and not getattr(contact, "archived_by_app", False)
                and getattr(contact, "is_archived", None) != event.is_archived):
            contact.is_archived = event.is_archived
            changed = True
        if changed:
            contact.save()

    async def _ingest_echo(event: InboundEvent, channel_id: str, phone: str):
        """Sync a message sent from the user's own device (``direction='out'``).

        Mirror of the GOWA webhook's ``is_from_me`` branch, provider-agnostic: save
        as an operator message + broadcast + ``message.sent`` (source echo), honoring
        ``filter.message.outgoing``. (Outgoing-audio transcription is a follow-up.)"""
        msg_id = event.external_msg_id or str(uuid.uuid4())
        dedup_key = f"{channel_id}:{event.external_msg_id}" if event.external_msg_id else None
        if dedup_key:
            if dedup_key in state.processed_messages:
                return
            state.processed_messages.add(dedup_key)
        text = (event.text or "").strip()
        media_type = event.media_type
        media_path, _img, _aud = await _resolve_inbound_media(event)
        media_extras = event.media_extras or None
        if not text and not media_type:
            return
        # Suppress the echo of a message WE just sent through the app.
        if text:
            sent_key = f"{channel_id}:{phone}:{text[:120]}"
            sent_at = state.recently_sent.pop(sent_key, None)
            if sent_at and (time.time() - sent_at) < 30:
                logger.info("[Ingest] Ignoring echo-back for %s/%s", channel_id, phone)
                return

        outgoing_msg = {
            "phone": phone, "channel_id": channel_id, "text": text, "msg_id": msg_id,
            "media_type": media_type, "media_path": media_path, "media_extras": media_extras,
            "reply_to_msg_id": event.reply_to_msg_id, "is_from_me": True,
            "source": "echo", "raw": event.raw or {}, "ts": event.ts or time.time(),
        }
        outgoing_msg = await apply_filter(
            "filter.message.outgoing", outgoing_msg, {"phone": phone})
        if outgoing_msg is None:
            logger.info("[Ingest] outgoing echo from %s filtered out", phone)
            return
        text = outgoing_msg.get("text", text)
        msg_id = outgoing_msg.get("msg_id", msg_id)
        media_type = outgoing_msg.get("media_type", media_type)
        media_path = outgoing_msg.get("media_path", media_path)
        media_extras = outgoing_msg.get("media_extras", media_extras)
        reply_to = outgoing_msg.get("reply_to_msg_id", event.reply_to_msg_id)

        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        await asyncio.to_thread(
            contact.add_message, "assistant", text,
            media_type=media_type, media_path=media_path, msg_id=msg_id,
            reply_to_msg_id=reply_to, status="operator")
        broadcast_msg: dict = {"role": "assistant", "content": text, "ts": time.time(),
                               "msg_id": msg_id, "status": "operator"}
        if reply_to:
            broadcast_msg["reply_to_msg_id"] = reply_to
        if media_type:
            broadcast_msg["media_type"] = media_type
            broadcast_msg["media_path"] = media_path
        await ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": channel_id, "message": broadcast_msg})
        await emit_with_filter("message.sent", {
            "phone": phone, "channel_id": channel_id, "text": text, "msg_id": msg_id,
            "media_type": media_type, "media_path": media_path, "media_extras": media_extras,
            "source": "echo", "status": "operator", "ts": time.time(),
        })

    async def ingest_event(event: InboundEvent):
        """Single ingress for ANY channel's inbound message (plano 11 / plano 13).

        Mirrors the GOWA webhook's pre-batch steps for a canonical ``InboundEvent``
        (dedup, contact resolution, broadcast, plugin filter/event) and feeds the
        SAME typing-aware orchestrator, keyed by ``(channel_id, chat_id)``. The
        reply is later routed back out through the channel's own adapter. GOWA
        enrichment (group @mention prefix → ``display_text``, ``trigger_ai`` gate
        for group-no-mention, echo via ``direction='out'``, group/archive metadata)
        is carried on the event; defaults keep Cloud/Telegram identical.
        """
        if getattr(event, "kind", "message") != "message":
            return
        channel_id = event.channel_id or "default"
        phone = event.chat_id or event.sender_id
        if not phone:
            return

        # Echo of a message sent from the user's own device (plano 13 Fase 0).
        if getattr(event, "direction", "in") == "out":
            await _ingest_echo(event, channel_id, phone)
            return

        # Idempotency by (channel_id, external_msg_id) — providers re-deliver (P18).
        msg_id = event.external_msg_id or str(uuid.uuid4())
        dedup_key = f"{channel_id}:{event.external_msg_id}" if event.external_msg_id else None
        if dedup_key:
            if dedup_key in state.processed_messages:
                return
            state.processed_messages.add(dedup_key)

        text = (event.text or "").strip()
        # display_text = operator/LLM-facing text (GOWA group "[Nome]: …"); falls
        # back to text for providers that don't set it.
        display_text = (event.display_text or text)
        media_type = event.media_type
        media_path, image_path, audio_path = await _resolve_inbound_media(event)
        media_extras = event.media_extras or None
        reply_to = event.reply_to_msg_id

        if not text and not media_type:
            return

        # Echo suppression (mirror of GOWA): drop a message we just sent out.
        if text:
            sent_key = f"{channel_id}:{phone}:{text[:120]}"
            sent_at = state.recently_sent.pop(sent_key, None)
            if sent_at and (time.time() - sent_at) < 30:
                logger.info("[Ingest] Ignoring echo-back for %s/%s", channel_id, phone)
                return

        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        if event.sender_name and not event.is_group and not contact.is_group:
            await asyncio.to_thread(contact.set_wa_name, event.sender_name)
        # Provider-resolved chat metadata (group name / can-send / archive).
        await asyncio.to_thread(_apply_contact_metadata, contact, event)
        await asyncio.to_thread(contact.increment_unread, msg_id)
        if event.is_group and event.mentioned:
            await asyncio.to_thread(contact.mark_mention)

        logger.info("[Ingest] %s/%s: %s", channel_id, phone,
                    display_text[:80] if display_text else f"[{media_type}]")

        parsed_msg = {
            "phone": phone,
            "channel_id": channel_id,
            "name": event.sender_name,
            "text": display_text,
            "raw_text": text,
            "msg_id": msg_id,
            "reply_to_msg_id": reply_to,
            "media_type": media_type,
            "media_path": media_path,
            "media_extras": media_extras,
            "is_group": event.is_group,
            "group_jid": event.chat_id if event.is_group else None,
            "individual_phone": event.sender_id if event.is_group else None,
            "is_from_me": False,
            "raw": event.raw or {},
            "ts": event.ts or time.time(),
        }
        parsed_msg = await apply_filter(
            "filter.message.before_save", parsed_msg, {"phone": phone})
        if parsed_msg is None:
            logger.info("[Ingest] inbound from %s filtered out before save", phone)
            return
        display_text = parsed_msg.get("text", display_text)
        msg_id = parsed_msg.get("msg_id", msg_id)
        reply_to = parsed_msg.get("reply_to_msg_id", reply_to)
        media_type = parsed_msg.get("media_type", media_type)
        media_path = parsed_msg.get("media_path", media_path)
        media_extras = parsed_msg.get("media_extras", media_extras)

        broadcast_msg: dict = {"role": "user", "content": display_text,
                               "ts": time.time(), "msg_id": msg_id}
        if reply_to:
            broadcast_msg["reply_to_msg_id"] = reply_to
        if media_type:
            broadcast_msg["media_type"] = media_type
            broadcast_msg["media_path"] = media_path
        if event.is_group and event.mentioned:
            broadcast_msg["mentioned"] = True
        await ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": channel_id, "message": broadcast_msg})

        await emit_with_filter("message.received", parsed_msg)

        # Group message with no @mention (group_reply_mode): save to history +
        # message.saved, but do NOT run the agent (plano 13 Fase 0 — trigger_ai).
        if not getattr(event, "trigger_ai", True):
            await asyncio.to_thread(
                contact.add_message, "user", display_text,
                media_type=media_type, media_path=media_path,
                msg_id=msg_id, reply_to_msg_id=reply_to)
            await emit_with_filter("message.saved", {
                "phone": phone, "channel_id": channel_id, "text": display_text,
                "msg_id": msg_id, "media_type": media_type, "media_path": media_path,
                "media_extras": media_extras, "is_group": event.is_group,
                "group_jid": event.chat_id if event.is_group else None,
                "source": "group_no_mention", "ts": time.time(),
            })
            return

        key = (channel_id, phone)
        # A real message proves the contact finished typing — clear any stale
        # `composing` flag so the orchestrator doesn't block on it until the 25s
        # stale timeout (mirror of the legacy GOWA handler; no-op for channels
        # without presence, which never set typing_state).
        ts = state.typing_state.get(key)
        if ts and ts.get("active"):
            state.typing_state[key] = {**ts, "active": False}
        state.pending_messages.setdefault(key, []).append({
            "text": display_text,
            "image_path": image_path if event.media_type == "image" else None,
            "audio_path": audio_path if event.media_type == "audio" else None,
            "media_type": media_type,
            "media_path": media_path,
            "media_extras": media_extras,
            "msg_id": msg_id,
            "reply_to_msg_id": reply_to,
        })
        _schedule_orchestrator(channel_id, phone)

        # Bound the dedup + recently-sent sets (mirror of the legacy GOWA handler).
        if len(state.processed_messages) > 5000:
            for item in list(state.processed_messages)[:2500]:
                state.processed_messages.discard(item)
        now = time.time()
        for k in [k for k, v in state.recently_sent.items() if now - v > 60]:
            state.recently_sent.pop(k, None)

    # Expose the ingress for the per-channel webhook route (Cloud/Telegram/…).
    deps.ingest_event = ingest_event

    # ── Webhook Endpoint ──────────────────────────────────────────

    @app.post("/api/webhook")
    async def webhook(body: dict):
        """Receive real-time message events from GOWA webhook."""
        # Plugin filter: full webhook payload before any parsing.
        # A plugin can rewrite media paths, drop messages or normalize fields.
        body = await apply_filter("filter.webhook.payload", body, {})
        if body is None:
            return _ok({"status": "filtered_out"})

        event = body.get("event", "")
        # GOWA wraps message data inside "payload"
        data = body.get("payload", body.get("data", body))

        # Store raw payload for debugging (last 50, in-memory fallback)
        state.webhook_payloads.append({
            "ts": time.time(),
            "event": event,
            "payload": data,
        })

        # Helper to extract a clean phone from a JID-ish string.
        def _phone_from_jid(jid: str) -> str:
            if not isinstance(jid, str) or not jid:
                return ""
            return jid.split("@")[0].split(":")[0]

        # ── Events GOWA emite que historicamente o WhatsBot ignorava ─────
        # Cada um vira um plugin event com payload tipado. O bot não age
        # localmente nestes (nenhum LLM, nenhum save), só fan-outa pros plugins.

        if event == "message.reaction":
            react_phone = _phone_from_jid(data.get("chat_id", "") or data.get("from", ""))
            react_from = _phone_from_jid(data.get("from", ""))
            reacted_id = data.get("reacted_message_id", "")
            emoji = data.get("reaction", "")
            is_from_me = bool(data.get("is_from_me", False))
            # Persist so reactions made on the phone / by the contact reflect in the
            # panel. Reactor "me" for the bot's own account, else the sender's phone.
            if reacted_id:
                reactor = "me" if is_from_me else (react_from or react_phone)
                reactions = await asyncio.to_thread(
                    message_repo.set_reaction, reacted_id, emoji, reactor)
                if reactions is not None:
                    await ws_manager.broadcast("message_reaction", {
                        "phone": react_phone, "msg_id": reacted_id, "reactions": reactions,
                    })
            await emit_with_filter("message.reaction", {
                "id": data.get("id", ""),
                "phone": react_phone,
                "from": react_from,
                "reaction": emoji,
                "reacted_message_id": reacted_id,
                "is_from_me": is_from_me,
                "ts": data.get("timestamp") or time.time(),
                "raw": data,
            })
            return _ok({"status": "reaction"})

        if event == "message.edited":
            await emit_with_filter("message.edited", {
                "id": data.get("id", ""),
                "phone": _phone_from_jid(data.get("chat_id", "") or data.get("from", "")),
                "from": _phone_from_jid(data.get("from", "")),
                "original_message_id": data.get("original_message_id", ""),
                "body": data.get("body", "") or data.get("content", ""),
                "is_from_me": bool(data.get("is_from_me", False)),
                "ts": data.get("timestamp") or time.time(),
                "raw": data,
            })
            return _ok({"status": "edited"})

        if event == "message.revoked":
            revoked_phone = _phone_from_jid(data.get("chat_id", "") or data.get("from", ""))
            revoked_id = data.get("revoked_message_id", "")
            # Flag as revoked (keeping content) so a revoke done on the phone/by the
            # contact reflects in the panel and survives reload, without losing the text.
            if revoked_id:
                matched = await asyncio.to_thread(message_repo.mark_revoked, revoked_id, "all")
                if matched:
                    await ws_manager.broadcast("message_revoked", {
                        "phone": revoked_phone, "msg_id": revoked_id,
                    })
            await emit_with_filter("message.revoked", {
                "id": data.get("id", ""),
                "phone": revoked_phone,
                "from": _phone_from_jid(data.get("from", "")),
                "revoked_message_id": revoked_id,
                "revoked_from_me": bool(data.get("revoked_from_me", False)),
                "revoked_chat": data.get("revoked_chat", ""),
                "ts": data.get("timestamp") or time.time(),
                "raw": data,
            })
            return _ok({"status": "revoked"})

        if event == "message.deleted":
            deleted_phone = _phone_from_jid(data.get("chat_id", "") or data.get("from", ""))
            deleted_id = data.get("deleted_message_id", "")
            # Keep the row (flag revoked) instead of hard-deleting, so a "delete for
            # me" done on the phone leaves the message visible in the panel.
            if deleted_id:
                matched = await asyncio.to_thread(message_repo.mark_revoked, deleted_id, "me")
                if matched:
                    await ws_manager.broadcast("message_deleted", {
                        "phone": deleted_phone, "msg_id": deleted_id,
                    })
            await emit_with_filter("message.deleted", {
                "deleted_message_id": deleted_id,
                "phone": deleted_phone,
                "from": _phone_from_jid(data.get("from", "")),
                "original_content": data.get("original_content", ""),
                "original_sender": data.get("original_sender", ""),
                "original_timestamp": data.get("original_timestamp"),
                "was_from_me": bool(data.get("was_from_me", False)),
                "ts": data.get("timestamp") or time.time(),
                "raw": data,
            })
            return _ok({"status": "deleted"})

        if event == "group.participants":
            chat_id = data.get("chat_id", "")
            ctype = data.get("type", "")
            jids = data.get("jids", []) or []
            # Apply the roster delta locally (join adds + resolves push name,
            # leave drops the member) and push the authoritative list to the open
            # panel so its @mention autocomplete updates immediately — a removed
            # member disappears and a just-joined one shows with its name, without
            # depending on a possibly-stale GOWA /group/info refetch.
            if chat_id:
                members = await asyncio.to_thread(
                    group_mentions.apply_participants_change, chat_id, ctype, jids)
                await ws_manager.broadcast("group_participants_changed",
                                           {"group_jid": chat_id, "members": members})
                # Surface a join/leave/promote notice in the chat timeline (only
                # for groups already tracked, to avoid materializing phantom
                # contacts). Saved as `system_notice`: rendered as a centered
                # bubble and excluded from the LLM context.
                existing = await asyncio.to_thread(contact_repo.get_by_phone, chat_id)
                if existing:
                    notice = await asyncio.to_thread(
                        group_mentions.describe_change, ctype, jids)
                    if notice:
                        contact_obj = agent_handler._get_contact(chat_id)
                        await asyncio.to_thread(
                            contact_obj.add_message, "system_notice", notice)
                        await ws_manager.broadcast("new_message", {
                            "phone": chat_id,
                            "message": {"role": "system_notice", "content": notice,
                                        "ts": time.time()},
                        })
            await emit_with_filter("group.participants_changed", {
                "chat_id": chat_id,
                "phone": _phone_from_jid(chat_id),
                "type": ctype,
                "jids": jids,
                "ts": time.time(),
                "raw": data,
            })
            return _ok({"status": "group_participants"})

        if event == "group.joined":
            await emit_with_filter("group.joined", {
                "chat_id": data.get("chat_id", "") or data.get("group_jid", ""),
                "phone": _phone_from_jid(data.get("chat_id", "") or data.get("group_jid", "")),
                "ts": time.time(),
                "raw": data,
            })
            return _ok({"status": "group_joined"})

        if event == "call.offer":
            await emit_with_filter("call.received", {
                "call_id": data.get("call_id", ""),
                "phone": _phone_from_jid(data.get("from", "")),
                "auto_rejected": bool(data.get("auto_rejected", False)),
                "remote_platform": data.get("remote_platform", ""),
                "remote_version": data.get("remote_version", ""),
                "group_jid": data.get("group_jid"),
                "ts": time.time(),
                "raw": data,
            })
            return _ok({"status": "call"})

        if isinstance(event, str) and event.startswith("newsletter."):
            await emit_with_filter("newsletter.event", {
                "subtype": event,
                "ts": time.time(),
                "raw": data,
            })
            return _ok({"status": "newsletter"})

        # Handle chat presence events (typing/recording indicators)
        if event == "chat_presence":
            from_jid = data.get("from", "")
            phone = from_jid.split("@")[0] if "@" in from_jid else from_jid
            presence_state = data.get("state", "")
            media = data.get("media", "") or "text"
            if phone and presence_state:
                logger.info("[Webhook] chat_presence %s from %s (media=%s)",
                            presence_state, phone, media)
                # Update orchestrator-visible typing state (GOWA = "default" channel)
                state.typing_state[("default", phone)] = {
                    "active": presence_state == "composing",
                    "media": media,
                    "last_ts": time.time(),
                }
                # GOWA presence is scoped to the "default" channel. Resolve the exact
                # GOWA conversation so the frontend marks ONLY that conversation as
                # "digitando" — not every conversation sharing this phone (plano 11).
                # Matching by conversation_id is unambiguous (channel_id strings /
                # LID-vs-phone can diverge). Cached (TTL 30s) — presence is frequent.
                conv_id = await asyncio.to_thread(_resolve_presence_conv_id, phone)
                await ws_manager.broadcast("chat_presence", {
                    "phone": phone,
                    "channel_id": "default",
                    "conversation_id": conv_id,
                    "state": presence_state,
                    "media": media,
                })
                await emit_with_filter("presence.changed", {
                    "phone": phone,
                    "state": presence_state,
                    "media": media,
                    "ts": time.time(),
                })
            return _ok({"status": "presence"})

        # Handle message.ack events (delivery + read receipts from WhatsApp)
        if event == "message.ack":
            receipt_type = data.get("receipt_type", "")
            msg_ids = data.get("ids", [])

            # Extract phone from ack payload (try multiple fields, GOWA is inconsistent)
            ack_phone = ""
            for field in ("chat_id", "from", "jid", "phone"):
                val = data.get(field, "")
                if val and "@" in val:
                    ack_phone = val.split("@")[0]
                    break
                elif val and not ack_phone:
                    ack_phone = val

            # Fallback: look up phone from the message in DB
            if not ack_phone and msg_ids:
                cid = await asyncio.to_thread(message_repo.get_contact_id_by_msg_id, msg_ids[0])
                if cid:
                    # _contacts is keyed by (channel_id, phone) — plano 11.
                    for (_ch, phone_key), contact in agent_handler._contacts.items():
                        if contact.id == cid:
                            ack_phone = phone_key
                            break

            if receipt_type == "delivered" and msg_ids:
                # Update outgoing message status to "delivered" (with cascade to prior msgs)
                all_updated = []
                for mid in msg_ids:
                    updated = await asyncio.to_thread(message_repo.update_status_by_msg_id, mid, "delivered")
                    all_updated.extend(updated)
                # Deduplicate
                all_updated = list(dict.fromkeys(all_updated))
                logger.info("[Webhook] message.ack delivered for %s (ids=%s, cascaded=%d)",
                            ack_phone, msg_ids, len(all_updated))
                if ack_phone and all_updated:
                    await ws_manager.broadcast("message_status", {
                        "phone": ack_phone,
                        "msg_ids": all_updated,
                        "status": "delivered",
                    })
                    await emit_with_filter("receipt.changed", {
                        "phone": ack_phone,
                        "msg_ids": all_updated,
                        "status": "delivered",
                        "ts": time.time(),
                    })

            elif receipt_type in ("read", "read-self") and msg_ids:
                # Update outgoing message status to "read" (with cascade to prior msgs)
                all_updated = []
                for mid in msg_ids:
                    updated = await asyncio.to_thread(message_repo.update_status_by_msg_id, mid, "read")
                    all_updated.extend(updated)
                all_updated = list(dict.fromkeys(all_updated))
                logger.info("[Webhook] message.ack read for %s (ids=%s, cascaded=%d)",
                            ack_phone, msg_ids, len(all_updated))
                if ack_phone and all_updated:
                    await ws_manager.broadcast("message_status", {
                        "phone": ack_phone,
                        "msg_ids": all_updated,
                        "status": "read",
                    })
                    await emit_with_filter("receipt.changed", {
                        "phone": ack_phone,
                        "msg_ids": all_updated,
                        "status": "read",
                        "ts": time.time(),
                    })

                # Existing unread tracking logic (for incoming messages read by us)
                for (_ch, phone_key), contact in agent_handler._contacts.items():
                    unread_ids = contact.get_unread_msg_ids()
                    matched = [mid for mid in msg_ids if mid in unread_ids]
                    if matched:
                        logger.info("[Webhook] message.ack unread cleared for %s (ids=%s)", phone_key, matched)
                        contact.mark_as_read()
                        await ws_manager.broadcast("messages_read", {"phone": phone_key})

            return _ok({"status": "ack"})

        # Only process incoming messages
        if event and event not in ("message", "message:received", ""):
            return _ok({"status": "ignored"})

        if not isinstance(data, dict):
            return _ok({"status": "ignored"})

        # Extract message fields (GOWA field names vary)
        is_from_me = data.get("is_from_me", data.get("from_me", data.get("FromMe", False)))

        # Capture bot's own phone from outgoing messages (for @mention detection)
        if is_from_me:
            own_jid = (data.get("sender_jid", "") or data.get("from", "")
                       or data.get("sender", ""))
            if own_jid and "@s.whatsapp.net" in own_jid:
                captured = own_jid.split("@")[0].split(":")[0]
                if captured and captured != state.bot_phone:
                    state.bot_phone = captured
                    logger.info("[Webhook] Bot phone captured from own message: %s", state.bot_phone)
                    # Persist + register so @mention detection in groups keeps
                    # working across restarts, even before the status poll runs.
                    try:
                        if settings.get("bot_phone", "") != state.bot_phone:
                            settings.set("bot_phone", state.bot_phone)
                        group_mentions.set_bot_identity(
                            state.bot_phone, state.bot_name)
                    except Exception as e:
                        logger.warning("[Webhook] Failed to persist bot_phone: %s", e)

        msg_id = data.get("id", data.get("Id", data.get("message_id", ""))
                         ) or str(uuid.uuid4())
        if msg_id in state.processed_messages:
            return _ok({"status": "duplicate"})

        # Best-effort: GOWA's webhook is inconsistent about exposing the quoted
        # message id. Probe a few known/likely keys (flat + nested context_info).
        reply_to_msg_id = _extract_reply_to(data)

        # Extract body — try multiple known field names
        text = (data.get("content", "")
                or data.get("body", "")
                or data.get("Body", "")
                or data.get("message", "")
                or data.get("text", "")).strip()

        # Extract media (image, audio, video_note→audio, video, sticker,
        # document, location, live_location, poll, interactive, order,
        # product, contact, contacts_array) — see _extract_media for the
        # detection order and the shape of the returned dict.
        extracted = _extract_media(
            data, is_from_me=is_from_me, existing_text=text
        )
        text = extracted["text"]
        media_type: str | None = extracted["media_type"]
        media_path: str | None = extracted["media_path"]
        media_extras: dict | None = extracted["media_extras"]
        # Back-compat locals used by downstream code that decides on
        # specific kinds (e.g. picks transcribe_audio vs describe_image,
        # branches on audio_path in the batch loop).
        audio_path: str | None = extracted["audio_path"]
        image_path: str | None = extracted["image_path"]
        document_path: str | None = extracted["document_path"]
        document_name: str | None = extracted["document_name"]

        # Extract chat and sender separately for group support.
        # GOWA v8.5.0 puts the chat in `chat_id` and the actual sender in `from`
        # (in private chats they're the same JID; in groups they differ — `from`
        # is the member who sent it, `chat_id` is the group). The legacy
        # `sender_jid`/`sender` fields aren't always present.
        chat_jid = (data.get("chat_jid", "") or data.get("chat_id", "")
                    or data.get("from", "") or data.get("jid", ""))
        sender_jid = (data.get("sender_jid", "") or data.get("sender", "")
                      or data.get("from", ""))

        # Classify the chat by its JID suffix — the only reliable discriminator
        # (the 120363… numeric prefix is shared by group/channel/community). Drop
        # types the GOWA channel isn't configured to surface BEFORE any contact
        # is materialized; otherwise a @newsletter/@broadcast/@bot JID falls into
        # the "person" branch and becomes a phantom contact. Default config keeps
        # people + groups and drops channels/status/bots. Unknown suffixes are
        # never gated (preserves legacy behaviour). Applies to inbound AND the
        # is_from_me echo branch below.
        jid_type = jid_classifier.classify_jid(chat_jid)
        is_group = jid_type == jid_classifier.GROUP
        allowed = await _gowa_allowed_jid_types()
        if not jid_classifier.is_allowed(jid_type, allowed):
            logger.info(
                "[Webhook] Skipping %s message (jid=%s, type=%s not in allowed=%s)",
                "outgoing" if is_from_me else "incoming", chat_jid, jid_type, allowed)
            return _ok({"status": "ignored_jid_type"})

        # ── Resolve document filename ─────────────────────────────────
        # GOWA's webhook omits the original filename for documents when
        # auto-download is enabled (buildAutoDownloadPayload sends only
        # path+caption, and the on-disk path is UUID-based). GOWA *does*
        # persist `filename` in its chat storage BEFORE forwarding the
        # webhook, so we look it up via GET /chat/{jid}/messages.
        if media_type == "document":
            try:
                real_name = await asyncio.to_thread(
                    gowa_client.get_message_filename, chat_jid, msg_id)
            except Exception as e:
                logger.warning("[Webhook] document filename lookup failed: %s", e)
                real_name = ""
            doc_caption = (media_extras or {}).get("caption") or ""
            doc_label = real_name or document_name or "documento"
            verb = "enviado" if is_from_me else "recebido"
            text = (f"[Documento {verb}: {doc_label}]"
                    + (f"\n{doc_caption}" if doc_caption else ""))
            if real_name:
                media_extras = {**(media_extras or {}), "file_name": real_name}
                logger.info("[Webhook] document filename resolved: %s", real_name)

        if is_group:
            # For groups: route replies to the group, track individual sender
            phone = chat_jid  # keep full JID (e.g. 120363xxx@g.us)
            # Strip @domain AND :device suffix from sender JID (multi-device WhatsApp
            # sends "5511999999:25@s.whatsapp.net"; we want just the bare phone).
            individual_phone = (sender_jid.split("@")[0].split(":")[0]
                                if sender_jid else "")
            from_name = data.get("from_name", "") or data.get("pushName", "") or data.get("notify", "")
        else:
            # For private chats: the conversation key is `chat_id` (the other party in
            # both directions). For incoming msgs `sender_jid == chat_id`; for outgoing
            # (`is_from_me=true`) `sender_jid`/`from` is the bot itself, so using sender
            # would route the message to the bot's own contact thread.
            conv_jid = chat_jid or sender_jid
            phone = (conv_jid.split("@")[0].split(":")[0] if conv_jid else "")
            individual_phone = phone
            from_name = data.get("from_name", "") or data.get("pushName", "") or data.get("notify", "")

        if not phone or (not text and not media_type):
            # Last-chance plugin hook: a filter may detect a media kind the
            # core doesn't natively understand and turn it into a synthetic
            # parsed_msg. Returning ``None`` (the default when no plugin
            # subscribes) lets the legacy "ignored" path proceed.
            synthetic = await apply_filter(
                "filter.media.unknown",
                None,
                {"phone": phone, "raw": data},
            )
            if isinstance(synthetic, dict):
                media_type = synthetic.get("media_type") or media_type
                media_path = synthetic.get("media_path") or media_path
                media_extras = synthetic.get("media_extras") or media_extras
                if synthetic.get("text"):
                    text = synthetic["text"]
            if not phone or (not text and not media_type):
                logger.info(
                    "[Webhook] Skipping: text=%r phone=%r media=%s keys=%s payload=%s",
                    text[:50] if text else "", phone, media_type or "none",
                    list(data.keys()), str(data)[:1000],
                )
                return _ok({"status": "ignored"})

        state.processed_messages.add(msg_id)

        # Filter GOWA echo-backs: ignore messages we recently sent (GOWA = "default")
        if text:
            sent_key = f"default:{phone}:{text[:120]}"
            sent_at = state.recently_sent.pop(sent_key, None)
            if sent_at and (time.time() - sent_at) < 30:
                logger.info("[Webhook] Ignoring echo-back for %s", phone)
                return _ok({"status": "echo"})

        # Sync outgoing messages sent from phone (not via our app)
        if is_from_me:
            # media_type/media_path already resolved by _extract_media above.

            # Plugin filter: mirror of `filter.message.before_save` but for
            # the echo branch — lets plugins rewrite/anonymize/drop messages
            # the user sent from their own phone before we persist them.
            outgoing_msg = {
                "phone": phone,
                "text": text,
                "msg_id": msg_id,
                "media_type": media_type,
                "media_path": media_path,
                "media_extras": media_extras,
                "reply_to_msg_id": reply_to_msg_id,
                "is_from_me": True,
                "source": "echo",
                "raw": data,
                "ts": time.time(),
            }
            outgoing_msg = await apply_filter(
                "filter.message.outgoing", outgoing_msg, {"phone": phone}
            )
            if outgoing_msg is None:
                logger.info(
                    "[Webhook] outgoing echo from %s filtered out", phone
                )
                return _ok({"status": "filtered_out"})
            text = outgoing_msg.get("text", text)
            msg_id = outgoing_msg.get("msg_id", msg_id)
            media_type = outgoing_msg.get("media_type", media_type)
            media_path = outgoing_msg.get("media_path", media_path)
            media_extras = outgoing_msg.get("media_extras", media_extras)
            reply_to_msg_id = outgoing_msg.get("reply_to_msg_id", reply_to_msg_id)

            logger.info("[Webhook] Syncing outgoing %s to %s: %s",
                        media_type or "message", phone,
                        text[:80] if text else f"[{media_type}]")

            # Save as "assistant" in contact memory (status="operator" to distinguish from AI)
            contact = agent_handler._get_contact(phone)
            await asyncio.to_thread(
                contact.add_message, "assistant", text,
                media_type=media_type, media_path=media_path, msg_id=msg_id,
                reply_to_msg_id=reply_to_msg_id,
                status="operator")

            # Broadcast to frontend
            broadcast_msg: dict = {"role": "assistant", "content": text,
                                   "ts": time.time(), "msg_id": msg_id,
                                   "status": "operator"}
            if reply_to_msg_id:
                broadcast_msg["reply_to_msg_id"] = reply_to_msg_id
            if media_type:
                broadcast_msg["media_type"] = media_type
                broadcast_msg["media_path"] = media_path
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": broadcast_msg,
            })

            # Plugin event: this message was sent from the user's phone outside the app
            await emit_with_filter("message.sent", {
                "phone": phone, "text": text, "msg_id": msg_id,
                "media_type": media_type, "media_path": media_path,
                "media_extras": media_extras,
                "source": "echo", "status": "operator",
                "ts": time.time(),
            })

            # Transcribe outgoing audio if the configured mode includes "sent"
            if audio_path:
                out_transcription = await _maybe_transcribe(
                    "audio", audio_path,
                    phone=phone, source="echo",
                    is_group=contact.is_group,
                    group_jid=phone if contact.is_group else None,
                )
                if out_transcription:
                    await _deliver_audio_transcription(
                        phone, contact, out_transcription
                    )

            return _ok({"status": "synced"})

        # media_type/media_path already resolved by _extract_media above.

        # For groups: prefix text with sender name and check @mention
        display_text = text
        skip_ai = False
        bot_mentioned = False  # set inside the is_group branch; kept bound for non-group
        if is_group:
            # Log full group payload for debugging field names
            logger.info("[Webhook] Group payload: %s", json.dumps(data, default=str, ensure_ascii=False)[:2000])

            # Ensure group metadata is stored
            contact = agent_handler._get_contact(phone)
            if not contact.is_group or not contact.group_name:
                contact.is_group = True
                # Try to get group name from payload fields (NOT from_name, that's the sender)
                group_name = (data.get("subject", "")
                              or data.get("group_name", "")
                              or data.get("group_subject", "")
                              or data.get("chat_name", ""))
                # Fallback: fetch group name from GOWA API
                if not group_name:
                    try:
                        group_name = await asyncio.to_thread(
                            gowa_client.get_group_name, phone)
                    except Exception as e:
                        logger.warning("[Webhook] Failed to fetch group name: %s", e)
                if group_name:
                    contact.group_name = group_name
                    logger.info("[Webhook] Group name resolved: %s -> %s", phone, group_name)
                else:
                    logger.warning("[Webhook] Could not resolve group name for %s", phone)
                contact.save()

            # Check if bot can send in this group
            if state.bot_phone:
                try:
                    can_send = await asyncio.to_thread(
                        gowa_client.can_bot_send_in_group, phone, state.bot_phone)
                    if contact.can_send != can_send:
                        contact.can_send = can_send
                        contact.save()
                        logger.info("[Webhook] Group %s can_send updated: %s", phone, can_send)
                except Exception as e:
                    logger.warning("[Webhook] Failed to check group send permission: %s", e)

            # Prefix message with sender name for group context.
            # Prefer the saved contact name (if the sender exists as a private
            # contact), so renames in the contact info panel propagate to new
            # group messages. Falls back to WhatsApp pushName, then phone.
            # Remember the sender's pushName so future name resolution works
            # (GOWA's participant list has no display names). Keyed by the
            # sender's digits (phone OR lid, depending on group addressing).
            if from_name:
                await asyncio.to_thread(
                    group_mentions.record_pushname, [individual_phone], from_name)

            # Resolve names for this group once (saved contact > pushName > number),
            # handling lid-addressed groups. `lookup` maps phone/lid digits -> name
            # and is reused to turn @<number> mentions in the body into @<Name>.
            try:
                lookup = await asyncio.to_thread(group_mentions.build_lookup, chat_jid)
            except Exception as e:
                logger.warning("[Webhook] member lookup failed for %s: %s", chat_jid, e)
                lookup = {}
            sender_label = lookup.get(individual_phone) or from_name or individual_phone
            if text:
                display_text = f"[{sender_label}]: {group_mentions.apply_incoming(lookup, text)}"

            # Check if bot is mentioned (use RAW text, before name resolution).
            # Per-channel (plano 21): the GOWA legacy webhook is single-channel, so
            # the group-reply policy is read from the ``default`` channel's override.
            group_mode = ai_settings.value(
                _GOWA_CHANNEL_ID, "group_reply_mode",
                settings.get("group_reply_mode", "mention_only"))
            bot_mentioned = _is_bot_mentioned(text, data)

            if group_mode == "never" or (group_mode == "mention_only" and not bot_mentioned):
                skip_ai = True
                logger.info("[Webhook] Group message (no mention) from %s in %s: %s",
                            sender_label, phone, text[:80] if text else "[media]")
            else:
                # Bot was mentioned — strip mention from text for LLM
                cleaned = _strip_bot_mention(text)
                display_text = (f"[{sender_label}]: {group_mentions.apply_incoming(lookup, cleaned)}"
                                if cleaned else display_text)
                logger.info("[Webhook] Group message (@mention) from %s in %s: %s",
                            sender_label, phone, text[:80] if text else "[media]")
        else:
            logger.info("[Webhook] %s from %s: %s",
                        media_type.capitalize() if media_type else "Message",
                        phone, text[:80] if text else f"[{media_type}]")

        # Check/update archive status from GOWA (skip if archived by app)
        try:
            contact = agent_handler._get_contact(phone)
            if not contact.archived_by_app:
                archived = await asyncio.to_thread(gowa_client.is_chat_archived, chat_jid)
                logger.info("[Webhook] Archive check: %s (jid=%s) -> archived=%s", phone, chat_jid, archived)
                if contact.is_archived != archived:
                    contact.is_archived = archived
                    contact.save()
                    logger.info("[Webhook] Archive status updated: %s -> %s", phone, archived)
                    await emit_with_filter("chat.archived", {
                        "phone": phone, "archived": bool(archived),
                        "ts": time.time(),
                    })
            else:
                logger.info("[Webhook] Skipping archive check for %s (archived by app)", phone)
        except Exception as e:
            logger.warning("[Webhook] Failed to check archive status for %s: %s", phone, e)

        # Auto-fill contact name from WhatsApp pushName (private chats only)
        if from_name and not is_group:
            await asyncio.to_thread(agent_handler._get_contact(phone).set_wa_name, from_name)

        # Increment unread count for incoming user messages
        await asyncio.to_thread(lambda: agent_handler._get_contact(phone).increment_unread(msg_id))

        # A group message that @mentions the bot raises the "mention" flag, shown as
        # an "@" next to the unread badge until the operator opens the conversation.
        if is_group and bot_mentioned:
            await asyncio.to_thread(lambda: agent_handler._get_contact(phone).mark_mention())

        # Build parsed message payload for plugins (filter + event). Includes
        # the full GOWA payload under `raw` so plugins that need an obscure
        # field can still get it. We emit BEFORE the skip_ai branch so group
        # messages without a mention still show up to event subscribers.
        parsed_msg = {
            "phone": phone,
            "name": from_name,
            "text": display_text,
            "raw_text": text,
            "msg_id": msg_id,
            "reply_to_msg_id": reply_to_msg_id,
            "media_type": media_type,
            "media_path": media_path,
            "media_extras": media_extras,
            "is_group": is_group,
            "group_jid": chat_jid if is_group else None,
            "individual_phone": individual_phone if is_group else None,
            "is_from_me": False,
            "raw": data,
            "ts": time.time(),
        }
        # Plugin filter: can rewrite/anonymize/translate or return None to drop
        parsed_msg = await apply_filter(
            "filter.message.before_save", parsed_msg, {"phone": phone}
        )
        if parsed_msg is None:
            logger.info("[Webhook] inbound from %s filtered out before save", phone)
            return _ok({"status": "filtered_out"})

        # Filter may have rewritten user-facing strings — propagate.
        display_text = parsed_msg.get("text", display_text)
        msg_id = parsed_msg.get("msg_id", msg_id)
        reply_to_msg_id = parsed_msg.get("reply_to_msg_id", reply_to_msg_id)
        media_type = parsed_msg.get("media_type", media_type)
        media_path = parsed_msg.get("media_path", media_path)
        media_extras = parsed_msg.get("media_extras", media_extras)

        # Broadcast incoming message to frontend in real-time
        broadcast_msg: dict = {"role": "user", "content": display_text, "ts": time.time(), "msg_id": msg_id}
        if reply_to_msg_id:
            broadcast_msg["reply_to_msg_id"] = reply_to_msg_id
        if media_type:
            broadcast_msg["media_type"] = media_type
            broadcast_msg["media_path"] = media_path
        if is_group and bot_mentioned:
            broadcast_msg["mentioned"] = True
        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "message": broadcast_msg,
        })

        # Plugin event: fired for ALL inbound messages, including group msgs
        # without a mention. Plugins filter inside their handler on
        # `is_group`/`media_type` etc.
        await emit_with_filter("message.received", parsed_msg)

        # For group messages without mention: save to history but don't trigger AI.
        # Persist media_type/media_path so the chat panel can render the audio/image/document
        # player on reload, the same way it does for private chats.
        if skip_ai:
            # Transcribe audio/describe image even though the AI won't reply, so the
            # transcription card still appears in the panel — matches private-chat UX.
            transcription = ""
            doc_path_grp = media_path if media_type == "document" else None
            if audio_path:
                transcription = await _maybe_transcribe(
                    "audio", audio_path,
                    phone=phone, source="group_no_mention",
                    is_group=True, group_jid=phone,
                )
            elif image_path:
                transcription = await _maybe_transcribe(
                    "image", image_path,
                    phone=phone, source="group_no_mention",
                    is_group=True, group_jid=phone,
                )
            elif doc_path_grp:
                transcription = await _maybe_transcribe(
                    "document", doc_path_grp,
                    phone=phone, source="group_no_mention",
                    is_group=True, group_jid=phone,
                    file_name=(media_extras or {}).get("file_name") or "",
                    mimetype=(media_extras or {}).get("mimetype") or "",
                )

            saved_text = display_text
            if transcription and audio_path:
                saved_text = f"{display_text}\n[Transcrição do áudio]: {transcription}" if display_text else f"[Transcrição do áudio]: {transcription}"
            elif transcription and image_path:
                desc_prefix = f"[Descrição da imagem]: {transcription}"
                saved_text = f"{desc_prefix}\n{display_text}" if display_text else desc_prefix
            elif transcription and doc_path_grp:
                doc_prefix = f"[Conteúdo do documento]: {transcription}"
                saved_text = f"{display_text}\n{doc_prefix}" if display_text else doc_prefix

            contact_obj = agent_handler._get_contact(phone)
            await asyncio.to_thread(
                contact_obj.add_message,
                "user", saved_text,
                media_type=media_type, media_path=media_path,
                msg_id=msg_id, reply_to_msg_id=reply_to_msg_id)
            await emit_with_filter("message.saved", {
                "phone": phone, "text": saved_text, "msg_id": msg_id,
                "media_type": media_type, "media_path": media_path,
                "media_extras": media_extras,
                "is_group": True, "group_jid": phone,
                "source": "group_no_mention",
                "ts": time.time(),
            })

            if transcription:
                if audio_path:
                    await _deliver_audio_transcription(phone, contact_obj, transcription)
                else:
                    await asyncio.to_thread(contact_obj.add_message, "transcription", transcription)
                    await ws_manager.broadcast("new_message", {
                        "phone": phone,
                        "message": {
                            "role": "transcription",
                            "content": transcription,
                            "ts": time.time(),
                        },
                    })
            return _ok({"status": "group_no_mention"})

        # Batch messages — accumulate and wait before responding. GOWA is the
        # "default" channel; the batch key is (channel_id, phone) — plano 11.
        batch_key = ("default", phone)
        if batch_key not in state.pending_messages:
            state.pending_messages[batch_key] = []
        state.pending_messages[batch_key].append({
            "text": display_text,
            "image_path": image_path,
            "audio_path": audio_path,
            "media_type": media_type,
            "media_path": media_path,
            "media_extras": media_extras,
            "msg_id": msg_id,
            "reply_to_msg_id": reply_to_msg_id,
        })

        # A real message arriving from the contact proves they finished typing
        # *something*. WhatsApp doesn't reliably emit `paused` for linked devices,
        # so without this the orchestrator would block on a stale `composing` flag
        # until the 25s stale timeout. Clear here; a *new* `composing` event for
        # the next message will re-set `active=True` with a fresh last_ts.
        ts = state.typing_state.get(batch_key)
        if ts and ts.get("active"):
            state.typing_state[batch_key] = {**ts, "active": False}

        # Schedule (or restart) the typing-aware orchestrator. This cancels the current
        # cycle if it's not in the SEND phase yet, so a newly arrived message can be
        # bundled into the same batch (and any in-flight LLM call is aborted).
        _schedule_orchestrator("default", phone)

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
