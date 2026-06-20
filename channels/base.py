"""Channel contract (plano 02 Fase 0).

``Channel`` is the provider-agnostic interface the core talks to instead of the
GOWA client directly. Plugins import it from the STABLE path ``channels.base``.
This phase introduces the contract + an internal ``GOWAChannel`` adapter; GOWA is
not yet extracted to a plugin (that is Fase 3).
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Optional

from channels.events import InboundEvent


@dataclasses.dataclass
class ChannelCapabilities:
    qr: bool = False
    templates: bool = False
    groups: bool = False
    presence: bool = False
    reactions: bool = False
    media: bool = False
    inbound_route: str = "path"           # "path" | "poll" | "none"


@dataclasses.dataclass
class SendResult:
    ok: bool
    external_msg_id: str = ""
    error: str = ""


class Channel(ABC):
    """A connected messaging channel for one account on one provider."""

    provider: str = "base"

    def __init__(self, channel_id: str,
                 capabilities: Optional[ChannelCapabilities] = None):
        self.channel_id = channel_id
        self.capabilities = capabilities or ChannelCapabilities()

    # ── Lifecycle ────────────────────────────────────────────────────
    def start(self) -> None:  # default no-op
        ...

    def stop(self) -> None:  # default no-op
        ...

    @abstractmethod
    def status(self) -> dict:
        """Return ``{connected, logged_in, needs_qr, error}``."""

    def get_qr(self) -> Optional[bytes]:
        return None

    # ── Outbound ─────────────────────────────────────────────────────
    @abstractmethod
    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult: ...

    @abstractmethod
    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult: ...

    # Optional, default no-op
    def mark_read(self, chat_id: str, msg_id: str) -> None: ...
    def send_presence(self, chat_id: str, state: str) -> None: ...
    def react(self, chat_id: str, msg_id: str, emoji: str) -> None: ...
    def revoke(self, chat_id: str, msg_id: str) -> None: ...

    def send_template(self, *args, **kwargs) -> SendResult:
        raise NotImplementedError(f"{self.provider} does not support templates")

    # ── Inbound ──────────────────────────────────────────────────────
    @abstractmethod
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Translate a provider-specific raw payload into InboundEvents."""
