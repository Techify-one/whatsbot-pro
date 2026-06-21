"""Per-provider webhook endpoints (plano 02 Fase 0.5 / Fase 2).

Generic ingress for provider-plugins that deliver inbound by HTTP path
(WhatsApp Cloud API, Telegram, …). The CORE owns the endpoint (plugins never
open their own webhook route — §2.3); it resolves the channel in the
``ChannelRegistry`` and delegates payload parsing to the provider's
``parse_inbound``.

Auth: these paths are exempt from the Bearer middleware (added to
``_AUTH_EXEMPT_PREFIXES`` as ``/api/webhook/``) — providers authenticate the
request themselves (Cloud API ``verify_token`` on GET; signature/verify on POST).
The exact ``/api/webhook`` (GOWA) and ``/health`` exemptions are preserved.

GET  = handshake (Cloud API ``hub.challenge`` verification).
POST = inbound delivery → ``parse_inbound`` (always answers 200 so the provider
       does not retry). NOTE: feeding the parsed events into the agentic
       reply loop reuses the batch orchestrator in ``webhook.py`` and lands with
       the live-pipeline refactor (0.5) — this route parses + records for now.
"""

import asyncio
import logging
import time

from fastapi import Request
from fastapi.responses import PlainTextResponse

from db.repositories import (channel_repo, channel_credential_repo, message_repo,
                             contact_repo, conversation_repo, inbox_repo)
from agent import group_mentions
from plugins.events import emit_with_filter, apply_filter
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

# Last raw payloads per provider (in-memory, debug) — mirrors webhook-payloads.
_RECENT: list[dict] = []
_RECENT_CAP = 50


