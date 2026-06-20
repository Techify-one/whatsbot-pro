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

import logging

from fastapi import Request
from fastapi.responses import PlainTextResponse

from db.repositories import channel_repo, channel_credential_repo
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

# Last raw payloads per provider (in-memory, debug) — mirrors webhook-payloads.
_RECENT: list[dict] = []
_RECENT_CAP = 50


def register_routes(app, deps):

    registry = getattr(deps, "channel_registry", None)

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
        # TODO (0.5): enfileirar os events no orquestrador de batch do webhook.py
        # para fechar inbound→resposta agêntica. Hoje parseia e registra.
        return _ok({"status": "received", "events": len(events)})

    @app.get("/api/channel-webhook-payloads")
    async def recent_payloads(request: Request, limit: int = 20):
        return _ok(_RECENT[-max(1, min(limit, _RECENT_CAP)):])
