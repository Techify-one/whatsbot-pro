"""Trivial ``test`` channel provider (plano 02 Fase 1 — fixture viva).

``TestChannel`` is a minimal, side-effect-free :class:`channels.base.Channel`
implementation used to validate the channel registration path end to end without
talking to any real provider. It does NOT inject into the real message pipeline
(that depends on the webhook refactor of a later phase); it only proves that a
plugin can register a ``Channel`` subclass via ``CHANNEL_PROVIDERS`` and that the
contract methods are callable.

Imports come exclusively from the STABLE path ``channels.base`` / ``channels.events``.
"""

from __future__ import annotations

import logging
import time
import uuid

from channels.base import Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent

logger = logging.getLogger(__name__)


class TestChannel(Channel):
    """A no-op messaging channel that echoes sends and synthesizes inbound events."""

    provider = "test"

    def __init__(self, channel_id: str = "test-default", capabilities=None):
        # inbound_route="poll": this channel is driven by polling, not by an
        # inbound HTTP path/webhook — matching how the supervised loop pattern
        # (see lifecycle.py) would feed it once wiring exists.
        super().__init__(
            channel_id,
            capabilities or ChannelCapabilities(
                qr=False,
                templates=False,
                groups=False,
                presence=False,
                reactions=False,
                media=False,
                inbound_route="poll",
            ),
        )

    # ── Lifecycle ────────────────────────────────────────────────────
    def start(self) -> None:
        logger.info("TestChannel[%s] start (no-op)", self.channel_id)

    def stop(self) -> None:
        logger.info("TestChannel[%s] stop (no-op)", self.channel_id)

    def status(self) -> dict:
        return {
            "connected": True,
            "logged_in": True,
            "needs_qr": False,
            "error": "",
        }

    # ── Outbound ─────────────────────────────────────────────────────
    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        msg_id = f"test-{uuid.uuid4().hex[:12]}"
        logger.info("TestChannel[%s] send_text -> %s: %r (id=%s)",
                    self.channel_id, chat_id, text, msg_id)
        return SendResult(ok=True, external_msg_id=msg_id)

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        msg_id = f"test-{uuid.uuid4().hex[:12]}"
        logger.info("TestChannel[%s] send_media(%s) -> %s: %r (id=%s)",
                    self.channel_id, kind, chat_id, path_or_url, msg_id)
        return SendResult(ok=True, external_msg_id=msg_id)

    # ── Inbound ──────────────────────────────────────────────────────
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Build one synthetic :class:`InboundEvent` from a raw dict.

        Accepts a loose dict (the keys mirror GOWA's webhook fields) and maps
        it onto the provider-agnostic event shape. Missing keys fall back to
        synthetic defaults so the method never raises on partial input.
        """
        raw = raw or {}
        chat_id = str(raw.get("chat_id") or raw.get("from") or "test-chat")
        sender_id = str(raw.get("from") or raw.get("sender_jid") or chat_id)
        event = InboundEvent(
            channel_id=self.channel_id,
            provider=self.provider,
            kind=str(raw.get("kind") or "message"),
            direction="in",
            external_msg_id=str(raw.get("id") or f"test-{uuid.uuid4().hex[:12]}"),
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=str(raw.get("from_name") or "Test Sender"),
            is_group=bool(raw.get("is_group") or chat_id.endswith("@g.us")),
            text=str(raw.get("body") or raw.get("text") or ""),
            ts=float(raw.get("timestamp") or time.time()),
            raw=raw,
        )
        return [event]


# Registered by the loader (entry.channels -> CHANNEL_PROVIDERS).
CHANNEL_PROVIDERS = [TestChannel]
