"""Inbound message pipeline — agentic ingress, batch orchestrator and reply send.

This module no longer owns an HTTP route. ALL inbound (GOWA included) arrives on
the generic ``POST /api/webhook/{provider}/{channel_id}`` route in
``channel_webhook.py``; GOWA is 100% on ``/api/webhook/gowa/{channel_id}`` (the
legacy auth-exempt ``/api/webhook`` fallback was retired in plano 23 Fase F2 once
the generic GOWA path reached behaviour parity — there is NO fallback).

``register_routes`` wires the provider-agnostic ``ingest_event(InboundEvent)``
funnel (plano 11): a parsed event from ANY channel (GOWA, WhatsApp Cloud, Telegram,
…) is broadcast/saved and fed into the SAME typing-aware batch orchestrator, then
the reply is routed back out through the channel's own adapter (``OutboundRouter``).
No ``if provider ==`` anywhere in the pipeline. The ingress is exposed to the
generic route via ``deps.ingest_event``.
"""

import asyncio
import json
import logging
import random
import time
import uuid

from channels.events import InboundEvent
from channels import jid as jid_classifier
from channels import ai_settings
from db.repositories import channel_repo, contact_repo, conversation_repo, message_repo
from agent import group_mentions
# Media/reply parsing helpers live in gowa.inbound (plano 13 Fase 0). Re-exported
# here for the test suite (``_extract_media``/``_extract_reply_to`` are imported
# from this module by the endpoint/filter tests); the inbound pipeline itself
# parses through ``parse_gowa_inbound`` in the GOWA channel adapter.
from gowa.inbound import _extract_media, _extract_reply_to  # noqa: F401
from server import system_notices
from server.execution import astart_execution, aend_execution, atrack_step, prune_executions
from server.helpers import _ok, parse_split_reply
from server.transcription import maybe_transcribe, format_media_content
from plugins.events import apply_filter, emit_with_filter

logger = logging.getLogger(__name__)

from collections import namedtuple

# Result of applying a content filter to a message dict (R5): the full (possibly
# mutated) dict — for sites that re-emit it, e.g. ``message.received`` — plus the
# 6 fields the inbound/outgoing pipelines pull back out.
_FilteredMessage = namedtuple(
    "_FilteredMessage",
    ["msg", "text", "msg_id", "reply_to_msg_id", "media_type", "media_path",
     "media_extras"],
)


async def _apply_message_filter(filter_name: str, msg: dict, extras: dict):
    """Apply a content filter to a message dict and re-extract the 6 fields.

    Returns a :class:`_FilteredMessage` carrying the full filtered dict (``.msg``)
    and the re-extracted ``text``/``msg_id``/``reply_to_msg_id``/``media_type``/
    ``media_path``/``media_extras`` values, or ``None`` when a plugin aborted the
    action (filter returned ``None``). Each field falls back to the pre-filter
    value so a plugin that drops a key never changes it — byte-identical to the
    previous inline ``.get(key, current)`` ceremony at every call site.
    """
    filtered = await apply_filter(filter_name, msg, extras)
    if filtered is None:
        return None
    return _FilteredMessage(
        msg=filtered,
        text=filtered.get("text", msg.get("text")),
        msg_id=filtered.get("msg_id", msg.get("msg_id")),
        reply_to_msg_id=filtered.get("reply_to_msg_id", msg.get("reply_to_msg_id")),
        media_type=filtered.get("media_type", msg.get("media_type")),
        media_path=filtered.get("media_path", msg.get("media_path")),
        media_extras=filtered.get("media_extras", msg.get("media_extras")),
    )


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


# Single-slot helper bound to the seeded ``default`` GOWA channel. The live
# inbound pipeline reads allowed-JID-types per channel via
# ``_channel_allowed_jid_types`` (below); this ``default``-hardcoded reader is
# kept as a convenience for the test suite (it imports ``_read_gowa_allowed_jid_types``
# to assert ``config.allowed_jid_types`` persistence). Cached briefly.
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
    _ALLOWED_JID_BY_CHANNEL.clear()


async def _gowa_allowed_jid_types() -> list[str]:
    now = time.time()
    cached = _ALLOWED_JID_CACHE["types"]
    if cached is not None and (now - _ALLOWED_JID_CACHE["ts"]) < _ALLOWED_JID_TTL:
        return cached
    types = await asyncio.to_thread(_read_gowa_allowed_jid_types)
    _ALLOWED_JID_CACHE["types"] = types
    _ALLOWED_JID_CACHE["ts"] = now
    return types


# Per-channel allowed-JID cache for the generic live path (a 2nd GOWA number
# resolves to its own channel — plano 11). Keyed by channel_id; same TTL as the
# single-slot cache above.
_ALLOWED_JID_BY_CHANNEL: dict = {}


def _read_channel_allowed_jid_types(channel_id: str) -> list[str]:
    """Read a specific channel's ``config.allowed_jid_types`` (live-path twin of
    :func:`_read_gowa_allowed_jid_types`, which is hardcoded to ``default``)."""
    try:
        row = channel_repo.get(channel_id)
        cfg = row.get("config") if row else None
        if isinstance(cfg, str) and cfg:
            cfg = json.loads(cfg)
        if isinstance(cfg, dict) and "allowed_jid_types" in cfg:
            return jid_classifier.normalize_allowed_types(cfg.get("allowed_jid_types"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Webhook] allowed_jid_types read failed for %s: %s",
                       channel_id, e)
    return list(jid_classifier.DEFAULT_ALLOWED_JID_TYPES)


