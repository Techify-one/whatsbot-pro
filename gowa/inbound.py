"""GOWA inbound parsing (plano 13 Fase 0).

Turns a raw GOWA webhook body into provider-agnostic ``InboundEvent`` objects so
GOWA ingresses through the SAME funnel as every other channel
(``parse_inbound -> ingest_event / _dispatch_events``) instead of a bespoke
``webhook()`` handler. This is the GOWA equivalent of
``WhatsAppCloudChannel.parse_inbound``.

The heavy media-detection (``_extract_media``) and quoted-message extraction
(``_extract_reply_to``) live here and are re-exported by the legacy
``server/routes/webhook.py`` handler so behaviour stays identical during the
transition (the legacy ``/api/webhook`` route is kept as a fallback while the
generic ``/api/webhook/gowa/{channel_id}`` route is validated live).

GOWA-specific enrichment that the agnostic funnel cannot do (group @mention
resolution, sender-name prefix, group_reply_mode gating, group name / archive /
document-filename / can-send lookups) is performed HERE via the GOWA client and
carried on the ``InboundEvent`` (``display_text``/``trigger_ai``/``mentioned``/
``group_name``/``can_send``/``is_archived``), keeping ``ingest_event`` provider
agnostic. Blocking client/DB calls happen inside ``parse_gowa_inbound`` — the
caller runs it via ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from channels.events import InboundEvent
from channels.jid import phone_from_ack_payload
from agent import group_mentions

logger = logging.getLogger(__name__)


# ── Media detection ───────────────────────────────────────────────────────
# Media types whose payload contains a downloadable ``path`` and can be
# rendered with a player/preview in the chat panel.
_PATHED_MEDIA: tuple[str, ...] = (
    "image", "audio", "video", "sticker", "document",
)


def _coerce_path(raw):
    """Accept either a string path or a dict ``{path, ...}`` from GOWA."""
    if isinstance(raw, str):
        return raw, {}
    if isinstance(raw, dict):
        return (raw.get("path") or ""), dict(raw)
    return "", {}


def _extract_media(data: dict, *, is_from_me: bool, existing_text: str) -> dict:
    """Inspect a GOWA payload and resolve which media (if any) it carries.

    Returns a dict with:

    * ``media_type`` — one of ``image|audio|video|sticker|document|location|
      live_location|poll|interactive|order|product|contact|contacts`` or ``None``.
    * ``media_path`` — path on disk for playable media; ``"geo:lat,lng"`` for
      location/live_location; ``None`` for non-pathed types.
    * ``media_extras`` — type-specific metadata (caption, duration, lat/lng,
      name, options, button_id, …) or ``None`` if there's nothing extra.
    * ``text`` — final placeholder text (existing_text plus any extracted
      caption or auto-generated placeholder like ``"[Vídeo recebido]"``).
    * ``audio_path`` / ``image_path`` / ``document_path`` / ``document_name``
      — back-compat fields. Other call sites still read these individually
      to decide branches (transcription kind, batch path, etc.).

    Detection order matches the original implementation for ``image, audio,
    video_note, document`` then extends it with the new types.
    """
    text = existing_text or ""
    media_type: str | None = None
    media_path: str | None = None
    extras: dict | None = None
    audio_path = image_path = document_path = None
    document_name: str | None = None

    def _placeholder(noun: str) -> str:
        return f"[{noun} enviado]" if is_from_me else f"[{noun} recebido]"

    # — image ————————————————————————————————————————————
    raw = data.get("image")
    if raw:
        p, info = _coerce_path(raw)
        if p:
            image_path = p
            media_type = "image"
            media_path = p
            caption = (info.get("caption") or "").strip()
            if not text and caption:
                text = caption
            elif not text and is_from_me:
                text = "[Imagem enviada]"
            if caption or info.get("mimetype"):
                extras = {
                    k: v for k, v in {
                        "caption": caption or None,
                        "mimetype": info.get("mimetype"),
                    }.items() if v is not None
                } or None

    # — audio ————————————————————————————————————————————
    if media_type is None:
        raw = data.get("audio")
        if raw:
            p, info = _coerce_path(raw)
            if p:
                audio_path = p
                media_type = "audio"
                media_path = p
                if not text:
                    text = _placeholder("Áudio")
                if info.get("duration") or info.get("mimetype"):
                    extras = {
                        k: v for k, v in {
                            "duration_ms": info.get("duration"),
                            "mimetype": info.get("mimetype"),
                        }.items() if v is not None
                    } or None

    # — video_note (voice) — treated as audio ————————————————
    if media_type is None:
        raw = data.get("video_note")
        if raw:
            p, info = _coerce_path(raw)
            if p:
                audio_path = p
                media_type = "audio"
                media_path = p
                if not text:
                    text = _placeholder("Áudio")
                extras = {"is_voice_note": True}

    # — video ————————————————————————————————————————————
    if media_type is None:
        raw = data.get("video")
        if raw:
            p, info = _coerce_path(raw)
            if p:
                media_type = "video"
                media_path = p
                caption = (info.get("caption") or "").strip()
                if not text and caption:
                    text = caption
                elif not text:
                    text = _placeholder("Vídeo")
                extras = {
                    k: v for k, v in {
                        "caption": caption or None,
                        "duration_ms": info.get("duration"),
                        "mimetype": info.get("mimetype"),
                    }.items() if v is not None
                } or None

    # — sticker ——————————————————————————————————————————
    if media_type is None:
        raw = data.get("sticker")
        if raw:
            p, info = _coerce_path(raw)
            if p:
                media_type = "sticker"
                media_path = p
                if not text:
                    text = "[Sticker]"
                if info.get("is_animated") is not None or info.get("mimetype"):
                    extras = {
                        k: v for k, v in {
                            "is_animated": info.get("is_animated"),
                            "mimetype": info.get("mimetype"),
                        }.items() if v is not None
                    } or None

    # — document —————————————————————————————————————————
    if media_type is None:
        raw = data.get("document")
        if raw:
            p, info = _coerce_path(raw)
            if p:
                document_path = p
                media_type = "document"
                media_path = p
                document_name = (info.get("file_name")
                                 or info.get("filename") or "")
                # GOWA echoes the document caption into the top-level body,
                # so `existing_text` may already equal it.
                caption = (info.get("caption") or "").strip() or text
                # With auto-download ON, the GOWA webhook does NOT carry the
                # original filename and the on-disk path is UUID-based, so it
                # can't be recovered here. The webhook layer resolves the real
                # name via GOWA's chat-storage API and rebuilds `text`.
                label = document_name or "documento"
                verb = "enviado" if is_from_me else "recebido"
                text = (f"[Documento {verb}: {label}]"
                        + (f"\n{caption}" if caption else ""))
                extras = {
                    k: v for k, v in {
                        "file_name": document_name or None,
                        "mimetype": info.get("mimetype"),
                        "caption": caption or None,
                    }.items() if v is not None
                } or None

    # — location ——————————————————————————————————————————
    if media_type is None:
        loc = data.get("location")
        if isinstance(loc, dict):
            lat = loc.get("latitude") or loc.get("lat")
            lng = loc.get("longitude") or loc.get("lng")
            if lat is not None and lng is not None:
                name = (loc.get("name") or "").strip()
                address = (loc.get("address") or "").strip()
                media_type = "location"
                media_path = f"geo:{lat},{lng}"
                if not text:
                    if name:
                        text = f"[Localização: {name}]"
                    elif address:
                        text = f"[Localização: {address}]"
                    else:
                        text = f"[Localização: {lat},{lng}]"
                extras = {
                    "lat": lat, "lng": lng,
                    **({"name": name} if name else {}),
                    **({"address": address} if address else {}),
                }

    # — live_location —————————————————————————————————————
    if media_type is None:
        live = data.get("live_location")
        if isinstance(live, dict):
            lat = live.get("latitude") or live.get("lat")
            lng = live.get("longitude") or live.get("lng")
            if lat is not None and lng is not None:
                media_type = "live_location"
                media_path = f"geo:{lat},{lng}"
                if not text:
                    text = "[Localização ao vivo]"
                extras = {"lat": lat, "lng": lng}

    # — poll ——————————————————————————————————————————————
    if media_type is None:
        poll = data.get("poll")
        if isinstance(poll, dict):
            name = (poll.get("name") or "").strip()
            options = poll.get("options") or []
            opt_titles = [
                (o.get("name") or o.get("optionName") or "").strip()
                for o in options if isinstance(o, dict)
            ]
            if name or opt_titles:
                media_type = "poll"
                if not text:
                    text = f"[Enquete: {name or 'sem título'}]"
                extras = {"name": name, "options": opt_titles}

    # — interactive responses (buttons / list) ———————————————
    if media_type is None:
        br = data.get("buttons_response") or data.get("buttonsResponse")
        if isinstance(br, dict):
            title = (br.get("title") or br.get("display_text") or "").strip()
            button_id = (br.get("button_id") or br.get("selected_id") or "").strip()
            media_type = "interactive"
            if not text:
                text = f"[Resposta: {title or button_id or 'sem id'}]"
            extras = {"button_id": button_id, "title": title}
    if media_type is None:
        lr = data.get("list_response") or data.get("listResponse")
        if isinstance(lr, dict):
            title = (lr.get("title") or "").strip()
            row_id = (lr.get("row_id") or lr.get("selected_id") or "").strip()
            media_type = "interactive"
            if not text:
                text = f"[Seleção: {title or row_id or 'sem id'}]"
            extras = {"row_id": row_id, "title": title}

    # — order ————————————————————————————————————————————
    if media_type is None:
        order = data.get("order")
        if isinstance(order, dict):
            item_count = order.get("item_count") or order.get("itemCount")
            media_type = "order"
            if not text:
                if item_count is not None:
                    text = f"[Pedido: {item_count} item(ns)]"
                else:
                    text = "[Pedido recebido]"
            extras = {
                k: v for k, v in {
                    "item_count": item_count,
                    "total": order.get("total"),
                    "currency": order.get("currency"),
                }.items() if v is not None
            } or None

    # — product ——————————————————————————————————————————
    if media_type is None:
        prod = data.get("product")
        if isinstance(prod, dict):
            media_type = "product"
            if not text:
                text = "[Produto compartilhado]"
            extras = {
                "product_id": prod.get("product_id") or prod.get("id"),
                "title": prod.get("title"),
            }

    # — contact / vCard (single or array) ——————————————————
    if media_type is None and not text:
        shared: list[tuple[str, str]] = []
        single = data.get("contact")
        if isinstance(single, dict):
            n = (single.get("displayName") or single.get("display_name")
                 or single.get("name") or "").strip()
            ph = (single.get("phone_number") or single.get("phoneNumber") or "").strip()
            shared.append((n, ph))
        arr = data.get("contacts_array") or data.get("contactsArray")
        if isinstance(arr, list):
            for c in arr:
                if not isinstance(c, dict):
                    continue
                n = (c.get("displayName") or c.get("display_name")
                     or c.get("name") or "").strip()
                ph = (c.get("phone_number") or c.get("phoneNumber") or "").strip()
                shared.append((n, ph))
        if shared:
            media_type = "contact" if len(shared) == 1 else "contacts"
            if len(shared) == 1:
                n, ph = shared[0]
                label = n or ph or "sem nome"
                suffix = f" ({ph})" if ph and n else ""
                text = f"[Contato compartilhado: {label}{suffix}]"
            else:
                names = ", ".join(n or p or "?" for n, p in shared)
                text = f"[Contatos compartilhados ({len(shared)}): {names}]"
            extras = {"contacts": [
                {"name": n, "phone": ph} for n, ph in shared
            ]}

    return {
        "media_type": media_type,
        "media_path": media_path,
        "media_extras": extras,
        "text": text,
        "audio_path": audio_path,
        "image_path": image_path,
        "document_path": document_path,
        "document_name": document_name,
    }


# ── Quoted-message (reply) extraction ─────────────────────────────────────
# Keys that hold the id of the quoted message (the message being replied to).
_REPLY_ID_KEYS = (
    "reply_message_id", "quoted_message_id", "quotedMessageId",
    "replied_id", "repliedId", "reply_to", "reply_to_id", "in_reply_to",
    "quoted_id", "quotedId",
)
# Keys that hold the quoted message id *inside* a context-info object (whatsmeow's
# ContextInfo.StanzaID). Here a bare ``id`` IS the quoted id (unlike a `message`
# wrapper, where ``id`` is the current message's own id).
_REPLY_CTX_KEYS = ("stanza_id", "stanzaId", "StanzaID", "quoted_message_id", "id", "Id")


def _deep_find_reply_id(obj) -> str | None:
    """Recursively scan a payload for a key that clearly names a quoted/replied
    message id (e.g. ``replied_id``, ``reply_message_id``, ``stanza_id``). Requires
    the key to mention repl/quot/stanza AND id, so plain ``id`` or the quoted *text*
    (``quoted_message``) are never mistaken for it."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (str, int)) and v != "" and isinstance(k, str):
                kl = k.lower()
                if (("repl" in kl or "quot" in kl or "stanza" in kl) and "id" in kl):
                    return str(v)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                found = _deep_find_reply_id(v)
                if found:
                    return found
    elif isinstance(obj, list):
        for it in obj:
            found = _deep_find_reply_id(it)
            if found:
                return found
    return None


