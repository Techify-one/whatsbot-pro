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
    # Customer-care session window in hours (plano 11 Fase 6). 0 = always-open
    # (GOWA/linked-device): free text any time. >0 = providers like the WhatsApp
    # Cloud API where free text is only allowed within N hours of the last inbound;
    # outside it requires an approved template (HSM). Drives the pipeline by
    # CAPABILITY, never by provider name.
    session_window_hours: int = 0
    # Credential keys a channel of this provider MUST have to ever become
    # operational (plano 02 — anti zombie-channel). Empty for QR/linked-device
    # providers (GOWA): they legitimately bootstrap from an empty channel via the
    # QR connect flow. For credential-only providers without a connect step
    # (WhatsApp Cloud API, Telegram) a channel missing these can never connect nor
    # send — so ``create_channel`` rejects it. Drives validation by CAPABILITY,
    # never by provider name. Tuple (immutable) so it is safe as a default.
    required_credentials: tuple = ()


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

    # Session actions — meaningful only for QR/linked-device providers (GOWA).
    # Default no-op so telegram/cloud/test don't have to implement them
    # (plano 27 D4).
    def reconnect(self) -> dict:  # default: unsupported
        return {"ok": False, "error": "não suportado"}

    def logout(self) -> dict:  # default: unsupported
        return {"ok": False, "error": "não suportado"}

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

    def list_templates(self) -> list[dict]:
        """List message templates (HSM) for the account, any status.

        Optional, default raises NotImplementedError (like ``send_template``). Only
        channels with ``capabilities.templates`` implement it. Each item is
        normalized to ``{name, language, category, status, components:[...]}``.
        """
        raise NotImplementedError(f"{self.provider} does not support listing templates")

    def create_template(self, name: str, *, category: str, language: str,
                        body_text: str, header_text: Optional[str] = None,
                        footer_text: Optional[str] = None,
                        body_examples: Optional[list] = None,
                        header_examples: Optional[list] = None) -> dict:
        """Create a message template (HSM) at the provider, returning the raw result.

        Optional, default raises NotImplementedError. Only channels with
        ``capabilities.templates`` implement it. Returns
        ``{ok, id?, status?, category?, error?}``.
        """
        raise NotImplementedError(f"{self.provider} does not support creating templates")

    def delete_template(self, name: str) -> dict:
        """Delete a message template (all language versions) by name.

        Optional, default raises NotImplementedError. Returns ``{ok, error?}``.
        """
        raise NotImplementedError(f"{self.provider} does not support deleting templates")

    def check_phone(self, phone: str) -> dict:
        """Whether ``phone`` is reachable on this channel.

        Default for providers that CANNOT verify a number before sending (WhatsApp
        Cloud API, Telegram, …): assume it's valid. Verification only exists on
        linked-device providers (GOWA, via ``/user/check``), which override this.
        Returns the GOWA-shaped dict (``registered``, ``canonical_phone``, ``name``)
        so the caller stays provider-agnostic — never branches on provider name.
        """
        return {"registered": True, "canonical_phone": phone, "name": ""}

    # ── Inbound ──────────────────────────────────────────────────────
    @abstractmethod
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Translate a provider-specific raw payload into InboundEvents."""
