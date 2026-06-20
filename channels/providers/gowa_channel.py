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

    def stop(self) -> None:
        if self._manager is not None:
            self._manager.stop()

    def status(self) -> dict:
        connected = bool(self._manager and self._manager.is_running)
        logged_in = False
        try:
            logged_in = bool(self._client and self._client.is_connected())
        except Exception:
            pass
        return {"connected": connected, "logged_in": logged_in,
                "needs_qr": connected and not logged_in, "error": None}

    # ── Outbound (delegates to the existing client) ──────────────────
    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        try:
            self._client.send_message(chat_id, text, mentions=mentions,
                                      reply_message_id=reply_to)
            return SendResult(ok=True)
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        try:
            if kind == "image":
                self._client.send_image(chat_id, path_or_url, caption=caption)
            elif kind == "audio":
                self._client.send_audio(chat_id, path_or_url)
            else:
                self._client.send_file(chat_id, path_or_url, caption=caption)
            return SendResult(ok=True)
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    # ── Inbound ──────────────────────────────────────────────────────
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        # Placeholder: the live webhook still parses inline. When the GOWA
        # parsing is extracted to a pure function (plano 02 §0.5) it is reused
        # here. Returning [] keeps the contract valid without duplicating logic.
        return []
