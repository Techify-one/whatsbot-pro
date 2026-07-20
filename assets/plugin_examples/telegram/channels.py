"""Telegram channel provider (plano 13 Fase 3).

First inbox built 100% on the public channel extension point — proof of the
"multi-canal dinâmico" goal: a third party drops a plugin with a ``Channel``
subclass (``parse_inbound`` + ``send_*`` + ``capabilities``) and it works end to
end, **without touching the core**.

Telegram is a Bot API provider: no QR / linked-device session — the channel
authenticates with a ``bot_token`` (from @BotFather), so ``capabilities.qr =
False`` and ``session_window_hours = 0`` (free text any time). Inbound arrives by
long-poll (``getUpdates`` — see ``lifecycle.py``) or by webhook (the core's
generic ``/api/webhook/telegram/{channel_id}`` route, which calls this
``parse_inbound``).

Credentials model (P24 — provider never touches the channels tables by SQL):
  * With a ``registry`` the ``bot_token`` is read via
    ``registry.get_credential(channel_id, "bot_token")`` (same API the core uses).
  * With ``registry is None`` (unit tests) an in-memory ``credentials`` dict is used.

HTTP uses ``httpx`` like the rest of the core (no extra dependency). All methods
here are synchronous (called via ``asyncio.to_thread`` by the OutboundRouter /
media-cache); the long-poll loop uses its own async client in ``lifecycle.py``.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Optional

import httpx

from channels.base import AccountIdentity, Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.telegram.org"
HTTP_TIMEOUT = 30.0
# Health-probe timeout (status()/getMe). Short on purpose: the panel polls status
# every ~8s, so a slow/hung probe must fail fast and recover on the next cycle
# instead of piling up and flashing a red error on the channel card.
STATUS_TIMEOUT = 8.0


def api_base() -> str:
    """Telegram API base, overridable for tests / self-hosted Bot API servers."""
    return os.environ.get("TELEGRAM_API_BASE", DEFAULT_API_BASE).rstrip("/")


class TelegramChannel(Channel):
    provider = "telegram"

    # ── Contact type (marca por canal — plano tipos-de-contato) ──────
    # Telegram não é WhatsApp: não guarda telefone (o identificador é o chat_id
    # numérico do Telegram, gravado na coluna `phone`), então tem tipo próprio.
    @classmethod
    def contact_type(cls) -> str:
        return "telegram"

    def __init__(self, channel_id: str, registry=None,
                 credentials: Optional[dict] = None):
        super().__init__(
            channel_id,
            ChannelCapabilities(
                qr=False,
                templates=False,
                groups=True,          # bots receive group msgs (privacy mode gates them)
                presence=False,       # Bot API has no "typing received" inbound
                reactions=True,       # setMessageReaction (Bot API 7.0+)
                media=True,
                revoke=True,          # deleteMessage (delete for everyone)
                edit_message=True,    # editMessageText
                inbound_route="poll",  # long-poll by default; webhook also supported
                session_window_hours=0,
                # No QR / connect step: without a bot_token the channel can never
                # poll, send or receive. The core rejects creating one without it —
                # see channels.base.required_credentials.
                required_credentials=("bot_token",),
            ),
        )
        self.registry = registry
        self._credentials = dict(credentials or {})

    # ── Provider descriptor (plano 33) ───────────────────────────────
    @classmethod
    def provider_descriptor(cls) -> dict:
        """Telegram is a Bot API provider: one ``bot_token`` credential, no QR, no
        templates. After create it self-configures (webhook if a public HTTPS domain
        exists, else long-poll) via the ``autoconfigure`` route — declared as the
        ``post_create`` action so the core drives it without knowing "telegram"."""
        return {
            "provider": "telegram",
            "label": "Telegram",
            "color": "purple",
            "credential_fields": [
                {"key": "bot_token", "label": "Bot Token", "type": "secret",
                 "required": True,
                 "placeholder": "123456:ABC-DEF... (do @BotFather)",
                 "help": "Crie um bot com o @BotFather (/newbot) e cole o token. "
                         "Recebe por long-poll (sem host público) — basta criar e "
                         "mandar mensagem ao bot."},
            ],
            "config_fields": [],
            "capabilities": {"needs_qr": False, "templates": False},
            "ai_sequential_default": False,
            "post_create": {"kind": "autoconfigure",
                            "endpoint": "/api/plugins/telegram/autoconfigure",
                            "webhook_path": "/api/webhook/telegram/{channel_id}"},
            "form_component": None,
        }

    # ── Credential access ────────────────────────────────────────────
    def _cred(self, key: str) -> str:
        if self.registry is not None:
            try:
                val = self.registry.get_credential(self.channel_id, key)
            except Exception:  # noqa: BLE001
                val = None
            if val:
                return val
        return self._credentials.get(key, "") or ""

    @property
    def _token(self) -> str:
        return self._cred("bot_token")

    # ── Account identity (dedup contract — plano 32 F5) ──────────────
    # A Telegram bot token is ``{bot_id}:{auth_hash}``, so the numeric ``bot_id``
    # (the bot's Telegram user id — the account) is derivable from the token
    # WITHOUT a network call. We use ``kind="bot_id"`` for BOTH hooks so the
    # create-time identity and the live ``getMe`` identity dedup against each other
    # (plano 32 P1: one consistent kind). Storing the bot_id — not the secret token
    # — also keeps the token out of the ``account_identity`` column.
    @staticmethod
    def _bot_id_from_token(token: str) -> str:
        token = (token or "").strip()
        prefix = token.split(":", 1)[0].strip() if ":" in token else ""
        return prefix if prefix.isdigit() else ""

    @classmethod
    def identity_from_credentials(cls, creds: dict) -> Optional[AccountIdentity]:
        """The bot account (``bot_id``), parsed from the ``bot_token`` at create time."""
        bot_id = cls._bot_id_from_token(creds.get("bot_token") or "")
        return AccountIdentity("bot_id", bot_id) if bot_id else None

    def account_identity(self) -> Optional[AccountIdentity]:
        """Live ``bot_id``: parsed from the token (no network), else via ``getMe``."""
        bot_id = self._bot_id_from_token(self._token)
        if bot_id:
            return AccountIdentity("bot_id", bot_id)
        res = self._request("getMe")
        if res.get("ok"):
            bid = str((res.get("result") or {}).get("id") or "").strip()
            if bid:
                return AccountIdentity("bot_id", bid)
        return None

    def _base_url(self) -> str:
        return f"{api_base()}/bot{self._token}"

    # ── Low-level request ────────────────────────────────────────────
    def _request(self, method: str, payload: Optional[dict] = None,
                 files: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        """Call a Bot API method. Returns ``{ok, result?, error?}`` (never raises)."""
        if not self._token:
            return {"ok": False, "error": "missing_bot_token"}
        url = f"{self._base_url()}/{method}"
        try:
            with httpx.Client(timeout=timeout or HTTP_TIMEOUT) as client:
                if files:
                    resp = client.post(url, data=payload or {}, files=files)
                else:
                    resp = client.post(url, json=payload or {})
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and data.get("ok"):
                return {"ok": True, "result": data.get("result")}
            return {"ok": False,
                    "error": data.get("description") or f"http_{resp.status_code}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ── Lifecycle / status ───────────────────────────────────────────
    def status(self) -> dict:
        if not self._token:
            return {"connected": False, "logged_in": False, "needs_qr": False,
                    "error": "missing_bot_token"}
        res = self._request("getMe", timeout=STATUS_TIMEOUT)
        if res.get("ok"):
            me = res.get("result") or {}
            return {"connected": True, "logged_in": True, "needs_qr": False,
                    "error": None, "own_username": me.get("username"),
                    "own_name": me.get("first_name"), "bot_id": me.get("id")}
        return {"connected": False, "logged_in": False, "needs_qr": False,
                "error": res.get("error")}

    # ── Outbound ─────────────────────────────────────────────────────
    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        # Telegram has no separate mentions list — @username goes inline in the
        # text. ``mentions`` is accepted for contract parity and ignored.
        payload: dict = {"chat_id": chat_id, "text": text}
        if reply_to:
            payload["reply_parameters"] = {"message_id": _to_int(reply_to)}
        res = self._request("sendMessage", payload)
        return _send_result(res)

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        kind = (kind or "").lower()
        method, field = _MEDIA_METHOD.get(kind, ("sendDocument", "document"))
        payload: dict = {"chat_id": chat_id}
        if caption and field != "voice":
            payload["caption"] = caption
        elif caption:
            # voice has no caption support; fall back to a follow-up text.
            payload["caption"] = caption

        local = _local_file(path_or_url)
        if local is not None:
            name = filename or local.name
            with local.open("rb") as fh:
                res = self._request(method, payload, files={field: (name, fh)})
        else:
            # A public URL or a Telegram file_id: pass as the field value.
            payload[field] = path_or_url
            res = self._request(method, payload)
        return _send_result(res)

    def react(self, chat_id: str, msg_id: str, emoji: str) -> None:
        reaction = ([{"type": "emoji", "emoji": emoji}] if emoji else [])
        self._request("setMessageReaction", {
            "chat_id": chat_id, "message_id": _to_int(msg_id), "reaction": reaction})

    def revoke(self, chat_id: str, msg_id: str) -> None:
        """Delete a message for everyone via Bot API ``deleteMessage``."""
        self._request("deleteMessage", {
            "chat_id": chat_id, "message_id": _to_int(msg_id)})

    def edit_text(self, chat_id: str, msg_id: str, text: str) -> SendResult:
        """Edit a sent message's text via Bot API ``editMessageText``."""
        res = self._request("editMessageText", {
            "chat_id": chat_id, "message_id": _to_int(msg_id), "text": text})
        return _send_result(res)

    # mark_read: Bot API has no "mark as read"; inherit the base no-op.

    # ── Inbound media download (mirrors Cloud P16) ───────────────────
    def download_media(self, file_id: str, dest_dir, *,
                       mime_type: Optional[str] = None) -> Optional[str]:
        """Resolve a Telegram ``file_id`` → a cached local file in ``dest_dir``.

        Two calls: ``getFile`` returns a ``file_path``; the bytes are then fetched
        from ``{api_base}/file/bot<token>/<file_path>``. Writes ``tg_<file_id>.<ext>``
        and returns the filename (the core composes ``statics/media/<filename>``),
        or ``None`` on failure (caller falls back to a placeholder)."""
        if not file_id or not self._token:
            return None
        try:
            meta = self._request("getFile", {"file_id": file_id})
            if not meta.get("ok"):
                return None
            file_path = (meta.get("result") or {}).get("file_path") or ""
            if not file_path:
                return None
            url = f"{api_base()}/file/bot{self._token}/{file_path}"
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                blob = client.get(url)
            if blob.status_code != 200:
                logger.warning("telegram download_media %s -> %s", file_id, blob.status_code)
                return None
            data = blob.content
            # Prefer the extension Telegram already encodes in file_path.
            ext = os.path.splitext(file_path)[1]
            if not ext and mime_type:
                ext = mimetypes.guess_extension((mime_type or "").split(";")[0].strip()) or ""
            if not ext and mime_type and "ogg" in mime_type:
                ext = ".ogg"
            if not ext:
                ext = ".bin"
            dest = Path(dest_dir)
            dest.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(c for c in str(file_id) if c.isalnum() or c in "-_")[:64]
            fname = f"tg_{safe_id}{ext}"
            (dest / fname).write_bytes(data)
            return fname
        except Exception:  # noqa: BLE001
            logger.warning("telegram download_media failed for %s", file_id, exc_info=True)
            return None

    # ── Inbound ──────────────────────────────────────────────────────
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Translate a Telegram ``Update`` → ``InboundEvent`` objects.

        Handles ``message`` / ``edited_message`` / ``channel_post`` (→ message),
        ``message_reaction`` (→ reaction) and ``callback_query`` (→ interactive
        message). Returns ``[]`` for update kinds we don't act on.
        """
        events: list[InboundEvent] = []
        if not isinstance(raw, dict):
            return events

        # An edited message keeps the SAME message_id, so mirror it as an "edited"
        # event (new text/caption) instead of re-ingesting it as a brand-new
        # message. The core webhook updates the stored content + broadcasts.
        edited = raw.get("edited_message") or raw.get("edited_channel_post")
        if isinstance(edited, dict):
            base = self._parse_message(edited)
            if base is not None:
                mid = base.external_msg_id
                events.append(InboundEvent(
                    channel_id=self.channel_id, provider=self.provider, kind="edited",
                    external_msg_id=mid, chat_id=base.chat_id, sender_id=base.sender_id,
                    sender_name=base.sender_name, is_group=base.is_group,
                    text=base.text, ts=base.ts,
                    media_extras={"original_message_id": mid, "is_from_me": False},
                    raw=edited))
            return events

        msg = raw.get("message") or raw.get("channel_post")
        if isinstance(msg, dict):
            ev = self._parse_message(msg)
            if ev is not None:
                events.append(ev)
            return events

        reaction = raw.get("message_reaction")
        if isinstance(reaction, dict):
            chat = reaction.get("chat") or {}
            user = reaction.get("user") or {}
            emoji = ""
            for r in reaction.get("new_reaction") or []:
                if isinstance(r, dict) and r.get("type") == "emoji":
                    emoji = r.get("emoji", "")
                    break
            events.append(InboundEvent(
                channel_id=self.channel_id, provider=self.provider, kind="reaction",
                external_msg_id=str(reaction.get("message_id", "")),
                chat_id=str(chat.get("id", "")),
                sender_id=str(user.get("id", "")),
                ts=_to_float(reaction.get("date")),
                media_extras={"emoji": emoji,
                              "reacted_message_id": str(reaction.get("message_id", ""))},
                raw=reaction))
            return events

        cb = raw.get("callback_query")
        if isinstance(cb, dict):
            msg = cb.get("message") or {}
            chat = msg.get("chat") or {}
            user = cb.get("from") or {}
            events.append(InboundEvent(
                channel_id=self.channel_id, provider=self.provider, kind="message",
                external_msg_id=str(cb.get("id", "")),
                chat_id=str(chat.get("id", "")),
                sender_id=str(user.get("id", "")),
                sender_name=_display_name(user),
                is_group=(chat.get("type") in ("group", "supergroup")),
                text=cb.get("data", "") or "",
                media_type="interactive",
                media_extras={"callback_data": cb.get("data")},
                raw=cb))
        return events

    def _parse_message(self, msg: dict) -> Optional[InboundEvent]:
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            return None
        is_group = chat.get("type") in ("group", "supergroup")
        text = msg.get("text") or ""
        caption = msg.get("caption") or ""
        media_type: Optional[str] = None
        media_path: Optional[str] = None
        extras: dict = {}

        if msg.get("photo"):
            # photo is a list of sizes (ascending) — take the largest.
            sizes = [p for p in msg["photo"] if isinstance(p, dict)]
            best = sizes[-1] if sizes else {}
            media_type = "image"
            extras = {"media_id": best.get("file_id"), "mime_type": "image/jpeg"}
        elif msg.get("voice"):
            v = msg["voice"]
            media_type = "audio"
            extras = {"media_id": v.get("file_id"),
                      "mime_type": v.get("mime_type") or "audio/ogg",
                      "duration_ms": (v.get("duration") or 0) * 1000,
                      "is_voice_note": True}
        elif msg.get("audio"):
            a = msg["audio"]
            media_type = "audio"
            extras = {"media_id": a.get("file_id"), "mime_type": a.get("mime_type"),
                      "duration_ms": (a.get("duration") or 0) * 1000,
                      "file_name": a.get("file_name")}
        elif msg.get("video"):
            vid = msg["video"]
            media_type = "video"
            extras = {"media_id": vid.get("file_id"), "mime_type": vid.get("mime_type"),
                      "duration_ms": (vid.get("duration") or 0) * 1000}
        elif msg.get("video_note"):
            vn = msg["video_note"]
            media_type = "video"
            extras = {"media_id": vn.get("file_id"),
                      "duration_ms": (vn.get("duration") or 0) * 1000,
                      "is_video_note": True}
        elif msg.get("document"):
            d = msg["document"]
            media_type = "document"
            extras = {"media_id": d.get("file_id"), "mime_type": d.get("mime_type"),
                      "file_name": d.get("file_name")}
        elif msg.get("sticker"):
            s = msg["sticker"]
            media_type = "sticker"
            extras = {"media_id": s.get("file_id"),
                      "is_animated": s.get("is_animated"), "emoji": s.get("emoji")}
        elif msg.get("location"):
            loc = msg["location"]
            lat, lng = loc.get("latitude"), loc.get("longitude")
            media_type = "location"
            media_path = f"geo:{lat},{lng}"
            extras = {"lat": lat, "lng": lng}
        elif msg.get("contact"):
            c = msg["contact"]
            media_type = "contact"
            name = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
            extras = {"contacts": [{"name": name, "phone": c.get("phone_number")}]}

        # A media item with a caption: use the caption as the message text.
        if caption and not text:
            text = caption
        if caption:
            extras.setdefault("caption", caption)

        # Prune Nones so the extras stay clean (mirrors the GOWA extractor).
        extras = {k: v for k, v in extras.items() if v is not None} or {}

        return InboundEvent(
            channel_id=self.channel_id,
            provider=self.provider,
            kind="message",
            direction="in",
            external_msg_id=str(msg.get("message_id", "")),
            chat_id=chat_id,
            sender_id=str(sender.get("id", "")),
            sender_name=_display_name(sender) or (chat.get("title") or ""),
            is_group=is_group,
            text=text,
            media_type=media_type,
            media_path=media_path,
            media_extras=extras,
            ts=_to_float(msg.get("date")),
            raw=msg,
        )


# image→sendPhoto, audio/voice→sendVoice, video→sendVideo, document→sendDocument,
# sticker→sendSticker. (field name must match the Bot API parameter.)
_MEDIA_METHOD = {
    "image": ("sendPhoto", "photo"),
    "photo": ("sendPhoto", "photo"),
    "audio": ("sendVoice", "voice"),
    "voice": ("sendVoice", "voice"),
    "video": ("sendVideo", "video"),
    "sticker": ("sendSticker", "sticker"),
    "document": ("sendDocument", "document"),
}


def _display_name(user: dict) -> str:
    if not isinstance(user, dict):
        return ""
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    return name or user.get("username") or ""


def _local_file(path_or_url: str) -> Optional[Path]:
    if not path_or_url or path_or_url.startswith(("http://", "https://")):
        return None
    try:
        p = Path(path_or_url)
        return p if p.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _send_result(res: dict) -> SendResult:
    if res.get("ok"):
        result = res.get("result") or {}
        return SendResult(ok=True, external_msg_id=str(result.get("message_id", "")))
    return SendResult(ok=False, error=res.get("error", "send_failed"))


# Exported for the plugin loader (entry.channels → CHANNEL_PROVIDERS).
CHANNEL_PROVIDERS = [TelegramChannel]
