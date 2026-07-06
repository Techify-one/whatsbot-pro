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


@dataclasses.dataclass(frozen=True)
class AccountIdentity:
    """A provider's stable identity for the *account* a channel is bound to.

    This is the dedup key (plano 32): two enabled channels of the SAME provider
    that resolve to the SAME ``AccountIdentity`` are the same account connected
    twice — the core rejects the second. It is intentionally opaque to the core:
    the core only ever compares two identities for equality, never interprets
    them (no ``if provider ==`` anywhere in the dedup machinery).

    - ``kind`` — the *namespace* of the value (``"phone"``, ``"phone_number_id"``,
      ``"bot_id"``, ``"bot_token"``, …). The engine only ever compares identities
      whose ``kind`` matches, so a ``phone`` is never confused with an ``@lid`` or
      a ``bot_token``. A provider MUST use ONE consistent ``kind`` for a given
      account across both hooks (see ``identity_from_credentials`` /
      ``account_identity``) — otherwise the create-time and login-time identities
      would never dedup against each other.
    - ``value`` — the already-**canonical**, non-empty string form of the account
      id. "Canonical" means the provider has normalized away every representation
      difference (e.g. a BR phone's 12↔13-digit variants collapse to one chosen
      form) so the core can compare with plain equality. Never pass a raw,
      un-normalized, or possibly-empty value; return ``None`` from the hook
      instead when the identity is unknown.
    """

    kind: str
    value: str


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

    # ── Account identity contract (dedup — plano 32) ─────────────────
    # A provider declares HOW to identify the account a channel is bound to; the
    # core does the rest generically (compare, store, unique index, 409 on create,
    # logout on connect). Adding a new provider = implement one or two of these
    # thin hooks; the core never changes and never branches on provider name.
    #
    # Providers split by WHEN the identity is knowable:
    #   • at create — it is inside the submitted credential (WhatsApp Cloud's
    #     ``phone_number_id``, Telegram's ``bot_token``) → override
    #     ``identity_from_credentials``. The core calls it in create/update and
    #     returns 409 before persisting a duplicate.
    #   • only after connection — it appears once the session is live (GOWA's own
    #     phone number after the QR pairing) → override ``account_identity``. The
    #     status sweep reads it and, on a conflict, calls ``reject_duplicate``.
    # A provider may implement both (e.g. Telegram: ``bot_token`` at create AND a
    # live ``bot_id``) — but MUST keep the ``kind`` consistent so the two dedup
    # against each other. Both hooks default to ``None`` (unknown), so ``test`` and
    # any provider that opts out simply never dedups.

    @classmethod
    def identity_from_credentials(cls, creds: dict) -> Optional[AccountIdentity]:
        """The account identity derivable from submitted credentials, or ``None``.

        Override for providers whose identity lives in the credential and is thus
        known at create/update time (before any connection). Return a canonical,
        non-empty :class:`AccountIdentity` or ``None`` when the credentials don't
        determine one (missing/blank field). Must be pure (no network/DB) — the
        core calls it synchronously in the create/update path. Default: ``None``.

        Example (WhatsApp Cloud)::

            pid = (creds.get("phone_number_id") or "").strip()
            return AccountIdentity("phone_number_id", pid) if pid else None
        """
        return None

    def account_identity(self) -> Optional[AccountIdentity]:
        """This live channel's account identity, or ``None`` if not yet knowable.

        Override for providers whose identity only appears once the session is
        connected (e.g. GOWA's own phone number after the QR pairing). Return a
        canonical, non-empty :class:`AccountIdentity`, or ``None`` while unknown
        (not logged in, probe failed). The status sweep calls it periodically and,
        on a conflict with another channel, refuses via :meth:`reject_duplicate`.
        Default: ``None``.

        Example (GOWA)::

            num = digits(self._client.get_own_number())
            return AccountIdentity("phone", canonical_br(num)) if num else None
        """
        return None

    def reject_duplicate(self) -> None:
        """Undo a just-detected duplicate connection (plano 32 D7).

        Called by the sweep when :meth:`account_identity` collided with an existing
        channel: this channel just paired to an account already owned by another
        channel, so we sever it. Default: ``logout()`` (QR/linked-device providers
        like GOWA drop the freshly-paired session), falling back to ``stop()``.
        Providers may override for a cleaner teardown. Must not raise.
        """
        try:
            logout = getattr(self, "logout", None)
            if callable(logout):
                logout()
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass

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
