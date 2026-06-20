"""Internal GOWA channel adapter (plano 02 Fase 0).

Wraps the existing ``GOWAClient`` / ``GOWAManager`` behind the ``Channel``
contract so the registry can be exercised with a real provider before GOWA is
extracted to a plugin (Fase 3, where this moves to
``storages/plugins/gowa/channels.py``).

This phase keeps the live webhook + send flow untouched; ``parse_inbound`` is a
thin placeholder until the webhook parsing is extracted into a pure function
(plano 02 §0.5). Outbound delegation is wired and ready but not yet the only path.
"""

from __future__ import annotations

import logging

from channels.base import Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent
from gowa.client import extract_msg_id

logger = logging.getLogger(__name__)


class GOWAChannel(Channel):
    provider = "gowa"

    def __init__(self, channel_id: str, gowa_client=None, gowa_manager=None):
        super().__init__(channel_id, ChannelCapabilities(
            qr=True, templates=False, groups=True, presence=True,
            reactions=True, media=True, inbound_route="path",
        ))
        self._client = gowa_client
        self._manager = gowa_manager

    # ── Lifecycle ────────────────────────────────────────────────────
    def start(self) -> None:
        if self._manager is not None:
            self._manager.start()
        # Register this channel's device in the (shared) GOWA process so it can
        # hold its own WhatsApp session — multi-device on a single GOWA process.
        if self._client is not None:
            try:
                self._client.ensure_device()
            except Exception as e:  # noqa: BLE001
                logger.debug("GOWAChannel.start: ensure_device failed: %s", e)

    def stop(self) -> None:
        if self._manager is not None:
            self._manager.stop()

    def status(self) -> dict:
        connected = bool(self._manager and self._manager.is_running)
        logged_in = False
        own_phone = ""
        try:
            logged_in = bool(self._client and self._client.is_connected())
            if logged_in:
                own_phone = self._client.get_own_number() or ""
        except Exception:
            pass
        return {"connected": connected, "logged_in": logged_in,
                "needs_qr": connected and not logged_in,
                "own_phone": own_phone, "error": None}

    def qr(self) -> bytes | None:
        """PNG bytes of this device's login QR (None if logged in / unavailable).

        Re-verifies the GOWA device exists before requesting a QR. A device that
        was never paired can vanish from GOWA (dropped on a GOWA restart, or
        removed by a prior logout), while this client still has it cached as
        ready — which makes ``/app/login`` fail with DEVICE_NOT_FOUND. Clearing
        the cache forces ``ensure_device`` to recreate it, so connecting an
        existing channel behaves like connecting a freshly-created one.
        """
        if self._client is None:
            return None
        try:
            self._client._device_ready = False
            self._client.ensure_device()
        except Exception as e:  # noqa: BLE001
            logger.debug("GOWAChannel.qr: ensure_device failed: %s", e)
        return self._client.get_qr_code()

    # ── Outbound (delegates to the existing client) ──────────────────
    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        try:
            res = self._client.send_message(chat_id, text, mentions=mentions,
                                            reply_message_id=reply_to)
            return SendResult(ok=True, external_msg_id=extract_msg_id(res) or "")
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        try:
            if kind == "image":
                res = self._client.send_image(chat_id, path_or_url, caption=caption)
            elif kind == "audio":
                res = self._client.send_audio(chat_id, path_or_url)
            else:
                res = self._client.send_file(chat_id, path_or_url, caption=caption,
                                             filename=filename)
            return SendResult(ok=True, external_msg_id=extract_msg_id(res) or "")
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    # ── Optional capabilities (delegate to the existing client) ──────
    def mark_read(self, chat_id: str, msg_id: str) -> None:
        try:
            self._client.mark_as_read(msg_id, chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("gowa mark_read failed", exc_info=True)

    def send_presence(self, chat_id: str, state: str) -> None:
        try:
            if state in ("paused", "stop", "available"):
                self._client.stop_chat_presence(chat_id)
            else:  # composing / recording / start
                self._client.send_chat_presence(chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("gowa send_presence failed", exc_info=True)

    def react(self, chat_id: str, msg_id: str, emoji: str) -> None:
        try:
            self._client.react_to_message(msg_id, chat_id, emoji)
        except Exception:  # noqa: BLE001
            logger.debug("gowa react failed", exc_info=True)

    def revoke(self, chat_id: str, msg_id: str) -> None:
        try:
            self._client.revoke_message(msg_id, chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("gowa revoke failed", exc_info=True)

    # ── Inbound ──────────────────────────────────────────────────────
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Translate a raw GOWA webhook body into InboundEvents (plano 13 Fase 0).

        Delegates to ``gowa.inbound.parse_gowa_inbound`` (the extracted pure-ish
        parser), passing this channel's client + the configured group_reply_mode.
        Bot identity defaults to what ``group_mentions`` holds (set by the status
        poll). Blocking client/DB lookups happen inside — callers run it via
        ``asyncio.to_thread``.
        """
        from gowa.inbound import parse_gowa_inbound
        group_mode = "mention_only"
        try:
            from db.repositories import config_repo
            group_mode = config_repo.get("group_reply_mode", "mention_only") or "mention_only"
        except Exception:  # noqa: BLE001
            pass
        return parse_gowa_inbound(raw, channel_id=self.channel_id,
                                  client=self._client, group_mode=group_mode)


def build_gowa_channel(channel_id: str, row: dict | None, *,
                       gowa_client, gowa_manager) -> "GOWAChannel":
    """Build a live ``GOWAChannel`` for a channel row.

    The ``default`` channel reuses the singleton client (device ``whatsbot``)
    that drives the legacy message pipeline. Every other GOWA channel gets its
    own ``GOWAClient`` bound to its ``gowa_device_id`` but pointing at the same
    (shared) GOWA process — i.e. N WhatsApp numbers as N devices on one GOWA.
    """
    if channel_id == "default":
        return GOWAChannel(channel_id, gowa_client, gowa_manager)
    device_id = ((row or {}).get("gowa_device_id") or channel_id)
    # Import here to avoid a circular import at module load time.
    from gowa.client import GOWAClient
    client = GOWAClient(port=getattr(gowa_client, "port", 3000))
    client.device_id = device_id
    client.strict_device = True  # bind to its own device, never adopt another's
    return GOWAChannel(channel_id, client, gowa_manager)
