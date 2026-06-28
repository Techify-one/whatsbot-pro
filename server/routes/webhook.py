"""Inbound message pipeline — agentic ingress, batch orchestrator and reply send.

This module no longer owns an HTTP route. ALL inbound (GOWA included) arrives on
the generic ``POST /api/webhook/{provider}/{channel_id}`` route in
``channel_webhook.py``; GOWA is 100% on ``/api/webhook/gowa/{channel_id}`` (the
legacy auth-exempt ``/api/webhook`` fallback was retired in plano 23 Fase F2 once
the generic GOWA path reached behaviour parity — there is NO fallback).

Plano 23 Fase B3 (Branch by Abstraction): the inbound INGEST and the outbound
REPLY pipeline were lifted into the service layer —
:mod:`app.services.message_ingest_service` (``MessageIngestService.ingest_event``)
and :mod:`app.services.messaging_service` (``MessagingService``: reply/media send,
transcription delivery, tool-call broadcast, the typing-aware batch orchestrator).
``register_routes`` now just BUILDS those two services (wiring the same infra it
used to capture in closures) and exposes ``deps.ingest_event`` /
``deps.broadcast_tool_calls`` exactly as before. No HTTP route is registered here;
the generic webhook route funnels into ``deps.ingest_event``.

A handful of internals stay re-exported from this module for the test suite and
``channels.py`` (``_extract_media`` / ``_extract_reply_to`` /
``_conversation_ai_active`` / ``_read_gowa_allowed_jid_types`` /
``reset_allowed_jid_cache``) — the symbols moved into the services but their
import path is preserved here.
"""

import logging

from channels import ai_settings
# Media/reply parsing helpers live in gowa.inbound (plano 13 Fase 0). Re-exported
# here for the test suite (``_extract_media``/``_extract_reply_to`` are imported
# from this module by the endpoint/filter tests); the inbound pipeline itself
# parses through ``parse_gowa_inbound`` in the GOWA channel adapter.
from gowa.inbound import _extract_media, _extract_reply_to  # noqa: F401

# Service layer (plano 23 Fase B3). The ingest + outbound pipeline live in the
# service modules now; this module is a thin delegator that builds them in
# ``register_routes``. The JID-cache helpers come from ``message_ingest_service``
# (which has NO ``server`` import, so re-exporting them at top level is cycle-free)
# and are re-exported for the test suite + ``channels.py``.
from app.services.message_ingest_service import (
    _read_gowa_allowed_jid_types,  # noqa: F401 — re-exported for the test suite
    _gowa_allowed_jid_types,       # noqa: F401 — re-exported
    reset_allowed_jid_cache,       # noqa: F401 — re-exported for channels.py
)

logger = logging.getLogger(__name__)


def _conversation_ai_active(contact) -> bool:
    """Re-export of the per-conversation AI gate (plano 01 Fase 2) for the test
    suite, which imports it from this module. Delegates lazily to the canonical
    implementation in ``messaging_service`` (imported at call time to avoid the
    ``messaging_service → server`` import cycle at module load)."""
    from app.services.messaging_service import _conversation_ai_active as _impl
    return _impl(contact)


def register_routes(app, deps):
    # Imported here (not at module top) so loading ``server.routes.webhook`` during
    # ``server.app`` import does NOT pull in ``messaging_service`` (which imports
    # back into ``server``) — by call time ``server.app`` has finished loading.
    from app.services.messaging_service import MessagingContext, MessagingService
    from app.services.message_ingest_service import MessageIngestService, IngestContext

    agent_handler = deps.agent_handler
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

    # ── Build the outbound + ingest services (Branch by Abstraction) ──────────
    messaging = MessagingService(MessagingContext(
        deps=deps,
        agent_handler=agent_handler,
        ws_manager=ws_manager,
        state=state,
        settings=settings,
        outbound=outbound,
        channel_ai_enabled=_channel_ai_enabled,
    ))
    ingest = MessageIngestService(IngestContext(
        agent_handler=agent_handler,
        ws_manager=ws_manager,
        state=state,
        channel_registry=channel_registry,
        media_dir=media_dir,
        messaging=messaging,
    ))

    # Expose broadcast_tool_calls for the sandbox / private-AI routes.
    deps.broadcast_tool_calls = messaging.broadcast_tool_calls
    # Expose the ingress for the per-channel webhook route (Cloud/Telegram/…).
    deps.ingest_event = ingest.ingest_event
