"""Inbound message ingest funnel (Plano 23 · Fase B3).

The provider-agnostic ingress that previously lived as closures inside
``server/routes/webhook.py::register_routes``: ``ingest_event`` (the single funnel
for ANY channel's inbound), ``_ingest_echo`` (messages sent from the user's own
device), ``_apply_contact_metadata`` (group name / can-send / archive), and
``_resolve_inbound_media`` (download + cache for push providers). Also the shared
``_apply_message_filter`` helper (R5).

Branch by Abstraction: ``register_routes`` builds ONE :class:`MessageIngestService`
(carrying the same infra the closures captured, plus the already-built
:class:`MessagingService` for the transcription/orchestrator handoff) and exposes
``ingest_event`` to the generic webhook route via ``deps.ingest_event`` — the same
wiring as before. No ``server.app`` import.

Semantics are preserved byte-for-byte (the golden-master characterization suite is
the contract).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import namedtuple
from dataclasses import dataclass

from channels.events import InboundEvent
from channels import jid as jid_classifier
from db.repositories import channel_repo, contact_repo, conversation_repo
from plugins.events import apply_filter, emit_with_filter

logger = logging.getLogger(__name__)


# Result of applying a content filter to a message dict (R5): the full (possibly
# mutated) dict — for sites that re-emit it, e.g. ``message.received`` — plus the
# 6 fields the inbound/outgoing pipelines pull back out.
_FilteredMessage = namedtuple(
    "_FilteredMessage",
    ["msg", "text", "msg_id", "reply_to_msg_id", "media_type", "media_path",
     "media_extras"],
)


async def apply_message_filter(filter_name: str, msg: dict, extras: dict):
    """Apply a content filter to a message dict and re-extract the 6 fields.

    Returns a :class:`_FilteredMessage` carrying the full filtered dict (``.msg``)
    and the re-extracted ``text``/``msg_id``/``reply_to_msg_id``/``media_type``/
    ``media_path``/``media_extras`` values, or ``None`` when a plugin aborted the
    action (filter returned ``None``). Each field falls back to the pre-filter
    value so a plugin that drops a key never changes it — byte-identical to the
    previous inline ``.get(key, current)`` ceremony at every call site.
    """
    filtered = await apply_filter(filter_name, msg, extras)
    if filtered is None:
        return None
    return _FilteredMessage(
        msg=filtered,
        text=filtered.get("text", msg.get("text")),
        msg_id=filtered.get("msg_id", msg.get("msg_id")),
        reply_to_msg_id=filtered.get("reply_to_msg_id", msg.get("reply_to_msg_id")),
        media_type=filtered.get("media_type", msg.get("media_type")),
        media_path=filtered.get("media_path", msg.get("media_path")),
        media_extras=filtered.get("media_extras", msg.get("media_extras")),
    )


# ── allowed-JID-types cache ───────────────────────────────────────────────────
#
# The single-slot helper below is bound to the seeded ``default`` GOWA channel and
# kept ONLY for the test suite, which imports ``_read_gowa_allowed_jid_types`` to
# assert ``config.allowed_jid_types`` persistence. The live inbound pipeline reads
# allowed-JID-types per channel via the per-channel cache.

_GOWA_CHANNEL_ID = "default"
_ALLOWED_JID_CACHE: dict = {"types": None, "ts": 0.0}
_ALLOWED_JID_TTL = 30.0
# Per-channel allowed-JID cache for the generic live path (a 2nd GOWA number
# resolves to its own channel — plano 11). Keyed by channel_id; same TTL.
_ALLOWED_JID_BY_CHANNEL: dict = {}


def _read_gowa_allowed_jid_types() -> list[str]:
    """Read the GOWA channel's allowed JID types from its ``config`` JSON.

    Falls back to :data:`channels.jid.DEFAULT_ALLOWED_JID_TYPES` when unset or
    malformed. Synchronous (DB read) — call via ``asyncio.to_thread``.
    """
    try:
        row = channel_repo.get(_GOWA_CHANNEL_ID)
        cfg = row.get("config") if row else None
        if isinstance(cfg, str) and cfg:
            cfg = json.loads(cfg)
        if isinstance(cfg, dict) and "allowed_jid_types" in cfg:
            return jid_classifier.normalize_allowed_types(cfg.get("allowed_jid_types"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Webhook] allowed_jid_types read failed: %s", e)
    return list(jid_classifier.DEFAULT_ALLOWED_JID_TYPES)


def reset_allowed_jid_cache() -> None:
    """Invalidate the cached allowed-JID-types (call after a channel config edit)."""
    _ALLOWED_JID_CACHE["types"] = None
    _ALLOWED_JID_CACHE["ts"] = 0.0
    _ALLOWED_JID_BY_CHANNEL.clear()


async def _gowa_allowed_jid_types() -> list[str]:
    now = time.time()
    cached = _ALLOWED_JID_CACHE["types"]
    if cached is not None and (now - _ALLOWED_JID_CACHE["ts"]) < _ALLOWED_JID_TTL:
        return cached
    types = await asyncio.to_thread(_read_gowa_allowed_jid_types)
    _ALLOWED_JID_CACHE["types"] = types
    _ALLOWED_JID_CACHE["ts"] = now
    return types


def _read_channel_allowed_jid_types(channel_id: str) -> list[str]:
    """Read a specific channel's ``config.allowed_jid_types`` (live-path twin of
    :func:`_read_gowa_allowed_jid_types`, which is hardcoded to ``default``)."""
    try:
        row = channel_repo.get(channel_id)
        cfg = row.get("config") if row else None
        if isinstance(cfg, str) and cfg:
            cfg = json.loads(cfg)
        if isinstance(cfg, dict) and "allowed_jid_types" in cfg:
            return jid_classifier.normalize_allowed_types(cfg.get("allowed_jid_types"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Webhook] allowed_jid_types read failed for %s: %s",
                       channel_id, e)
    return list(jid_classifier.DEFAULT_ALLOWED_JID_TYPES)


async def _channel_allowed_jid_types(channel_id: str) -> list[str]:
    now = time.time()
    cached = _ALLOWED_JID_BY_CHANNEL.get(channel_id)
    if cached is not None and (now - cached[1]) < _ALLOWED_JID_TTL:
        return cached[0]
    types = await asyncio.to_thread(_read_channel_allowed_jid_types, channel_id)
    _ALLOWED_JID_BY_CHANNEL[channel_id] = (types, now)
    return types


# ── service context ───────────────────────────────────────────────────────────

@dataclass
class IngestContext:
    """Infra the inbound funnel needs, lifted from the ``register_routes`` closures.

    ``messaging`` is the already-built :class:`app.services.messaging_service.MessagingService`
    (the ingest funnel hands off to it for transcription / delivery / orchestration);
    ``channel_registry`` and ``media_dir`` feed the push-provider media download.
    No ``server.app`` import.
    """

    agent_handler: object
    ws_manager: object
    state: object
    channel_registry: object
    media_dir: object
    messaging: object  # MessagingService


class MessageIngestService:
    """The inbound ingest funnel (see module docstring)."""

    def __init__(self, ctx: IngestContext):
        self.ctx = ctx

    @property
    def agent_handler(self):
        return self.ctx.agent_handler

    @property
    def ws_manager(self):
        return self.ctx.ws_manager

    @property
    def state(self):
        return self.ctx.state

    @property
    def messaging(self):
        return self.ctx.messaging

    async def _resolve_inbound_media(self, event: InboundEvent):
        """For push providers that only ship a media id (Cloud P16), download +
        cache the file so the existing transcription/render pipeline works.

        Returns ``(media_path, image_path, audio_path)`` — image/audio locals feed
        the per-kind branches in ``_run_one_cycle``. No-op when the event already
        carries a local ``media_path`` (GOWA auto-download)."""
        channel_registry = self.ctx.channel_registry
        media_dir = self.ctx.media_dir
        media_path = event.media_path
        if event.media_type and not media_path:
            extras = event.media_extras or {}
            media_id = extras.get("media_id")
            inst = channel_registry.get(event.channel_id) if channel_registry else None
            if media_id and inst is not None and hasattr(inst, "download_media"):
                try:
                    fname = await asyncio.to_thread(
                        inst.download_media, media_id, media_dir,
                        mime_type=extras.get("mime_type"))
                    if fname:
                        media_path = f"statics/media/{fname}"
                except Exception:
                    logger.warning("[Ingest] media download failed for %s", media_id,
                                   exc_info=True)
        image_path = media_path if event.media_type == "image" else None
        audio_path = media_path if event.media_type == "audio" else None
        return media_path, image_path, audio_path

    def _apply_contact_metadata(self, contact, event: InboundEvent) -> bool:
        """Persist provider-resolved chat metadata onto the contact (plano 13 Fase 0).

        GOWA resolves a group's name / can-send / archive status from its client and
        carries them on the event; other providers leave them ``None`` (no-op).
        Sync — call via ``asyncio.to_thread``. Returns whether the archive status
        actually changed so the caller can emit ``chat.archived`` on the bus
        (parity with the legacy ``/api/webhook`` handler, which fires it when the
        archive flag flips)."""
        changed = False
        archive_changed = False
        if event.is_group:
            if event.group_name and getattr(contact, "group_name", None) != event.group_name:
                contact.is_group = True
                contact.group_name = event.group_name
                changed = True
            if event.can_send is not None and getattr(contact, "can_send", None) != event.can_send:
                contact.can_send = event.can_send
                changed = True
        if (event.is_archived is not None
                and not getattr(contact, "archived_by_app", False)
                and getattr(contact, "is_archived", None) != event.is_archived):
            contact.is_archived = event.is_archived
            changed = True
            archive_changed = True
        if changed:
            contact.save()
        return archive_changed

    async def _ingest_echo(self, event: InboundEvent, channel_id: str, phone: str):
        """Sync a message sent from the user's own device (``direction='out'``).

        Mirror of the GOWA webhook's ``is_from_me`` branch, provider-agnostic: save
        as an operator message + broadcast + ``message.sent`` (source echo), honoring
        ``filter.message.outgoing``. (Outgoing-audio transcription is a follow-up.)"""
        agent_handler = self.agent_handler
        ws_manager = self.ws_manager
        state = self.state
        messaging = self.messaging

        msg_id = event.external_msg_id or str(uuid.uuid4())
        dedup_key = f"{channel_id}:{event.external_msg_id}" if event.external_msg_id else None
        if dedup_key:
            if dedup_key in state.processed_messages:
                return
            state.processed_messages.add(dedup_key)
        text = (event.text or "").strip()
        media_type = event.media_type
        media_path, _img, audio_path = await self._resolve_inbound_media(event)
        media_extras = event.media_extras or None
        if not text and not media_type:
            return
        # Suppress the echo of a message WE just sent through the app.
        if text:
            sent_key = f"{channel_id}:{phone}:{text[:120]}"
            sent_at = state.recently_sent.pop(sent_key, None)
            if sent_at and (time.time() - sent_at) < 30:
                logger.info("[Ingest] Ignoring echo-back for %s/%s", channel_id, phone)
                return

        outgoing_msg = {
            "phone": phone, "channel_id": channel_id, "text": text, "msg_id": msg_id,
            "media_type": media_type, "media_path": media_path, "media_extras": media_extras,
            "reply_to_msg_id": event.reply_to_msg_id, "is_from_me": True,
            "source": "echo", "raw": event.raw or {}, "ts": event.ts or time.time(),
        }
        _filtered = await apply_message_filter(
            "filter.message.outgoing", outgoing_msg, {"phone": phone})
        if _filtered is None:
            logger.info("[Ingest] outgoing echo from %s filtered out", phone)
            return
        text = _filtered.text
        msg_id = _filtered.msg_id
        media_type = _filtered.media_type
        media_path = _filtered.media_path
        media_extras = _filtered.media_extras
        reply_to = _filtered.reply_to_msg_id

        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        await asyncio.to_thread(
            contact.add_message, "assistant", text,
            media_type=media_type, media_path=media_path, msg_id=msg_id,
            reply_to_msg_id=reply_to, status="operator")
        broadcast_msg: dict = {"role": "assistant", "content": text, "ts": time.time(),
                               "msg_id": msg_id, "status": "operator"}
        if reply_to:
            broadcast_msg["reply_to_msg_id"] = reply_to
        if media_type:
            broadcast_msg["media_type"] = media_type
            broadcast_msg["media_path"] = media_path
        await ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": channel_id, "message": broadcast_msg})
        await emit_with_filter("message.sent", {
            "phone": phone, "channel_id": channel_id, "text": text, "msg_id": msg_id,
            "media_type": media_type, "media_path": media_path, "media_extras": media_extras,
            "source": "echo", "status": "operator", "ts": time.time(),
        })

        # Transcribe outgoing (echo) audio when the configured mode includes
        # "sent" (parity with the legacy GOWA handler — channel-aware here so the
        # gate + delivery target resolve from THIS channel's overrides).
        if audio_path:
            out_transcription = await messaging.maybe_transcribe(
                "audio", audio_path,
                phone=phone, source="echo",
                is_group=contact.is_group,
                group_jid=phone if contact.is_group else None,
                channel_id=channel_id,
            )
            if out_transcription:
                await messaging.deliver_audio_transcription(
                    phone, contact, out_transcription, channel_id=channel_id)

    async def ingest_event(self, event: InboundEvent):
        """Single ingress for ANY channel's inbound message (plano 11 / plano 13).

        Mirrors the GOWA webhook's pre-batch steps for a canonical ``InboundEvent``
        (dedup, contact resolution, broadcast, plugin filter/event) and feeds the
        SAME typing-aware orchestrator, keyed by ``(channel_id, chat_id)``. The
        reply is later routed back out through the channel's own adapter. GOWA
        enrichment (group @mention prefix → ``display_text``, ``trigger_ai`` gate
        for group-no-mention, echo via ``direction='out'``, group/archive metadata)
        is carried on the event; defaults keep Cloud/Telegram identical.
        """
        agent_handler = self.agent_handler
        ws_manager = self.ws_manager
        state = self.state
        messaging = self.messaging

        if getattr(event, "kind", "message") != "message":
            return
        channel_id = event.channel_id or "default"
        phone = event.chat_id or event.sender_id
        if not phone:
            return

        # JID-type discard (parity with the legacy GOWA handler — CLAUDE.md
        # "Filtro de tipos de JID"): drop newsletter/broadcast/bot etc. BEFORE any
        # contact is materialized, per the channel's ``config.allowed_jid_types``.
        # GOWA-only (the suffix discriminator is a WhatsApp concept); other
        # providers leave ``event.raw`` without a JID suffix → classified UNKNOWN,
        # which is never gated, so this is a no-op for Cloud/Telegram. Applies to
        # inbound AND the is_from_me echo branch below (same as legacy).
        if getattr(event, "provider", "") == "gowa":
            raw = event.raw or {}
            chat_jid = (raw.get("chat_jid", "") or raw.get("chat_id", "")
                        or raw.get("from", "") or raw.get("jid", ""))
            jid_type = jid_classifier.classify_jid(chat_jid)
            allowed = await _channel_allowed_jid_types(channel_id)
            if not jid_classifier.is_allowed(jid_type, allowed):
                logger.info(
                    "[Ingest] Skipping %s message (jid=%s, type=%s not in allowed=%s)",
                    "outgoing" if getattr(event, "direction", "in") == "out" else "incoming",
                    chat_jid, jid_type, allowed)
                return

        # Echo of a message sent from the user's own device (plano 13 Fase 0).
        if getattr(event, "direction", "in") == "out":
            await self._ingest_echo(event, channel_id, phone)
            return

        # Idempotency by (channel_id, external_msg_id) — providers re-deliver (P18).
        msg_id = event.external_msg_id or str(uuid.uuid4())
        dedup_key = f"{channel_id}:{event.external_msg_id}" if event.external_msg_id else None
        if dedup_key:
            if dedup_key in state.processed_messages:
                return
            state.processed_messages.add(dedup_key)

        text = (event.text or "").strip()
        # display_text = operator/LLM-facing text (GOWA group "[Nome]: …"); falls
        # back to text for providers that don't set it.
        display_text = (event.display_text or text)
        media_type = event.media_type
        media_path, image_path, audio_path = await self._resolve_inbound_media(event)
        media_extras = event.media_extras or None
        reply_to = event.reply_to_msg_id

        if not text and not media_type:
            return

        # Echo suppression (mirror of GOWA): drop a message we just sent out.
        if text:
            sent_key = f"{channel_id}:{phone}:{text[:120]}"
            sent_at = state.recently_sent.pop(sent_key, None)
            if sent_at and (time.time() - sent_at) < 30:
                logger.info("[Ingest] Ignoring echo-back for %s/%s", channel_id, phone)
                return

        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        if event.sender_name and not event.is_group and not contact.is_group:
            await asyncio.to_thread(contact.set_wa_name, event.sender_name)
        # Provider-resolved chat metadata (group name / can-send / archive).
        archive_changed = await asyncio.to_thread(self._apply_contact_metadata, contact, event)
        # chat.archived bus event when the archive flag flipped (parity with the
        # legacy GOWA handler, which emits {phone, archived, ts} on a change).
        if archive_changed:
            await emit_with_filter("chat.archived", {
                "phone": phone, "archived": bool(event.is_archived),
                "ts": time.time(),
            })
        await asyncio.to_thread(contact.increment_unread, msg_id)
        if event.is_group and event.mentioned:
            await asyncio.to_thread(contact.mark_mention)

        logger.info("[Ingest] %s/%s: %s", channel_id, phone,
                    display_text[:80] if display_text else f"[{media_type}]")

        parsed_msg = {
            "phone": phone,
            "channel_id": channel_id,
            "name": event.sender_name,
            "text": display_text,
            "raw_text": text,
            "msg_id": msg_id,
            "reply_to_msg_id": reply_to,
            "media_type": media_type,
            "media_path": media_path,
            "media_extras": media_extras,
            "is_group": event.is_group,
            "group_jid": event.chat_id if event.is_group else None,
            "individual_phone": event.sender_id if event.is_group else None,
            "is_from_me": False,
            "raw": event.raw or {},
            "ts": event.ts or time.time(),
        }
        _filtered = await apply_message_filter(
            "filter.message.before_save", parsed_msg, {"phone": phone})
        if _filtered is None:
            logger.info("[Ingest] inbound from %s filtered out before save", phone)
            return
        parsed_msg = _filtered.msg  # full filtered dict, re-emitted via message.received
        display_text = _filtered.text
        msg_id = _filtered.msg_id
        reply_to = _filtered.reply_to_msg_id
        media_type = _filtered.media_type
        media_path = _filtered.media_path
        media_extras = _filtered.media_extras

        # plano 25 Fase 2 (bug #2): materialize the atendimento thread NOW (t=0), so a
        # brand-new conversation's sidebar row appears together with the tab badge —
        # not only when the batch saves the combined message (t≈message_batch_delay).
        # Idempotent: the batch's add_message re-resolves the SAME thread → no
        # re-announce. Runs AFTER echo suppression (:400) and filter.message.before_save
        # (:447) so a dropped/echo message never creates a conversation.
        conversation_id = await asyncio.to_thread(contact.ensure_conversation_live)

        broadcast_msg: dict = {"role": "user", "content": display_text,
                               "ts": time.time(), "msg_id": msg_id}
        # The frontend matches the sidebar row by conversation_id first (precedence
        # over phone/channel), so a NEW conversation's row updates in place at t=0.
        if conversation_id is not None:
            broadcast_msg["conversation_id"] = conversation_id
        if reply_to:
            broadcast_msg["reply_to_msg_id"] = reply_to
        if media_type:
            broadcast_msg["media_type"] = media_type
            broadcast_msg["media_path"] = media_path
        if event.is_group and event.mentioned:
            broadcast_msg["mentioned"] = True
        await ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": channel_id, "message": broadcast_msg})

        await emit_with_filter("message.received", parsed_msg)

        # Group message with no @mention (group_reply_mode): save to history +
        # message.saved, but do NOT run the agent (plano 13 Fase 0 — trigger_ai).
        if not getattr(event, "trigger_ai", True):
            await asyncio.to_thread(
                contact.add_message, "user", display_text,
                media_type=media_type, media_path=media_path,
                msg_id=msg_id, reply_to_msg_id=reply_to)
            await emit_with_filter("message.saved", {
                "phone": phone, "channel_id": channel_id, "text": display_text,
                "msg_id": msg_id, "media_type": media_type, "media_path": media_path,
                "media_extras": media_extras, "is_group": event.is_group,
                "group_jid": event.chat_id if event.is_group else None,
                "source": "group_no_mention", "ts": time.time(),
            })
            return

        key = (channel_id, phone)
        # A real message proves the contact finished typing — clear any stale
        # `composing` flag so the orchestrator doesn't block on it until the 25s
        # stale timeout (mirror of the legacy GOWA handler; no-op for channels
        # without presence, which never set typing_state).
        ts = state.typing_state.get(key)
        if ts and ts.get("active"):
            state.typing_state[key] = {**ts, "active": False}
        state.pending_messages.setdefault(key, []).append({
            "text": display_text,
            "image_path": image_path if event.media_type == "image" else None,
            "audio_path": audio_path if event.media_type == "audio" else None,
            "media_type": media_type,
            "media_path": media_path,
            "media_extras": media_extras,
            "msg_id": msg_id,
            "reply_to_msg_id": reply_to,
        })
        messaging.schedule_orchestrator(channel_id, phone)

        # Bound the dedup + recently-sent sets (mirror of the legacy GOWA handler).
        if len(state.processed_messages) > 5000:
            for item in list(state.processed_messages)[:2500]:
                state.processed_messages.discard(item)
        now = time.time()
        for k in [k for k, v in state.recently_sent.items() if now - v > 60]:
            state.recently_sent.pop(k, None)
