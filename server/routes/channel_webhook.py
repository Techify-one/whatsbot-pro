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

from db.repositories import channel_repo, channel_credential_repo, message_repo
from plugins.events import emit_with_filter
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

# Last raw payloads per provider (in-memory, debug) — mirrors webhook-payloads.
_RECENT: list[dict] = []
_RECENT_CAP = 50


def register_routes(app, deps):

    registry = getattr(deps, "channel_registry", None)
    ws_manager = getattr(deps, "ws_manager", None)

    async def _dispatch_events(events: list) -> int:
        """Route parsed InboundEvents (plano 11 Fase 2).

        message  → the agentic ingress (deps.ingest_event), same orchestrator as GOWA.
        reaction → persist + broadcast + bus event (panel parity).
        receipt  → outbound delivery/read status update + bus event.
        Returns the number of events that produced an action.
        """
        ingest = getattr(deps, "ingest_event", None)
        handled = 0
        for ev in events:
            kind = getattr(ev, "kind", "message")
            try:
                if kind == "message":
                    if ingest is not None:
                        await ingest(ev)
                        handled += 1
                elif kind == "reaction":
                    extras = ev.media_extras or {}
                    reacted_id = extras.get("reacted_message_id", "")
                    emoji = extras.get("emoji", "")
                    if reacted_id:
                        reactions = await asyncio.to_thread(
                            message_repo.set_reaction, reacted_id, emoji, ev.sender_id or "")
                        if reactions is not None and ws_manager is not None:
                            await ws_manager.broadcast("message_reaction", {
                                "phone": ev.chat_id, "msg_id": reacted_id, "reactions": reactions})
                    await emit_with_filter("message.reaction", {
                        "id": ev.external_msg_id, "phone": ev.chat_id,
                        "from": ev.sender_id, "reaction": emoji,
                        "reacted_message_id": reacted_id, "is_from_me": False,
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "receipt":
                    extras = ev.media_extras or {}
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
        _RECENT.append({"provider": provider, "channel_id": channel_id, "raw": raw})
        del _RECENT[:-_RECENT_CAP]

        row = channel_repo.get(channel_id)
        if row is None:
            # Unknown channel: ack 200 (avoid retries) but record nothing useful.
            logger.warning("Webhook inbound for unknown channel %s/%s", provider, channel_id)
            return _ok({"status": "ignored", "reason": "unknown_channel"})

        events = []
        inst = registry.get(channel_id) if registry is not None else None
        if inst is not None and hasattr(inst, "parse_inbound"):
            try:
                events = inst.parse_inbound(raw) or []
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
