"""Shared base for the Meta Graph messaging providers (plano 46 · 01-B, D2).

Facebook Messenger and Instagram Direct are the SAME API with a different host,
token namespace and id namespace (PSID × IGSID): the same
``{recipient}{message}`` body on ``/me/messages``, the same
``entry[].messaging[]`` webhook shape, the same 24h window with a ``HUMAN_AGENT``
escape hatch, and the same ``X-Hub-Signature-256`` verification. Everything that
is common lives here so a provider plugin only declares host, credential keys,
capabilities and its descriptor.

This lives INSIDE the plugin (plano 76 · F9 / sub-plano 03): the ``.zip`` is
self-sufficient, carrying everything the provider needs that the core does not.
Sibling modules import it relatively (``from .meta_graph import …``), which the
plugin loader resolves as ``whatsbot_plugins.instagram.meta_graph``. It registers
NO provider by itself: it is abstract (no ``provider`` string, no descriptor), so
nothing appears in the channel picker until a subclass ships. This file is the
Instagram plugin's OWN copy of the base — the twin of the one in the
``facebook_messenger`` plugin (D2 trade-off: two Meta channels, two copies — the
price of self-contained zips; a fix to the Meta API must land in BOTH copies).

Note this is NOT the WhatsApp Cloud API shape. Cloud walks
``entry[].changes[].value.messages[]`` and uploads media to ``/media``; Messenger
and Instagram walk ``entry[].messaging[]`` and send media by **public URL**
(D4 — see the sibling ``media_urls`` module).

Credentials model (same as every provider): with a ``registry`` the values come
from ``registry.get_credential(channel_id, key)``; without one (unit tests) from
an in-memory ``credentials`` dict.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from channels.base import Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent
from .media_urls import public_media_url

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 20.0
STATUS_TIMEOUT = 8.0
# Anexo por URL: a Meta BAIXA o arquivo dentro da própria chamada, então um vídeo
# de dezenas de MB estoura facilmente os 20s do timeout padrão (o envio falhava
# com "The read operation timed out" mesmo tendo dado certo do lado deles).
MEDIA_TIMEOUT = 120.0
# Graph v25.0 is the current release (2026-02-18) — kept configurable per channel
# via the ``graph_api_version`` credential/config field, like whatsapp_cloud does.
DEFAULT_GRAPH_VERSION = "v25.0"

# Meta's attachment types on BOTH directions. ``file`` is Meta's name for what the
# rest of the WhatsBot calls ``document``.
_ATTACHMENT_TO_MEDIA = {
    "image": "image",
    "audio": "audio",
    "video": "video",
    "file": "document",
    "location": "location",
    "story_mention": "image",
    "share": "share",
}
_MEDIA_TO_ATTACHMENT = {
    "image": "image",
    "sticker": "image",
    "audio": "audio",
    "voice": "audio",
    "video": "video",
    "document": "file",
}

# MIME major type -> tipo de anexo da Meta. A Send API valida o tipo declarado
# CONTRA o Content-Type do arquivo que ela baixa: um .mp4 mandado como ``file``
# (o mapeamento de "documento") é recusado com ``(#100) … code 100/2018007``
# ("Upload attachment failure"). Só ``application/*`` e afins viram ``file``.
_MIME_TO_ATTACHMENT = {
    "image": "image",
    "video": "video",
    "audio": "audio",
}


def attachment_type_for(kind: str, path_or_url: str = "",
                        filename: Optional[str] = None) -> str:
    """Tipo de anexo da Meta para um arquivo — pelo CONTEÚDO, não pelo rótulo.

    O painel deixa o operador mandar qualquer arquivo pelo botão "documento", mas
    a Meta rejeita o anexo quando o tipo declarado não bate com o MIME real da
    URL. Então o MIME (deduzido da extensão do arquivo/URL) manda, e o ``kind``
    pedido pelo core só decide quando não dá pra deduzir nada.
    """
    for candidate in (filename, path_or_url):
        name = (candidate or "").split("?")[0].strip()
        if not name:
            continue
        mime = mimetypes.guess_type(name)[0] or ""
        major = mime.split("/")[0] if mime else ""
        mapped = _MIME_TO_ATTACHMENT.get(major)
        if mapped:
            return mapped
        if mime:
            return "file"
    return _MEDIA_TO_ATTACHMENT.get((kind or "").lower(), "file")


class MetaGraphChannel(Channel):
    """Base class for providers speaking the Meta Send/Webhook API."""

    # ── Provider knobs (a subclass overrides what differs) ───────────
    graph_host = "graph.facebook.com"        # Instagram uses graph.instagram.com
    token_credential_key = "access_token"    # Messenger: page_access_token
    #: Fields requested when enriching a sender's profile; empty disables it.
    profile_fields: tuple = ()
    #: Cache TTL (seconds) for a resolved sender name.
    profile_cache_ttl = 6 * 3600

    def __init__(self, channel_id: str,
                 capabilities: Optional[ChannelCapabilities] = None,
                 registry=None, credentials: Optional[dict] = None):
        super().__init__(channel_id, capabilities)
        self.registry = registry
        self._credentials = dict(credentials or {})
        # psid -> (resolved_at, name)
        self._profile_cache: dict = {}
        # (fetched_at, config dict) — see _channel_config
        self._config_cache: Optional[tuple] = None

    # ── Credentials ──────────────────────────────────────────────────
    def _cred(self, key: str) -> str:
        if self.registry is not None:
            try:
                val = self.registry.get_credential(self.channel_id, key)
            except Exception:  # noqa: BLE001
                val = None
            if val:
                return val
        return self._credentials.get(key, "") or ""

    def _channel_config(self) -> dict:
        """This channel's ``config`` JSON (descriptor ``config_fields``), cached.

        Read through the registry's DB API (never raw SQL — P24) with a short TTL:
        it is consulted on every send, and an edit only has to take effect within
        seconds.
        """
        if self.registry is None:
            return {}
        cached = self._config_cache
        if cached and (time.time() - cached[0]) < 30:
            return cached[1]
        cfg: dict = {}
        try:
            row = self.registry.get_channel(self.channel_id) or {}
            raw_cfg = row.get("config")
            if isinstance(raw_cfg, str):
                import json as _json
                raw_cfg = _json.loads(raw_cfg or "{}")
            if isinstance(raw_cfg, dict):
                cfg = raw_cfg
        except Exception:  # noqa: BLE001
            logger.debug("config lookup failed for %s", self.channel_id, exc_info=True)
        self._config_cache = (time.time(), cfg)
        return cfg

    def _config(self, key: str, default: str = "") -> str:
        """A non-secret channel config value, falling back to the credential bag
        (so a unit test can pass everything in a single dict)."""
        val = self._channel_config().get(key)
        if val not in (None, ""):
            return str(val)
        return self._credentials.get(key, "") or default

    def _config_bool(self, key: str, default: bool = False) -> bool:
        val = self._channel_config().get(key, self._credentials.get(key))
        if val is None or val == "":
            return default
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "on", "yes", "sim")
        return bool(val)

    @property
    def _access_token(self) -> str:
        return self._cred(self.token_credential_key)

    @property
    def _app_secret(self) -> str:
        return self._cred("app_secret")

    @property
    def _graph_version(self) -> str:
        return (self._config("graph_api_version")
                or self._cred("graph_api_version")
                or os.environ.get("META_GRAPH_VERSION")
                or DEFAULT_GRAPH_VERSION)

    def _graph_base(self) -> str:
        return f"https://{self.graph_host}/{self._graph_version}"

    # ── Auth helpers ─────────────────────────────────────────────────
    def _appsecret_proof(self) -> str:
        """``HMAC_SHA256(access_token, app_secret)`` — required by every Graph call
        when the app has "Require App Secret" on, harmless otherwise."""
        token, secret = self._access_token, self._app_secret
        if not token or not secret:
            return ""
        return hmac.new(secret.encode("utf-8"), token.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def _auth_params(self, extra: Optional[dict] = None) -> dict:
        params = {"access_token": self._access_token}
        proof = self._appsecret_proof()
        if proof:
            params["appsecret_proof"] = proof
        if extra:
            params.update(extra)
        return params

    # ── Inbound signature (plano 46 · 01-A / D3) ─────────────────────
    def verify_inbound_signature(self, raw_body: bytes, headers) -> bool:
        """Validate ``X-Hub-Signature-256`` over the RAW body with ``app_secret``.

        A channel with NO ``app_secret`` configured is NOT verified (returns True +
        warns once per call): the operator may not have pasted it yet, and refusing
        would silently break their inbox. With a secret configured, a missing or
        mismatching header is a hard reject — the core then drops the payload.
        """
        secret = self._app_secret
        if not secret:
            logger.warning("[%s] canal %s sem app_secret — webhook NÃO verificado "
                           "(configure o App Secret do app Meta)",
                           self.provider, self.channel_id)
            return True
        try:
            header = ""
            if headers is not None:
                header = (headers.get("X-Hub-Signature-256")
                          or headers.get("x-hub-signature-256") or "")
        except Exception:  # noqa: BLE001
            header = ""
        if not header.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode("utf-8"), raw_body or b"",
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, header.split("=", 1)[1].strip())

    # ── Outbound ─────────────────────────────────────────────────────
    def _messages_endpoint(self) -> str:
        """The node messages are POSTed to. ``/me/messages`` resolves to the Page /
        IG account owning the token, so no id is needed in the path."""
        return f"{self._graph_base()}/me/messages"

    def _post_message(self, payload: dict, *,
                      timeout: float = HTTP_TIMEOUT) -> SendResult:
        if not self._access_token:
            return SendResult(ok=False, error="missing_credentials")
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(self._messages_endpoint(),
                                   params=self._auth_params(), json=payload)
            if resp.status_code in (200, 201):
                data = resp.json() if resp.content else {}
                return SendResult(ok=True, external_msg_id=data.get("message_id", "") or "")
            return SendResult(ok=False, error=graph_error(resp))
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def _message_envelope(self, chat_id: str, message: dict) -> dict:
        """The common Send API body. ``messaging_type`` is REQUIRED by Meta."""
        return {
            "recipient": {"id": chat_id},
            "messaging_type": "RESPONSE",
            "message": message,
        }

    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        # Meta has no inline @mentions in DMs; ``reply_to`` maps to the reply_to
        # field (Messenger/IG accept ``{"reply_to": {"mid": …}}``).
        payload = self._message_envelope(chat_id, {"text": text})
        if reply_to:
            payload["message"]["reply_to"] = {"mid": reply_to}
        return self._post_message(payload)

    def _media_payload(self, chat_id: str, kind: str, path_or_url: str, *,
                       filename=None):
        """Send API body for an attachment — or a failed ``SendResult``.

        Shared by the base ``send_media`` and by subclasses that wrap the POST
        (Messenger's 24h-window fallback), so the attachment-type resolution lives
        in exactly one place.
        """
        url = public_media_url(path_or_url)
        if not url:
            return SendResult(
                ok=False,
                error="mídia precisa de URL pública: configure o endereço público "
                      "do painel (public_base_url) e garanta que o arquivo está em "
                      "statics/")
        return self._message_envelope(chat_id, {
            "attachment": {
                "type": attachment_type_for(kind, path_or_url, filename),
                "payload": {"url": url, "is_reusable": True},
            },
        })

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        """Send an attachment BY PUBLIC URL (D4).

        Meta fetches the file from the URL itself — there is no upload endpoint in
        this API, so a local path is converted with ``public_media_url``. Without a
        configured ``public_base_url`` (or a path outside ``statics/``) the send
        fails with an actionable message instead of shipping a broken link.
        A caption is not supported by the attachment payload, so it is delivered as
        a follow-up text message (what the Messenger UI does anyway).
        """
        payload = self._media_payload(chat_id, kind, path_or_url, filename=filename)
        if isinstance(payload, SendResult):
            return payload
        result = self._post_message(payload, timeout=MEDIA_TIMEOUT)
        if result.ok and caption:
            self.send_text(chat_id, caption)
        return result

    def react(self, chat_id: str, msg_id: str, emoji: str) -> None:
        """React / un-react to a message (``sender_action`` on the Send API)."""
        if not msg_id:
            return
        payload = {
            "recipient": {"id": chat_id},
            "sender_action": "react" if emoji else "unreact",
            # Remover a reação leva só o id da mensagem (sem emoji).
            "payload": ({"message_id": msg_id, "reaction": "other", "emoji": emoji}
                        if emoji else {"message_id": msg_id}),
        }
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                client.post(self._messages_endpoint(), params=self._auth_params(),
                            json=payload)
        except Exception:  # noqa: BLE001
            logger.debug("[%s] react failed", self.provider, exc_info=True)

    def mark_read(self, chat_id: str, msg_id: str) -> None:
        self._sender_action(chat_id, "mark_seen")

    def send_presence(self, chat_id: str, state: str) -> None:
        self._sender_action(chat_id,
                            "typing_on" if state == "composing" else "typing_off")

    def _sender_action(self, chat_id: str, action: str) -> None:
        if not chat_id or not self._access_token:
            return
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                client.post(self._messages_endpoint(), params=self._auth_params(),
                            json={"recipient": {"id": chat_id},
                                  "sender_action": action})
        except Exception:  # noqa: BLE001
            logger.debug("[%s] sender_action %s failed", self.provider, action,
                         exc_info=True)

    # ── Profile enrichment ───────────────────────────────────────────
    def resolve_sender_name(self, user_id: str) -> str:
        """Best-effort display name for a PSID/IGSID (``""`` when unavailable).

        ``GET /{id}?fields=…`` needs an app feature Meta grants by review
        ("Business Asset User Profile Access" for Messenger); without it the node
        answers ``{}`` and we fall back to the opaque id. Cached in memory so a
        chatty conversation costs one call.
        """
        if not user_id or not self.profile_fields or not self._access_token:
            return ""
        cached = self._profile_cache.get(user_id)
        if cached and (time.time() - cached[0]) < self.profile_cache_ttl:
            return cached[1]
        name = ""
        try:
            with httpx.Client(timeout=STATUS_TIMEOUT) as client:
                resp = client.get(
                    f"{self._graph_base()}/{user_id}",
                    params=self._auth_params({"fields": ",".join(self.profile_fields)}))
            if resp.status_code == 200:
                data = resp.json() or {}
                name = (data.get("name")
                        or " ".join(p for p in (data.get("first_name"),
                                                data.get("last_name")) if p)
                        or data.get("username") or "").strip()
            else:
                logger.debug("[%s] profile %s -> %s", self.provider, user_id,
                             resp.status_code)
        except Exception:  # noqa: BLE001
            logger.debug("[%s] profile fetch failed", self.provider, exc_info=True)
        self._profile_cache[user_id] = (time.time(), name)
        return name

    # ── Inbound media ────────────────────────────────────────────────
    def download_media(self, media_id: str, dest_dir, *,
                       mime_type: Optional[str] = None) -> Optional[str]:
        """Download an inbound attachment and cache it under ``dest_dir``.

        Simpler than the Cloud API's two-step: ``parse_inbound`` already carries the
        CDN url in ``media_extras["media_id"]``, so one GET is enough. Those URLs
        expire, which is exactly why the core downloads at ingest time instead of
        storing the link. Returns the written filename or ``None``.
        """
        url = (media_id or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return None
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(url)
            if resp.status_code != 200:
                logger.warning("[%s] download_media -> %s", self.provider,
                               resp.status_code)
                return None
            data = resp.content
            mime = (resp.headers.get("content-type") or mime_type or "").split(";")[0]
            ext = (mimetypes.guess_extension(mime.strip()) if mime else "") or ""
            if not ext:
                guessed = os.path.splitext(url.split("?")[0])[1]
                ext = guessed if 1 < len(guessed) <= 6 else ".bin"
            dest = Path(dest_dir)
            dest.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            filename = f"{self.provider}_{digest}{ext}"
            (dest / filename).write_bytes(data)
            return filename
        except Exception:  # noqa: BLE001
            logger.warning("[%s] download_media failed", self.provider, exc_info=True)
            return None

    # ── Inbound parsing ──────────────────────────────────────────────
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Walk ``entry[].messaging[]`` (Messenger/Instagram shape).

        Emits, per item: ``message`` (text and/or one event per attachment;
        ``is_echo`` flips it to ``direction="out"`` so the core records it as an
        outgoing echo instead of replying to itself), ``reaction``, ``receipt``
        (delivery/read) and postbacks (as a message carrying the button title).
        Unknown item kinds are skipped — never raise inside the webhook path.
        """
        events: list[InboundEvent] = []
        if not isinstance(raw, dict):
            return events
        for entry in raw.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            items = entry.get("messaging") or entry.get("standby") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    events.extend(self._parse_messaging_item(item, entry))
                except Exception:  # noqa: BLE001
                    logger.warning("[%s] falha ao parsear item de messaging",
                                   self.provider, exc_info=True)
        return events

    def _parse_messaging_item(self, item: dict, entry: dict) -> list[InboundEvent]:
        sender = str((item.get("sender") or {}).get("id") or "")
        recipient = str((item.get("recipient") or {}).get("id") or "")
        ts = _to_float(item.get("timestamp")) / 1000.0  # Meta sends milliseconds

        message = item.get("message") or {}
        is_echo = bool(message.get("is_echo"))
        # On an echo the roles are swapped: the Page/IG account is the sender and
        # the human is the recipient — the conversation key is always the HUMAN.
        chat_id = recipient if is_echo else sender
        direction = "out" if is_echo else "in"

        def _event(**kwargs) -> InboundEvent:
            base = {
                "channel_id": self.channel_id,
                "provider": self.provider,
                "chat_id": chat_id,
                "sender_id": sender,
                "ts": ts,
                "raw": item,
            }
            base.update(kwargs)
            return InboundEvent(**base)

        # ── reactions ────────────────────────────────────────────────
        reaction = item.get("reaction") or {}
        if reaction:
            emoji = reaction.get("emoji") or ""
            if (reaction.get("action") or "").lower() == "unreact":
                emoji = ""
            return [_event(kind="reaction", external_msg_id=reaction.get("mid", "") or "",
                           media_extras={"emoji": emoji,
                                         "reacted_message_id": reaction.get("mid", "") or "",
                                         "is_from_me": is_echo})]

        # ── receipts ─────────────────────────────────────────────────
        delivery = item.get("delivery") or {}
        if delivery:
            out = []
            for mid in delivery.get("mids") or []:
                out.append(_event(kind="receipt", direction="out",
                                  external_msg_id=mid,
                                  media_extras={"status": "delivered",
                                                "watermark": delivery.get("watermark")}))
            return out
        read = item.get("read") or {}
        if read:
            return [_event(kind="receipt", direction="out",
                           external_msg_id=read.get("mid", "") or "",
                           media_extras={"status": "read",
                                         "watermark": read.get("watermark")})]

        # ── postbacks (button taps) ──────────────────────────────────
        postback = item.get("postback") or {}
        if postback:
            title = postback.get("title") or postback.get("payload") or ""
            return [_event(kind="message", direction="in",
                           external_msg_id=postback.get("mid", "") or "",
                           sender_name=self.resolve_sender_name(chat_id),
                           text=title,
                           media_type="interactive",
                           media_extras={"payload": postback.get("payload"),
                                         "title": postback.get("title")})]

        if not message:
            return []

        # ── messages ─────────────────────────────────────────────────
        mid = message.get("mid", "") or ""
        text = message.get("text", "") or ""
        # Only enrich the name for INBOUND (an echo is us; the id is the Page).
        sender_name = "" if is_echo else self.resolve_sender_name(chat_id)
        quoted = (message.get("reply_to") or {}).get("mid") or None

        attachments = [a for a in (message.get("attachments") or [])
                       if isinstance(a, dict)]
        if not attachments:
            if not text:
                return []
            return [_event(kind="message", direction=direction, external_msg_id=mid,
                           sender_name=sender_name, text=text,
                           reply_to_msg_id=quoted)]

        out: list[InboundEvent] = []
        for index, att in enumerate(attachments):
            att_type = (att.get("type") or "").lower()
            payload = att.get("payload") or {}
            media_type = _ATTACHMENT_TO_MEDIA.get(att_type)
            if media_type == "location":
                coords = payload.get("coordinates") or {}
                lat, lng = coords.get("lat"), coords.get("long")
                out.append(_event(
                    kind="message", direction=direction,
                    external_msg_id=f"{mid}:{index}" if index else mid,
                    sender_name=sender_name,
                    text=text if index == 0 else "",
                    media_type="location",
                    media_path=f"geo:{lat},{lng}" if lat is not None else None,
                    media_extras={"lat": lat, "lng": lng,
                                  "title": payload.get("title")},
                    reply_to_msg_id=quoted))
                continue
            url = payload.get("url") or ""
            if not media_type or not url:
                # ``fallback`` (a shared link preview) and anything unknown: keep
                # the text/URL so nothing is silently lost.
                fallback_text = text or payload.get("title") or url
                if not fallback_text:
                    continue
                out.append(_event(kind="message", direction=direction,
                                  external_msg_id=f"{mid}:{index}" if index else mid,
                                  sender_name=sender_name, text=fallback_text,
                                  reply_to_msg_id=quoted))
                continue
            out.append(_event(
                kind="message", direction=direction,
                external_msg_id=f"{mid}:{index}" if index else mid,
                sender_name=sender_name,
                text=text if index == 0 else "",
                media_type=media_type,
                # The core's inbound resolver calls ``download_media(media_id, …)``;
                # for Meta the "id" IS the CDN url (it expires — never store it).
                media_extras={"media_id": url, "attachment_type": att_type,
                              "sticker_id": payload.get("sticker_id")},
                reply_to_msg_id=quoted))
        return out


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def graph_error(resp) -> str:
    """Human-readable message from a Graph API error response.

    Meta answers ``{"error": {"message", "error_user_msg", "code", "error_subcode"}}``;
    prefer the user-facing text and keep the codes (they drive the 24h-window
    fallback in the Messenger provider).
    """
    try:
        err = (resp.json() or {}).get("error") or {}
        msg = err.get("error_user_msg") or err.get("message")
        if msg:
            code = err.get("code")
            sub = err.get("error_subcode")
            suffix = f" (code {code}{'/' + str(sub) if sub else ''})" if code else ""
            return f"{msg}{suffix}"
    except Exception:  # noqa: BLE001
        pass
    return f"http_{resp.status_code}: {resp.text[:300]}"