def _extract_reply_to(data: dict) -> str | None:
    """Best-effort extraction of the quoted message id from a GOWA webhook payload.

    GOWA is not consistent about exposing this for inbound messages (and nests it
    differently across versions), so we probe, in order: flat keys, a nested
    ``message`` object, a ``context_info`` object (flat or inside ``message``), and
    finally a guarded recursive scan. Returns None when nothing quoted-looking is
    present.
    """
    if not isinstance(data, dict):
        return None

    # 1) Flat well-known keys.
    for key in _REPLY_ID_KEYS:
        val = data.get(key)
        if val:
            return str(val)

    # 2) Some GOWA builds nest the text + reply id under a `message` object.
    msg = data.get("message")
    if isinstance(msg, dict):
        for key in _REPLY_ID_KEYS:
            val = msg.get(key)
            if val:
                return str(val)

    # 3) A container describing the quoted message (context info / quoted / reply).
    #    Inside one of these a bare ``id`` IS the quoted message id. Probe both at
    #    the top level and nested under ``message``.
    container_names = ("context_info", "contextInfo", "ContextInfo",
                       "quoted", "quoted_message", "quotedMessage", "reply")
    scopes = [data] + ([msg] if isinstance(msg, dict) else [])
    for scope in scopes:
        for name in container_names:
            ctx = scope.get(name)
            if isinstance(ctx, dict):
                for key in _REPLY_CTX_KEYS:
                    val = ctx.get(key)
                    if val:
                        return str(val)

    # 4) Last resort: scan the whole payload for a reply/quote *id* key.
    return _deep_find_reply_id(data)