def register_routes(app, deps):

    registry = getattr(deps, "channel_registry", None)
    ws_manager = getattr(deps, "ws_manager", None)
    agent_handler = getattr(deps, "agent_handler", None)
    state = getattr(deps, "state", None)

    def _resolve_presence_conv_id(channel_id: str, phone: str):
        """Resolve the conversation id for a typing indicator so the frontend
        scopes "digitando" to the exact conversation (it keys by conversation_id —
        a None would never match the open chat's ``conv:<id>`` key). Cached per
        (channel, phone) with a short TTL; best-effort → None on any failure."""
        now = time.time()
        cache = getattr(state, "presence_conv_cache", None) if state is not None else None
        ckey = (channel_id, phone)
        if cache is not None:
            cached = cache.get(ckey)
            if cached and cached[1] > now:
                return cached[0]
        conv_id = None
        try:
            contact = contact_repo.get_by_phone(phone)
            if contact:
                inbox = inbox_repo.get_by_channel(channel_id)
                inbox_id = inbox["id"] if inbox else conversation_repo.DEFAULT_INBOX_ID
                conv = conversation_repo.get_latest_for_contact_inbox(
                    contact["id"], inbox_id)
                if conv:
                    conv_id = conv["id"]
        except Exception:
            logger.debug("presence conv_id resolution failed for %s/%s",
                         channel_id, phone, exc_info=True)
        if cache is not None:
            cache[ckey] = (conv_id, now + 30.0)
        return conv_id

    async def _dispatch_events(events: list) -> int:
        """Route parsed InboundEvents (plano 11 Fase 2 / plano 13 Fase 0).

        message  → the agentic ingress (deps.ingest_event; handles echo via
                   ``direction='out'``), same orchestrator for every channel.
        reaction/receipt/edited/revoked/deleted/presence/group_*/call/newsletter
                 → persist + broadcast + bus event (panel + plugin parity with the
                   legacy GOWA handler — moved here so GOWA ingresses through this
                   same dispatch). Returns the number of events that produced an action.
        """
        ingest = getattr(deps, "ingest_event", None)
        handled = 0
        for ev in events:
            kind = getattr(ev, "kind", "message")
            extras = ev.media_extras or {}
            try:
                if kind == "message":
                    if ingest is not None:
                        await ingest(ev)
                        handled += 1
                elif kind == "reaction":
                    reacted_id = extras.get("reacted_message_id", "")
                    emoji = extras.get("emoji", "")
                    is_from_me = bool(extras.get("is_from_me", False))
                    if reacted_id:
                        reactor = "me" if is_from_me else (ev.sender_id or ev.chat_id or "")
                        reactions = await asyncio.to_thread(
                            message_repo.set_reaction, reacted_id, emoji, reactor)
                        if reactions is not None and ws_manager is not None:
                            await ws_manager.broadcast("message_reaction", {
                                "phone": ev.chat_id, "msg_id": reacted_id, "reactions": reactions})
                    await emit_with_filter("message.reaction", {
                        "id": ev.external_msg_id, "phone": ev.chat_id,
                        "from": ev.sender_id, "reaction": emoji,
                        "reacted_message_id": reacted_id, "is_from_me": is_from_me,
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "receipt":
                    status = extras.get("status")
                    mid = ev.external_msg_id
                    if status in ("delivered", "read") and mid:
                        updated = await asyncio.to_thread(
                            message_repo.update_status_by_msg_id, mid, status)
                        if updated and ws_manager is not None:
                            await ws_manager.broadcast("message_status", {
                                "phone": ev.chat_id, "msg_ids": updated, "status": status})
                        await emit_with_filter("receipt.changed", {
                            "phone": ev.chat_id, "msg_ids": [mid], "status": status,
                            "channel_id": ev.channel_id, "ts": ev.ts or time.time()})
                    handled += 1
                elif kind == "edited":
                    await emit_with_filter("message.edited", {
                        "id": ev.external_msg_id, "phone": ev.chat_id, "from": ev.sender_id,
                        "original_message_id": extras.get("original_message_id", ""),
                        "body": ev.text, "is_from_me": bool(extras.get("is_from_me", False)),
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "revoked":
                    revoked_id = extras.get("revoked_message_id", "")
                    if revoked_id:
                        matched = await asyncio.to_thread(message_repo.mark_revoked, revoked_id, "all")
                        if matched and ws_manager is not None:
                            await ws_manager.broadcast("message_revoked", {
                                "phone": ev.chat_id, "msg_id": revoked_id})
                    await emit_with_filter("message.revoked", {
                        "id": ev.external_msg_id, "phone": ev.chat_id, "from": ev.sender_id,
                        "revoked_message_id": revoked_id,
                        "revoked_from_me": bool(extras.get("revoked_from_me", False)),
                        "revoked_chat": extras.get("revoked_chat", ""),
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "deleted":
                    deleted_id = extras.get("deleted_message_id", "")
                    if deleted_id:
                        matched = await asyncio.to_thread(message_repo.mark_revoked, deleted_id, "me")
                        if matched and ws_manager is not None:
                            await ws_manager.broadcast("message_deleted", {
                                "phone": ev.chat_id, "msg_id": deleted_id})
                    await emit_with_filter("message.deleted", {
                        "deleted_message_id": deleted_id, "phone": ev.chat_id, "from": ev.sender_id,
                        "original_content": extras.get("original_content", ""),
                        "original_sender": extras.get("original_sender", ""),
                        "original_timestamp": extras.get("original_timestamp"),
                        "was_from_me": bool(extras.get("was_from_me", False)),
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "presence":
                    phone = ev.chat_id
                    pstate = extras.get("state", "")
                    media = extras.get("media", "") or "text"
                    if phone and pstate:
                        if state is not None:
                            state.typing_state[(ev.channel_id, phone)] = {
                                "active": pstate == "composing", "media": media,
                                "last_ts": time.time()}
                        # Resolve the exact conversation so the panel scopes the
                        # "digitando" indicator to it (frontend keys by conversation_id).
                        conv_id = await asyncio.to_thread(
                            _resolve_presence_conv_id, ev.channel_id, phone)
                        if ws_manager is not None:
                            await ws_manager.broadcast("chat_presence", {
                                "phone": phone, "channel_id": ev.channel_id,
                                "conversation_id": conv_id, "state": pstate, "media": media})
                        await emit_with_filter("presence.changed", {
                            "phone": phone, "state": pstate, "media": media,
                            "channel_id": ev.channel_id, "ts": ev.ts or time.time()})
                    handled += 1
                elif kind == "group_participants":
                    chat_id = ev.chat_id
                    ctype = extras.get("type", "")
                    jids = extras.get("jids", []) or []
                    if chat_id:
                        members = await asyncio.to_thread(
                            group_mentions.apply_participants_change, chat_id, ctype, jids)
                        if ws_manager is not None:
                            await ws_manager.broadcast("group_participants_changed",
                                                       {"group_jid": chat_id, "members": members})
                        existing = await asyncio.to_thread(contact_repo.get_by_phone, chat_id)
                        if existing and agent_handler is not None:
                            notice = await asyncio.to_thread(
                                group_mentions.describe_change, ctype, jids)
                            if notice:
                                contact_obj = agent_handler._get_contact(chat_id)
                                await asyncio.to_thread(contact_obj.add_message,
                                                        "system_notice", notice)
                                if ws_manager is not None:
                                    await ws_manager.broadcast("new_message", {
                                        "phone": chat_id,
                                        "message": {"role": "system_notice",
                                                    "content": notice, "ts": time.time()}})
                    await emit_with_filter("group.participants_changed", {
                        "chat_id": chat_id, "phone": chat_id.split("@")[0] if chat_id else "",
                        "type": ctype, "jids": jids,
                        "channel_id": ev.channel_id, "ts": time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "group_joined":
                    await emit_with_filter("group.joined", {
                        "chat_id": ev.chat_id,
                        "phone": ev.chat_id.split("@")[0] if ev.chat_id else "",
                        "channel_id": ev.channel_id, "ts": time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "call":
                    await emit_with_filter("call.received", {
                        "call_id": extras.get("call_id", ""), "phone": ev.chat_id,
                        "auto_rejected": bool(extras.get("auto_rejected", False)),
                        "remote_platform": extras.get("remote_platform", ""),
                        "remote_version": extras.get("remote_version", ""),
                        "group_jid": extras.get("group_jid"),
                        "channel_id": ev.channel_id, "ts": time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "newsletter":
                    await emit_with_filter("newsletter.event", {
                        "subtype": extras.get("subtype", ""),
                        "channel_id": ev.channel_id, "ts": time.time(), "raw": ev.raw})
                    handled += 1
            except Exception:
                logger.warning("Falha ao processar evento %s do canal %s",
                               kind, getattr(ev, "channel_id", "?"), exc_info=True)
        return handled

    @app.get("/api/webhook/{provider}/{channel_id}")
    async def webhook_handshake(provider: str, channel_id: str, request: Request):
        # Cloud API verification handshake (§5.2): echo hub.challenge when the
        # verify_token matches the one stored for this channel.
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and challenge is not None:
            expected = channel_credential_repo.get(channel_id, "verify_token")
            if expected and token == expected:
                return PlainTextResponse(challenge, status_code=200)
            logger.warning("Webhook handshake: verify_token mismatch for %s/%s",
                           provider, channel_id)
            return PlainTextResponse("forbidden", status_code=403)
        return PlainTextResponse("ok", status_code=200)

    @app.post("/api/webhook/{provider}/{channel_id}")
    async def webhook_inbound(provider: str, channel_id: str, request: Request):
        # Must never 500 — a provider that gets an error retries forever.
        try:
            raw = await request.json()
        except Exception:
            raw = {}

        # Plugin filter: full webhook payload before any parse (plano 13 Fase 0 —
        # same hook the legacy /api/webhook handler offers, now for every provider).
        raw = await apply_filter("filter.webhook.payload", raw, {})
        if raw is None:
            return _ok({"status": "filtered_out"})

        _RECENT.append({"provider": provider, "channel_id": channel_id, "raw": raw})
        del _RECENT[:-_RECENT_CAP]
        # GOWA debug parity: also surface in /api/webhook-payloads.
        if state is not None:
            try:
                state.webhook_payloads.append({
                    "ts": time.time(), "event": raw.get("event", ""),
                    "payload": raw.get("payload", raw.get("data", raw))})
            except Exception:
                pass

        # GOWA delivers every device's inbound to the SAME webhook URL (launched as
        # .../gowa/default), so the URL's channel_id can't tell which number received
        # the message. Resolve the real channel from the GOWA v8 envelope (top-level
        # ``device_id`` = receiving JID, ``session_id`` = registered device string) so
        # a 2nd GOWA number lands in its own inbox/conversation and replies go out its
        # own device. Best-effort: only override when it maps to a DIFFERENT live
        # channel; otherwise keep the URL channel (legacy behaviour) — never worse.
        if provider == "gowa":
            sess = raw.get("session_id") or (raw.get("payload") or {}).get("session_id")
            djid = raw.get("device_id") or (raw.get("payload") or {}).get("device_id")
            resolved = await asyncio.to_thread(
                channel_repo.get_gowa_channel_for_device, sess, djid)
            if (resolved and resolved != channel_id
                    and registry is not None and registry.get(resolved) is not None):
                logger.info("[Webhook gowa] inbound routed by device to channel %r "
                            "(url=%r, session_id=%r, device_id=%r)",
                            resolved, channel_id, sess, djid)
                channel_id = resolved

        row = channel_repo.get(channel_id)
        if row is None:
            # Unknown channel: ack 200 (avoid retries) but record nothing useful.
            logger.warning("Webhook inbound for unknown channel %s/%s", provider, channel_id)
            return _ok({"status": "ignored", "reason": "unknown_channel"})

        events = []
        inst = registry.get(channel_id) if registry is not None else None
        if inst is not None and hasattr(inst, "parse_inbound"):
            try:
                # parse_inbound may do blocking I/O (GOWA resolves group name /
                # archive / filename via its client) — keep it off the event loop.
                events = await asyncio.to_thread(inst.parse_inbound, raw) or []
            except Exception as e:
                logger.warning("parse_inbound failed for %s/%s: %s", provider, channel_id, e)
        logger.info("Webhook inbound %s/%s → %d evento(s) parseado(s)",
                    provider, channel_id, len(events))
        # plano 11 Fase 2: feed the parsed events into the agentic pipeline (same
        # batch orchestrator as GOWA) and re-dispatch reaction/receipt to the bus.
        handled = await _dispatch_events(events)
        return _ok({"status": "received", "events": len(events), "handled": handled})

    @app.get("/api/channel-webhook-payloads")
    async def recent_payloads(request: Request, limit: int = 20):
        return _ok(_RECENT[-max(1, min(limit, _RECENT_CAP)):])