async def _channel_allowed_jid_types(channel_id: str) -> list[str]:
    now = time.time()
    cached = _ALLOWED_JID_BY_CHANNEL.get(channel_id)
    if cached is not None and (now - cached[1]) < _ALLOWED_JID_TTL:
        return cached[0]
    types = await asyncio.to_thread(_read_channel_allowed_jid_types, channel_id)
    _ALLOWED_JID_BY_CHANNEL[channel_id] = (types, now)
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

        plano 23 Fase C0: além do card painel-only, promove ``conversation.ai_takeover``
        a evento de domínio no bus de plugins (1×/conversa, preservando o dedupe — o
        emit só dispara quando o card ainda não existia).
        """
        def _emit():
            contact = agent_handler._get_contact(phone, channel_id=channel_id)
            if contact is None:
                return None
            conv = conversation_repo.get_open_for_contact(contact.id)
            if conv is None or system_notices.has_event(conv["id"], "ai_takeover"):
                return None
            system_notices.emit_conversation_notice(
                event_type="ai_takeover", conversation_id=conv["id"],
                contact_id=contact.id, phone=phone)
            return {"conversation_id": conv["id"],
                    "agent_key": conv.get("active_agent_key")}
        try:
            fired = await asyncio.to_thread(_emit)
            if fired is not None:
                await emit_with_filter("conversation.ai_takeover", {
                    "conversation_id": fired["conversation_id"],
                    "agent_key": fired["agent_key"],
                    "ts": time.time(),
                })
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
                        new_content = format_media_content("audio", transcription)
                    elif image_path:
                        new_content = format_media_content("image", transcription, text)
                    elif document_path:
                        new_content = format_media_content("document", transcription, text)
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
                        llm_text = format_media_content("audio", transcription)
                    else:
                        llm_text = llm_text or "[Áudio recebido]"
                elif image_path and transcription:
                    llm_text = format_media_content("image", transcription, text)
                elif document_path and transcription:
                    llm_text = format_media_content("document", transcription, text)

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

    def _apply_contact_metadata(contact, event: InboundEvent) -> bool:
        """Persist provider-resolved chat metadata onto the contact (plano 13 Fase 0).

        GOWA resolves a group's name / can-send / archive status from its client and
        carries them on the event; other providers leave them ``None`` (no-op).
        Sync — call via ``asyncio.to_thread``. Returns whether the archive status
        actually changed so the caller can emit ``chat.archived`` on the bus
        (parity with the legacy ``/api/webhook`` handler, which fires it when the
        archive flag flips)."""
        changed = False
        archive_changed = False
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
            archive_changed = True
        if changed:
            contact.save()
        return archive_changed

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
        media_path, _img, audio_path = await _resolve_inbound_media(event)
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
        _filtered = await _apply_message_filter(
            "filter.message.outgoing", outgoing_msg, {"phone": phone})
        if _filtered is None:
            logger.info("[Ingest] outgoing echo from %s filtered out", phone)
            return
        text = _filtered.text
        msg_id = _filtered.msg_id
        media_type = _filtered.media_type
        media_path = _filtered.media_path
        media_extras = _filtered.media_extras
        reply_to = _filtered.reply_to_msg_id

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

        # Transcribe outgoing (echo) audio when the configured mode includes
        # "sent" (parity with the legacy GOWA handler — channel-aware here so the
        # gate + delivery target resolve from THIS channel's overrides).
        if audio_path:
            out_transcription = await _maybe_transcribe(
                "audio", audio_path,
                phone=phone, source="echo",
                is_group=contact.is_group,
                group_jid=phone if contact.is_group else None,
                channel_id=channel_id,
            )
            if out_transcription:
                await _deliver_audio_transcription(
                    phone, contact, out_transcription, channel_id=channel_id)

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

        # JID-type discard (parity with the legacy GOWA handler — CLAUDE.md
        # "Filtro de tipos de JID"): drop newsletter/broadcast/bot etc. BEFORE any
        # contact is materialized, per the channel's ``config.allowed_jid_types``.
        # GOWA-only (the suffix discriminator is a WhatsApp concept); other
        # providers leave ``event.raw`` without a JID suffix → classified UNKNOWN,
        # which is never gated, so this is a no-op for Cloud/Telegram. Applies to
        # inbound AND the is_from_me echo branch below (same as legacy).
        if getattr(event, "provider", "") == "gowa":
            raw = event.raw or {}
            chat_jid = (raw.get("chat_jid", "") or raw.get("chat_id", "")
                        or raw.get("from", "") or raw.get("jid", ""))
            jid_type = jid_classifier.classify_jid(chat_jid)
            allowed = await _channel_allowed_jid_types(channel_id)
            if not jid_classifier.is_allowed(jid_type, allowed):
                logger.info(
                    "[Ingest] Skipping %s message (jid=%s, type=%s not in allowed=%s)",
                    "outgoing" if getattr(event, "direction", "in") == "out" else "incoming",
                    chat_jid, jid_type, allowed)
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
        archive_changed = await asyncio.to_thread(_apply_contact_metadata, contact, event)
        # chat.archived bus event when the archive flag flipped (parity with the
        # legacy GOWA handler, which emits {phone, archived, ts} on a change).
        if archive_changed:
            await emit_with_filter("chat.archived", {
                "phone": phone, "archived": bool(event.is_archived),
                "ts": time.time(),
            })
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
        _filtered = await _apply_message_filter(
            "filter.message.before_save", parsed_msg, {"phone": phone})
        if _filtered is None:
            logger.info("[Ingest] inbound from %s filtered out before save", phone)
            return
        parsed_msg = _filtered.msg  # full filtered dict, re-emitted via message.received
        display_text = _filtered.text
        msg_id = _filtered.msg_id
        reply_to = _filtered.reply_to_msg_id
        media_type = _filtered.media_type
        media_path = _filtered.media_path
        media_extras = _filtered.media_extras

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