# ── Helpers shared with the legacy handler's group-mention logic ──────────
def _phone_from_jid(jid: str) -> str:
    if not isinstance(jid, str) or not jid:
        return ""
    return jid.split("@")[0].split(":")[0]


def _bot_identity(bot_phone: str, bot_name: str) -> tuple[str, str]:
    """Resolve the bot phone/name, defaulting to what group_mentions holds."""
    return (bot_phone or getattr(group_mentions, "_bot_phone", "") or "",
            bot_name or getattr(group_mentions, "_bot_name", "") or "")


def _is_bot_mentioned(text: str, data: dict, bot_phone: str, bot_name: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    if bot_phone and f"@{bot_phone}" in text:
        return True
    if bot_name and f"@{bot_name.lower()}" in text_lower:
        return True
    mentioned = data.get("mentioned_jids", data.get("mentioned", []))
    if mentioned and bot_phone:
        for jid in mentioned:
            if bot_phone in str(jid):
                return True
    return False


def _strip_bot_mention(text: str, bot_phone: str, bot_name: str) -> str:
    if bot_phone:
        text = text.replace(f"@{bot_phone}", "").strip()
    if bot_name:
        text = re.sub(rf"@{re.escape(bot_name)}", "", text, flags=re.IGNORECASE).strip()
    return text


# ── Main entry point ──────────────────────────────────────────────────────
def parse_gowa_inbound(body: dict, *, channel_id: str = "default", client=None,
                       bot_phone: str = "", bot_name: str = "",
                       group_mode: str = "mention_only") -> list[InboundEvent]:
    """Translate a raw GOWA webhook ``body`` into ``InboundEvent`` objects.

    Mirrors the legacy ``webhook()`` handler's branching but produces events
    instead of acting. Blocking client/DB calls (group name/archive/filename and
    @mention lookups) run here — call via ``asyncio.to_thread``.
    """
    if not isinstance(body, dict):
        return []
    event = body.get("event", "")
    data = body.get("payload", body.get("data", body))
    bot_phone, bot_name = _bot_identity(bot_phone, bot_name)

    def _ev(**kw) -> InboundEvent:
        kw.setdefault("channel_id", channel_id)
        kw.setdefault("provider", "gowa")
        return InboundEvent(**kw)

    # ── Non-message GOWA events (panel/bus parity) ───────────────────
    if event == "message.reaction":
        react_phone = _phone_from_jid(data.get("chat_id", "") or data.get("from", ""))
        return [_ev(kind="reaction",
                    external_msg_id=data.get("id", ""),
                    chat_id=react_phone,
                    sender_id=_phone_from_jid(data.get("from", "")),
                    ts=data.get("timestamp") or 0.0,
                    media_extras={
                        "emoji": data.get("reaction", ""),
                        "reacted_message_id": data.get("reacted_message_id", ""),
                        "is_from_me": bool(data.get("is_from_me", False)),
                    }, raw=data)]

    if event == "message.edited":
        return [_ev(kind="edited",
                    external_msg_id=data.get("id", ""),
                    chat_id=_phone_from_jid(data.get("chat_id", "") or data.get("from", "")),
                    sender_id=_phone_from_jid(data.get("from", "")),
                    text=data.get("body", "") or data.get("content", ""),
                    ts=data.get("timestamp") or 0.0,
                    media_extras={
                        "original_message_id": data.get("original_message_id", ""),
                        "is_from_me": bool(data.get("is_from_me", False)),
                    }, raw=data)]

    if event == "message.revoked":
        return [_ev(kind="revoked",
                    external_msg_id=data.get("id", ""),
                    chat_id=_phone_from_jid(data.get("chat_id", "") or data.get("from", "")),
                    sender_id=_phone_from_jid(data.get("from", "")),
                    ts=data.get("timestamp") or 0.0,
                    media_extras={
                        "revoked_message_id": data.get("revoked_message_id", ""),
                        "revoked_from_me": bool(data.get("revoked_from_me", False)),
                        "revoked_chat": data.get("revoked_chat", ""),
                    }, raw=data)]

    if event == "message.deleted":
        return [_ev(kind="deleted",
                    chat_id=_phone_from_jid(data.get("chat_id", "") or data.get("from", "")),
                    sender_id=_phone_from_jid(data.get("from", "")),
                    ts=data.get("timestamp") or 0.0,
                    media_extras={
                        "deleted_message_id": data.get("deleted_message_id", ""),
                        "original_content": data.get("original_content", ""),
                        "original_sender": data.get("original_sender", ""),
                        "original_timestamp": data.get("original_timestamp"),
                        "was_from_me": bool(data.get("was_from_me", False)),
                    }, raw=data)]

    if event == "group.participants":
        return [_ev(kind="group_participants",
                    chat_id=data.get("chat_id", ""),
                    media_extras={"type": data.get("type", ""),
                                  "jids": data.get("jids", []) or []}, raw=data)]

    if event == "group.joined":
        return [_ev(kind="group_joined",
                    chat_id=data.get("chat_id", "") or data.get("group_jid", ""),
                    raw=data)]

    if event == "call.offer":
        return [_ev(kind="call",
                    chat_id=_phone_from_jid(data.get("from", "")),
                    media_extras={
                        "call_id": data.get("call_id", ""),
                        "auto_rejected": bool(data.get("auto_rejected", False)),
                        "remote_platform": data.get("remote_platform", ""),
                        "remote_version": data.get("remote_version", ""),
                        "group_jid": data.get("group_jid"),
                    }, raw=data)]

    if isinstance(event, str) and event.startswith("newsletter."):
        return [_ev(kind="newsletter", media_extras={"subtype": event}, raw=data)]

    if event == "chat_presence":
        from_jid = data.get("from", "")
        return [_ev(kind="presence",
                    chat_id=_phone_from_jid(from_jid),
                    media_extras={"state": data.get("state", ""),
                                  "media": data.get("media", "") or "text"}, raw=data)]

    if event == "message.ack":
        # Normalise to the uniform receipt shape (one event per id, extras{status})
        # so _dispatch_events handles GOWA and Cloud receipts identically.
        status = {"delivered": "delivered", "read": "read",
                  "read-self": "read"}.get(data.get("receipt_type", ""))
        ids = data.get("ids", []) or []
        if not status or not ids:
            return []
        ack_phone = phone_from_ack_payload(data)
        return [_ev(kind="receipt", external_msg_id=mid, chat_id=ack_phone,
                    direction="out", media_extras={"status": status}, raw=data)
                for mid in ids]

    # Only message events past here.
    if event and event not in ("message", "message:received", ""):
        return []
    if not isinstance(data, dict):
        return []

    return _parse_message(data, channel_id=channel_id, client=client,
                          bot_phone=bot_phone, bot_name=bot_name,
                          group_mode=group_mode)


def _parse_message(data: dict, *, channel_id: str, client, bot_phone: str,
                   bot_name: str, group_mode: str) -> list[InboundEvent]:
    is_from_me = data.get("is_from_me", data.get("from_me", data.get("FromMe", False)))
    msg_id = (data.get("id", data.get("Id", data.get("message_id", "")))
              or str(uuid.uuid4()))
    reply_to_msg_id = _extract_reply_to(data)

    text = (data.get("content", "") or data.get("body", "") or data.get("Body", "")
            or data.get("message", "") or data.get("text", "")).strip()

    extracted = _extract_media(data, is_from_me=is_from_me, existing_text=text)
    text = extracted["text"]
    media_type = extracted["media_type"]
    media_path = extracted["media_path"]
    media_extras = extracted["media_extras"]
    document_name = extracted["document_name"]

    chat_jid = (data.get("chat_jid", "") or data.get("chat_id", "")
                or data.get("from", "") or data.get("jid", ""))
    sender_jid = (data.get("sender_jid", "") or data.get("sender", "")
                  or data.get("from", ""))
    is_group = "@g.us" in chat_jid

    # Resolve the document's real filename via GOWA's chat storage (the webhook
    # omits it with auto-download on).
    if media_type == "document" and client is not None:
        try:
            real_name = client.get_message_filename(chat_jid, msg_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[gowa.inbound] document filename lookup failed: %s", e)
            real_name = ""
        doc_caption = (media_extras or {}).get("caption") or ""
        doc_label = real_name or document_name or "documento"
        verb = "enviado" if is_from_me else "recebido"
        text = (f"[Documento {verb}: {doc_label}]"
                + (f"\n{doc_caption}" if doc_caption else ""))
        if real_name:
            media_extras = {**(media_extras or {}), "file_name": real_name}

    group_name = can_send = is_archived = None
    mentioned = False
    trigger_ai = True

    if is_group:
        phone = chat_jid
        individual_phone = (sender_jid.split("@")[0].split(":")[0]
                            if sender_jid else "")
        from_name = (data.get("from_name", "") or data.get("pushName", "")
                     or data.get("notify", ""))
        # Group name (subject in payload, else GOWA API).
        group_name = (data.get("subject", "") or data.get("group_name", "")
                      or data.get("group_subject", "") or data.get("chat_name", ""))
        if not group_name and client is not None:
            try:
                group_name = client.get_group_name(phone) or ""
            except Exception as e:  # noqa: BLE001
                logger.warning("[gowa.inbound] group name fetch failed: %s", e)
        # Can the bot send in this group?
        if bot_phone and client is not None:
            try:
                can_send = client.can_bot_send_in_group(phone, bot_phone)
            except Exception as e:  # noqa: BLE001
                logger.warning("[gowa.inbound] can_send check failed: %s", e)
        # @mention resolution + sender-name prefix.
        if from_name:
            group_mentions.record_pushname([individual_phone], from_name)
        try:
            lookup = group_mentions.build_lookup(chat_jid)
        except Exception as e:  # noqa: BLE001
            logger.warning("[gowa.inbound] member lookup failed for %s: %s", chat_jid, e)
            lookup = {}
        sender_label = lookup.get(individual_phone) or from_name or individual_phone
        display_text = (f"[{sender_label}]: {group_mentions.apply_incoming(lookup, text)}"
                        if text else "")
        mentioned = _is_bot_mentioned(text, data, bot_phone, bot_name)
        if group_mode == "never" or (group_mode == "mention_only" and not mentioned):
            trigger_ai = False
        elif mentioned:
            cleaned = _strip_bot_mention(text, bot_phone, bot_name)
            if cleaned:
                display_text = f"[{sender_label}]: {group_mentions.apply_incoming(lookup, cleaned)}"
        logger.info("[gowa.inbound] Group payload: %s",
                    json.dumps(data, default=str, ensure_ascii=False)[:1000])
    else:
        conv_jid = chat_jid or sender_jid
        phone = (conv_jid.split("@")[0].split(":")[0] if conv_jid else "")
        individual_phone = phone
        from_name = (data.get("from_name", "") or data.get("pushName", "")
                     or data.get("notify", ""))
        display_text = text

    # Archive status (best-effort; ingest only applies it when not archived-by-app).
    if not is_from_me and client is not None and phone:
        try:
            is_archived = bool(client.is_chat_archived(chat_jid))
        except Exception as e:  # noqa: BLE001
            logger.warning("[gowa.inbound] archive check failed for %s: %s", phone, e)

    if not phone or (not text and not media_type):
        return []

    chat_id = chat_jid if is_group else phone
    return [InboundEvent(
        channel_id=channel_id, provider="gowa", kind="message",
        direction="out" if is_from_me else "in",
        external_msg_id=msg_id,
        chat_id=chat_id,
        sender_id=individual_phone,
        sender_name=from_name,
        is_group=is_group,
        text=text,
        display_text=display_text,
        trigger_ai=trigger_ai,
        reply_to_msg_id=reply_to_msg_id,
        mentioned=mentioned,
        media_type=media_type,
        media_path=media_path,
        media_extras=media_extras or {},
        group_name=group_name or None,
        can_send=can_send,
        is_archived=is_archived,
        ts=data.get("timestamp") or 0.0,
        raw=data,
    )]
