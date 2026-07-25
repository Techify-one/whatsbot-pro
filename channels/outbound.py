"""Outbound router (plano 11 Fase 2/3).

Single send surface the runtime uses INSTEAD of ``gowa_client`` directly. Every
outbound leg goes through ``registry.get(channel_id)`` so a reply lands on the
channel the conversation belongs to (GOWA, WhatsApp Cloud, Telegram, …) without a
single ``if provider ==`` in the pipeline.

Capability-aware (plano 11 Fase 3): presence / reactions / @mentions are only
attempted when the target channel's ``ChannelCapabilities`` declares support, so a
Cloud channel (``presence=False``, ``groups=False``) silently skips them instead
of erroring. Decisions are driven by CAPABILITY, never by provider name.

Blocking I/O lives inside the provider methods (httpx/requests); callers wrap the
router call in ``asyncio.to_thread`` on the async hot path.
"""

from __future__ import annotations

import logging
import time

from channels.base import ChannelCapabilities, SendResult

logger = logging.getLogger(__name__)

# Used when a channel id has no live instance (mis-config / not yet materialised).
_EMPTY_CAPS = ChannelCapabilities()


class OutboundRouter:
    def __init__(self, registry) -> None:
        self._registry = registry

    # ── Lookups ──────────────────────────────────────────────────────
    def get(self, channel_id: str):
        if self._registry is None:
            return None
        return self._registry.get(channel_id)

    def capabilities(self, channel_id: str) -> ChannelCapabilities:
        inst = self.get(channel_id)
        caps = getattr(inst, "capabilities", None)
        return caps if isinstance(caps, ChannelCapabilities) else _EMPTY_CAPS

    def supports(self, channel_id: str, cap: str) -> bool:
        return bool(getattr(self.capabilities(channel_id), cap, False))

    def session_open(self, channel_id: str, last_inbound_ts: float | None, *,
                     by_human: bool = False) -> bool:
        """Whether FREE TEXT is allowed to ``channel_id`` right now (plano 11 Fase 6).

        Channels with ``session_window_hours == 0`` (GOWA/linked-device) are always
        open. For windowed providers (WhatsApp Cloud = 24h) free text is only
        allowed within N hours of the LAST INBOUND; outside the window an approved
        template (HSM, via :meth:`send_template`) is required. Driven by CAPABILITY,
        never provider name. The agentic auto-reply is inherently in-window (it
        answers a just-received inbound); this guards proactive/operator sends.

        ``by_human=True`` (plano 46 · 02.2) additionally honours
        ``capabilities.human_window_hours`` — the EXTENDED window a provider grants
        to a real human reply (Messenger/Instagram: 7 days via the ``HUMAN_AGENT``
        tag). Only the operator-initiated send paths pass it; the AI never does, so
        the AI can never reach a human-only escape hatch.
        """
        caps = self.capabilities(channel_id)
        hours = caps.session_window_hours
        if by_human:
            hours = max(hours, getattr(caps, "human_window_hours", 0) or 0)
        if not hours:
            return True
        if not last_inbound_ts:
            return False
        return (time.time() - last_inbound_ts) <= hours * 3600

    def check_phone(self, channel_id: str, phone: str) -> dict:
        """Verify ``phone`` on the conversation's channel (plano 21).

        Routes to the channel instance so the "Nova conversa" check uses the RIGHT
        provider: GOWA actually queries WhatsApp, while Cloud API / Telegram inherit
        the base "assume valid" (they can't verify before sending). Without a live
        instance the number is assumed valid rather than erroring. Provider errors
        (e.g. GOWA HTTP 401 when disconnected) propagate so the UI shows them."""
        inst = self.get(channel_id)
        if inst is None:
            logger.warning("OutboundRouter: no live channel %r for check_phone", channel_id)
            return {"registered": True, "canonical_phone": phone, "name": ""}
        return inst.check_phone(phone)

    # ── Sends ────────────────────────────────────────────────────────
    def send_text(self, channel_id: str, chat_id: str, text: str, *,
                  reply_to: str | None = None, mentions=None) -> SendResult:
        inst = self.get(channel_id)
        if inst is None:
            logger.warning("OutboundRouter: no live channel %r for send_text", channel_id)
            return SendResult(ok=False, error="channel_not_registered")
        try:
            return inst.send_text(chat_id, text, reply_to=reply_to, mentions=mentions)
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def send_media(self, channel_id: str, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename: str | None = None) -> SendResult:
        inst = self.get(channel_id)
        if inst is None:
            return SendResult(ok=False, error="channel_not_registered")
        try:
            return inst.send_media(chat_id, kind, path_or_url,
                                   caption=caption, filename=filename)
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def send_template(self, channel_id: str, chat_id: str, template_name: str,
                      *, lang: str = "pt_BR", components=None) -> SendResult:
        inst = self.get(channel_id)
        if inst is None:
            return SendResult(ok=False, error="channel_not_registered")
        try:
            return inst.send_template(chat_id, template_name, lang=lang,
                                      components=components)
        except NotImplementedError:
            return SendResult(ok=False, error="templates_not_supported")
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def list_templates(self, channel_id: str) -> list[dict]:
        """Approved templates for a channel (capability-gated, mirrors send_template).

        Returns ``[]`` when the channel doesn't support templates, isn't live, or the
        provider call fails — so the picker degrades cleanly instead of erroring.
        """
        if not self.supports(channel_id, "templates"):
            return []
        inst = self.get(channel_id)
        if inst is None:
            return []
        try:
            return inst.list_templates() or []
        except NotImplementedError:
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("list_templates failed on %s: %s", channel_id, e)
            return []

    def create_template(self, channel_id: str, name: str, **kwargs) -> dict:
        """Create a template on a channel (capability-gated, mirrors list_templates).

        Returns ``{ok, ...}``; ``{ok: False, error}`` when the channel can't manage
        templates, isn't live, or the provider call fails — so the caller maps it to
        a clean API error instead of a 500.
        """
        if not self.supports(channel_id, "templates"):
            return {"ok": False, "error": "templates_not_supported"}
        inst = self.get(channel_id)
        if inst is None:
            return {"ok": False, "error": "channel_not_registered"}
        try:
            return inst.create_template(name, **kwargs)
        except NotImplementedError:
            return {"ok": False, "error": "templates_not_supported"}
        except Exception as e:  # noqa: BLE001
            logger.warning("create_template failed on %s: %s", channel_id, e)
            return {"ok": False, "error": str(e)}

    def upload_template_example(self, channel_id: str, file_bytes: bytes,
                                mime: str, filename: str) -> dict:
        """Upload a media sample and return its provider handle (plano 73).

        Capability-gated exactly like ``create_template``: the returned handle is
        what ``create_template(header_handle=...)`` expects. Returns
        ``{ok, handle?, error?}`` — never raises, so the route maps a failure to a
        clean API error instead of a 500.
        """
        if not self.supports(channel_id, "templates"):
            return {"ok": False, "error": "templates_not_supported"}
        inst = self.get(channel_id)
        if inst is None:
            return {"ok": False, "error": "channel_not_registered"}
        try:
            return inst.upload_example(file_bytes, mime, filename)
        except NotImplementedError:
            return {"ok": False, "error": "templates_not_supported"}
        except Exception as e:  # noqa: BLE001
            logger.warning("upload_template_example failed on %s: %s", channel_id, e)
            return {"ok": False, "error": str(e)}

    def delete_template(self, channel_id: str, name: str) -> dict:
        """Delete a template (all languages) on a channel (capability-gated)."""
        if not self.supports(channel_id, "templates"):
            return {"ok": False, "error": "templates_not_supported"}
        inst = self.get(channel_id)
        if inst is None:
            return {"ok": False, "error": "channel_not_registered"}
        try:
            return inst.delete_template(name)
        except NotImplementedError:
            return {"ok": False, "error": "templates_not_supported"}
        except Exception as e:  # noqa: BLE001
            logger.warning("delete_template failed on %s: %s", channel_id, e)
            return {"ok": False, "error": str(e)}

    # ── Optional, capability-gated, best-effort ──────────────────────
    def send_presence(self, channel_id: str, chat_id: str, state: str) -> None:
        if not self.supports(channel_id, "presence"):
            return
        inst = self.get(channel_id)
        if inst is None:
            return
        try:
            inst.send_presence(chat_id, state)
        except Exception:  # noqa: BLE001
            logger.debug("send_presence failed on %s", channel_id, exc_info=True)

    def mark_read(self, channel_id: str, chat_id: str, msg_id: str) -> None:
        inst = self.get(channel_id)
        if inst is None or not msg_id:
            return
        try:
            inst.mark_read(chat_id, msg_id)
        except Exception:  # noqa: BLE001
            logger.debug("mark_read failed on %s", channel_id, exc_info=True)

    def fetch_avatar(self, channel_id: str, chat_id: str) -> bytes | None:
        """Profile photo bytes for ``chat_id`` on ``channel_id`` (plano 38 F5), via the
        channel's ``fetch_avatar`` hook. Returns ``None`` when the channel has no live
        instance or its provider doesn't implement avatars (Telegram/Cloud) — the
        ``None`` is the gate, so no cross-provider GOWA fetch ever happens."""
        inst = self.get(channel_id)
        if inst is None or not chat_id:
            return None
        try:
            data = inst.fetch_avatar(chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("fetch_avatar failed on %s", channel_id, exc_info=True)
            return None
        return data if isinstance(data, bytes) else None

    def react(self, channel_id: str, chat_id: str, msg_id: str, emoji: str) -> None:
        if not self.supports(channel_id, "reactions"):
            return
        inst = self.get(channel_id)
        if inst is None:
            return
        try:
            inst.react(chat_id, msg_id, emoji)
        except Exception:  # noqa: BLE001
            logger.debug("react failed on %s", channel_id, exc_info=True)

    def revoke(self, channel_id: str, chat_id: str, msg_id: str) -> bool:
        """Revoke (delete-for-everyone) a message via the conversation's channel.

        Best-effort and provider-agnostic: a channel that can't revoke inherits
        ``Channel.revoke``'s no-op (e.g. WhatsApp Cloud), so the operator action
        degrades silently instead of erroring. Returns True when a live channel
        handled it. Driven by the channel of the conversation, never by name.
        """
        inst = self.get(channel_id)
        if inst is None or not msg_id:
            return False
        try:
            inst.revoke(chat_id, msg_id)
            return True
        except Exception:  # noqa: BLE001
            logger.debug("revoke failed on %s", channel_id, exc_info=True)
            return False

    def edit_text(self, channel_id: str, chat_id: str, msg_id: str,
                  text: str) -> SendResult:
        """Edit a sent message's text via the conversation's channel (capability-gated).

        Returns ``SendResult(ok=False, error="edit_not_supported")`` when the channel
        doesn't declare ``edit_message`` or isn't live, so the caller maps it to a
        clean API error. Driven by CAPABILITY, never provider name.
        """
        if not self.supports(channel_id, "edit_message"):
            return SendResult(ok=False, error="edit_not_supported")
        inst = self.get(channel_id)
        if inst is None or not msg_id:
            return SendResult(ok=False, error="channel_not_registered")
        try:
            return inst.edit_text(chat_id, msg_id, text)
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))
