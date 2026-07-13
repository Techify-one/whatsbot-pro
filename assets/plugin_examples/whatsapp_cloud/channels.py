"""WhatsApp Cloud API channel provider (Plano 02 Fase 2).

Implements the ``Channel`` contract for the official WhatsApp Cloud API (Meta
Graph API). Unlike GOWA there is no QR / linked-device session: the channel
authenticates with a permanent ``access_token`` against a ``phone_number_id``,
so ``capabilities.qr = False`` and ``inbound_route = "path"`` (Meta delivers
inbound messages to the core webhook the user registers in the Meta dashboard).

Credentials model (P24 — provider never touches the channels tables by SQL):
  * When a ``registry`` is given, secrets are read via
    ``registry.get_credential(channel_id, key)`` — the same API the core uses.
  * When ``registry is None`` (unit tests), an in-memory ``credentials`` dict
    passed to ``__init__`` is used instead.

Keys read: ``access_token``, ``phone_number_id`` and the optional
``graph_api_version`` (default ``v21.0`` — also exposed as a plugin setting).

HTTP uses ``httpx`` to match the rest of the core (gowa/client.py, balance
monitor, etc.) — no extra dependency.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx

from channels.base import AccountIdentity, Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com"
DEFAULT_GRAPH_VERSION = "v21.0"
HTTP_TIMEOUT = 20.0


class WhatsAppCloudChannel(Channel):
    provider = "whatsapp_cloud"

    # ── Contact type (marca por canal — plano tipos-de-contato) ──────
    @classmethod
    def contact_type(cls) -> str:
        """WhatsApp Cloud é WhatsApp — mesmo tipo que o GOWA."""
        return "whatsapp"

    def __init__(self, channel_id: str, registry=None, credentials: Optional[dict] = None):
        super().__init__(
            channel_id,
            ChannelCapabilities(
                qr=False,
                templates=True,
                groups=False,         # Cloud API is 1:1 only (no group messaging)
                presence=False,
                reactions=True,
                media=True,
                inbound_route="path",
                session_window_hours=24,  # free text only within 24h of last inbound
                # No QR / connect step: a channel missing these can never connect,
                # send or receive (status() pings phone_number_id+access_token; the
                # core webhook handshake needs verify_token). The core rejects
                # creating one without them — see channels.base.required_credentials.
                required_credentials=("access_token", "phone_number_id", "verify_token"),
            ),
        )
        self.registry = registry
        # In-memory fallback for tests / registry-less usage.
        self._credentials = dict(credentials or {})
        # Short cache for list_templates (avoid hitting the Graph API on every
        # picker open): (fetched_at, [templates]).
        self._templates_cache: Optional[tuple] = None

    # ── Provider descriptor (plano 33) ───────────────────────────────
    @classmethod
    def provider_descriptor(cls) -> dict:
        """WhatsApp Cloud API (Meta Graph): credential-only (no QR), supports
        templates (HSM). After create the core shows the inbound webhook callback
        URL to paste into the Meta app config (``post_create`` = ``webhook_url``).
        ``verify_token`` offers a "suggest random token" button."""
        return {
            "provider": "whatsapp_cloud",
            "label": "WhatsApp Cloud",
            "color": "blue",
            "credential_fields": [
                {"key": "access_token", "label": "Access Token", "type": "secret",
                 "required": True, "placeholder": "EAAB..."},
                {"key": "phone_number_id", "label": "Phone Number ID",
                 "type": "text", "required": True,
                 "placeholder": "ID do número (Meta)"},
                {"key": "waba_id", "label": "WABA ID", "type": "text",
                 "required": False,
                 "placeholder": "WhatsApp Business Account ID",
                 "help": "Necessário para listar e criar templates (HSM). Em "
                         "WhatsApp Manager → Configurações da conta. Diferente do "
                         "Phone Number ID."},
                {"key": "verify_token", "label": "Verify Token",
                 "type": "token_suggest", "required": True,
                 "placeholder": "token de verificação do webhook"},
            ],
            "config_fields": [],
            "capabilities": {"needs_qr": False, "templates": True},
            "ai_sequential_default": False,
            "post_create": {
                "kind": "webhook_url",
                "path": "/api/webhook/whatsapp_cloud/{channel_id}",
                "title": "Canal criado",
                "help": "Cole esta URL como Callback URL na configuração de "
                        "webhook do seu app na Meta:",
            },
            "form_component": None,
        }

    # ── Account identity (dedup contract — plano 32 F5) ──────────────
    @classmethod
    def identity_from_credentials(cls, creds: dict) -> Optional[AccountIdentity]:
        """The Cloud account is the ``phone_number_id`` — known at create time.

        Two channels with the same ``phone_number_id`` are the same WhatsApp
        Business number connected twice, so the core rejects the duplicate before
        it is persisted (no network needed). ``None`` when the field is blank.
        """
        pid = (creds.get("phone_number_id") or "").strip()
        return AccountIdentity("phone_number_id", pid) if pid else None

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
    def _graph_version(self) -> str:
        return (
            self._cred("graph_api_version")
            or os.environ.get("WHATSAPP_CLOUD_GRAPH_VERSION")
            or DEFAULT_GRAPH_VERSION
        )

    @property
    def _phone_number_id(self) -> str:
        return self._cred("phone_number_id")

    @property
    def _access_token(self) -> str:
        return self._cred("access_token")

    def _base_url(self) -> str:
        return f"{GRAPH_BASE}/{self._graph_version}"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    # ── Lifecycle ────────────────────────────────────────────────────
    # Cloud API is stateless / push-based; nothing to start or stop. The
    # base class no-op start()/stop() are inherited.

    def status(self) -> dict:
        """Ping the phone-number node to confirm token + id are valid."""
        phone_id = self._phone_number_id
        if not phone_id or not self._access_token:
            return {
                "connected": False,
                "logged_in": False,
                "needs_qr": False,
                "error": "missing_credentials",
            }
        url = f"{self._base_url()}/{phone_id}"
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.get(url, headers=self._headers())
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "connected": True,
                    "logged_in": True,
                    "needs_qr": False,
                    "error": None,
                    "verified_name": data.get("verified_name"),
                    "display_phone_number": data.get("display_phone_number"),
                    "quality_rating": data.get("quality_rating"),
                }
            return {
                "connected": False,
                "logged_in": False,
                "needs_qr": False,
                "error": f"http_{resp.status_code}: {resp.text[:200]}",
            }
        except Exception as e:  # noqa: BLE001
            return {
                "connected": False,
                "logged_in": False,
                "needs_qr": False,
                "error": str(e),
            }

    # ── Outbound ─────────────────────────────────────────────────────
    def _post_message(self, payload: dict) -> SendResult:
        phone_id = self._phone_number_id
        if not phone_id or not self._access_token:
            return SendResult(ok=False, error="missing_credentials")
        url = f"{self._base_url()}/{phone_id}/messages"
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.post(url, headers=self._headers(), json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                msgs = data.get("messages") or []
                ext_id = msgs[0].get("id", "") if msgs else ""
                return SendResult(ok=True, external_msg_id=ext_id)
            return SendResult(ok=False, error=f"http_{resp.status_code}: {resp.text[:300]}")
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        # Cloud API has no inline @mentions and replies use "context".
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": chat_id,
            "type": "text",
            "text": {"body": text, "preview_url": True},
        }
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        return self._post_message(payload)

    def upload_media(self, path: str, *, mime_type: Optional[str] = None) -> Optional[str]:
        """Upload a LOCAL file to the Graph API and return its media id.

        ``POST /{phone_number_id}/media`` (multipart) with ``messaging_product=whatsapp``
        and the file's mime ``type``. The returned id is consumed immediately by
        :meth:`send_media`; Cloud media ids are single-use-ish and expire, so we do
        NOT cache them. The header carries ONLY ``Authorization`` — httpx sets the
        multipart ``Content-Type`` (with boundary) itself; forcing
        ``application/json`` (as :meth:`_headers` does) would corrupt the upload.
        Returns ``None`` on any failure (caller maps it to a clean SendResult error).
        """
        phone_id = self._phone_number_id
        if not phone_id or not self._access_token or not path:
            return None
        mime = mime_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        url = f"{self._base_url()}/{phone_id}/media"
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client, open(path, "rb") as fh:
                resp = client.post(
                    url,
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    data={"messaging_product": "whatsapp", "type": mime},
                    files={"file": (Path(path).name, fh, mime)},
                )
            if resp.status_code in (200, 201):
                return (resp.json() or {}).get("id")
            logger.warning("whatsapp_cloud upload_media -> %s: %s",
                           resp.status_code, resp.text[:300])
        except Exception:  # noqa: BLE001
            logger.warning("whatsapp_cloud upload_media failed", exc_info=True)
        return None

    @staticmethod
    def _is_local_file(path_or_url: str) -> bool:
        """True when the input is a local filesystem path (needs upload), not a
        public URL (sent as ``link``)."""
        if not path_or_url:
            return False
        low = path_or_url.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return False
        try:
            return os.path.isfile(path_or_url)
        except OSError:
            return False

    @staticmethod
    def _gif_to_mp4(path: str) -> Optional[str]:
        """Best-effort convert an animated GIF to a looping, silent MP4 so it plays
        inline on WhatsApp (the Cloud API has no native GIF type — a raw GIF can't
        be sent as ``image``/``video``). Requires ffmpeg on PATH; returns the temp
        mp4 path, or ``None`` when ffmpeg is absent/fails (caller then falls back to
        sending the GIF as a document)."""
        if not shutil.which("ffmpeg"):
            return None
        try:
            out = os.path.join(
                tempfile.gettempdir(),
                f"wac_gif_{Path(path).stem}_{int(time.time() * 1000)}.mp4")
            # yuv420p + even dimensions + faststart = broad WhatsApp compatibility;
            # -an drops audio (GIFs have none, and silent video autoplays like a GIF).
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", path,
                 "-movflags", "faststart", "-pix_fmt", "yuv420p",
                 "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-an", out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if proc.returncode == 0 and os.path.isfile(out):
                return out
        except Exception:  # noqa: BLE001
            logger.warning("whatsapp_cloud gif->mp4 conversion failed", exc_info=True)
        return None

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        """Send media via the Cloud API.

        Accepts EITHER a public URL (sent as ``link``) OR a LOCAL file path — the
        core hands the provider a local ``statics/...`` path (the same contract GOWA
        gets), so a local file is uploaded to ``/media`` first and sent by its
        ``id``. Sending a local path as ``link`` is what produced the
        ``(#100) ... is not a valid URI`` error.

        GIF (decision: animate as video): a local ``.gif`` is converted to MP4 and
        sent as ``video`` when ffmpeg is available; otherwise it degrades to a
        document (delivered, not animated).
        """
        kind = (kind or "").lower()
        is_local = self._is_local_file(path_or_url)

        # GIF → video (animated) when we can convert; else send as a document.
        # Only auto-animate on the IMAGE path: if the operator explicitly attached
        # the .gif as a DOCUMENT, respect that and send the file as-is.
        is_gif = kind in ("image", "gif") and (
            kind == "gif"
            or (path_or_url or "").lower().endswith(".gif")
            or (mimetypes.guess_type(path_or_url)[0] or "") == "image/gif"
        )
        upload_path = path_or_url
        cleanup_path: Optional[str] = None
        if is_gif:
            mp4 = self._gif_to_mp4(path_or_url) if is_local else None
            if mp4:
                kind, upload_path, cleanup_path = "video", mp4, mp4
            else:
                kind = "document"  # cannot animate without conversion → deliver as file

        # Resolve a local path to a media id (upload); keep public URLs as a link.
        if is_local:
            media_id = self.upload_media(upload_path)
            if cleanup_path:
                try:
                    os.remove(cleanup_path)
                except OSError:
                    pass
            if not media_id:
                return SendResult(ok=False, error="media_upload_failed")
            media_ref = {"id": media_id}
        else:
            media_ref = {"link": path_or_url}

        if kind == "image":
            mtype = "image"
            media_obj: dict = dict(media_ref)
            if caption:
                media_obj["caption"] = caption
        elif kind in ("audio", "voice"):
            mtype = "audio"
            media_obj = dict(media_ref)
        elif kind == "video":
            mtype = "video"
            media_obj = dict(media_ref)
            if caption:
                media_obj["caption"] = caption
        elif kind == "sticker":
            mtype = "sticker"
            media_obj = dict(media_ref)
        else:
            mtype = "document"
            media_obj = dict(media_ref)
            if caption:
                media_obj["caption"] = caption
            # Documents keep a human filename; fall back to the local file's name.
            doc_name = filename or (os.path.basename(path_or_url) if is_local else None)
            if doc_name:
                media_obj["filename"] = doc_name
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": chat_id,
            "type": mtype,
            mtype: media_obj,
        }
        return self._post_message(payload)

    def react(self, chat_id: str, msg_id: str, emoji: str) -> None:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": chat_id,
            "type": "reaction",
            "reaction": {"message_id": msg_id, "emoji": emoji or ""},
        }
        self._post_message(payload)

    def mark_read(self, chat_id: str, msg_id: str) -> None:
        # Cloud API marks-as-read by message id (chat_id unused but kept for
        # contract parity).
        phone_id = self._phone_number_id
        if not phone_id or not self._access_token or not msg_id:
            return
        url = f"{self._base_url()}/{phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": msg_id,
        }
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                client.post(url, headers=self._headers(), json=payload)
        except Exception:  # noqa: BLE001
            logger.debug("whatsapp_cloud mark_read failed", exc_info=True)

    def send_template(self, chat_id: str, template_name: str, lang: str = "pt_BR",
                      components=None) -> SendResult:
        """Send an approved template (HSM) — required outside the 24h window."""
        template: dict = {
            "name": template_name,
            "language": {"code": lang},
        }
        if components:
            template["components"] = components
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": chat_id,
            "type": "template",
            "template": template,
        }
        return self._post_message(payload)

    # ── Templates (HSM) listing ──────────────────────────────────────
    def list_templates(self) -> list[dict]:
        """List message templates for the WABA (Graph API), ANY status.

        ``GET /{waba_id}/message_templates`` — note this uses ``waba_id`` (NOT
        ``phone_number_id``, which send_template uses). Returns templates of every
        status (APPROVED/PENDING/REJECTED/…) normalized for the UI — the picker
        shows the status badge and only lets approved ones be sent. Follows
        ``paging.next``. Cached for ~5 min so reopening the picker doesn't re-hit
        Meta (invalidated by create/delete). Returns ``[]`` on any failure or
        missing ``waba_id`` (the UI then shows a "configure o WABA ID" hint).
        """
        waba_id = self._cred("waba_id")
        token = self._access_token
        if not waba_id or not token:
            return []
        if self._templates_cache is not None:
            fetched_at, cached = self._templates_cache
            if (time.time() - fetched_at) < 300:
                return cached

        results: list[dict] = []
        url = f"{self._base_url()}/{waba_id}/message_templates"
        params: Optional[dict] = {
            "fields": "name,language,status,category,components",
            "limit": 100,
        }
        headers = {"Authorization": f"Bearer {token}"}
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                # Cap pagination defensively (a WABA shouldn't have thousands).
                for _ in range(20):
                    resp = client.get(url, headers=headers, params=params)
                    if resp.status_code != 200:
                        logger.warning("whatsapp_cloud list_templates %s -> %s: %s",
                                       waba_id, resp.status_code, resp.text[:200])
                        break
                    data = resp.json()
                    for tpl in data.get("data") or []:
                        results.append(self._normalize_template(tpl))
                    nxt = ((data.get("paging") or {}).get("next")) or ""
                    if not nxt:
                        break
                    url, params = nxt, None  # next is a full URL; don't re-send params
        except Exception:  # noqa: BLE001
            logger.warning("whatsapp_cloud list_templates failed", exc_info=True)
            # Serve a stale cache if we have one rather than nothing.
            return self._templates_cache[1] if self._templates_cache else []

        self._templates_cache = (time.time(), results)
        return results

    def _invalidate_templates_cache(self) -> None:
        self._templates_cache = None

    def create_template(self, name: str, *, category: str, language: str,
                        body_text: str, header_text: Optional[str] = None,
                        footer_text: Optional[str] = None,
                        body_examples: Optional[list] = None,
                        header_examples: Optional[list] = None) -> dict:
        """Create a template via ``POST /{waba_id}/message_templates`` (Graph API).

        Assembles the Graph ``components`` from a simple normalized definition so the
        Graph-specific shape (UPPERCASE types, the nested ``example`` arrays Meta
        requires for ``{{n}}`` placeholders) stays inside the provider. The created
        template starts ``PENDING`` until Meta reviews it. Invalidates the list
        cache on success. Returns ``{ok, id?, status?, category?, error?}``.
        """
        waba_id = self._cred("waba_id")
        token = self._access_token
        if not waba_id or not token:
            return {"ok": False, "error": "missing_credentials"}
        if not (name or "").strip() or not (body_text or "").strip():
            return {"ok": False, "error": "name e body_text são obrigatórios"}

        components: list[dict] = []
        if header_text and header_text.strip():
            header_comp: dict = {"type": "HEADER", "format": "TEXT",
                                 "text": header_text.strip()}
            if header_examples:
                header_comp["example"] = {"header_text": list(header_examples)}
            components.append(header_comp)
        body_comp: dict = {"type": "BODY", "text": body_text.strip()}
        if body_examples:
            # Meta expects body_text as a list-of-lists (one inner list per example set).
            body_comp["example"] = {"body_text": [list(body_examples)]}
        components.append(body_comp)
        if footer_text and footer_text.strip():
            components.append({"type": "FOOTER", "text": footer_text.strip()})

        payload = {
            "name": name.strip(),
            "language": (language or "pt_BR").strip(),
            "category": (category or "UTILITY").strip().upper(),
            "components": components,
        }
        url = f"{self._base_url()}/{waba_id}/message_templates"
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.post(url, headers=self._headers(), json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                self._invalidate_templates_cache()
                return {
                    "ok": True,
                    "id": data.get("id"),
                    "status": data.get("status"),
                    "category": data.get("category"),
                }
            return {"ok": False, "error": _graph_error(resp)}
        except Exception as e:  # noqa: BLE001
            logger.warning("whatsapp_cloud create_template failed", exc_info=True)
            return {"ok": False, "error": str(e)}

    def delete_template(self, name: str) -> dict:
        """Delete a template (every language version) by name.

        ``DELETE /{waba_id}/message_templates?name={name}`` removes ALL languages of
        that template name. Invalidates the list cache on success. Returns
        ``{ok, error?}``.
        """
        waba_id = self._cred("waba_id")
        token = self._access_token
        if not waba_id or not token:
            return {"ok": False, "error": "missing_credentials"}
        if not (name or "").strip():
            return {"ok": False, "error": "name é obrigatório"}
        url = f"{self._base_url()}/{waba_id}/message_templates"
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.delete(url, headers={"Authorization": f"Bearer {token}"},
                                     params={"name": name.strip()})
            if resp.status_code in (200, 201):
                data = resp.json() if resp.content else {}
                if data.get("success") is False:
                    return {"ok": False, "error": _graph_error(resp)}
                self._invalidate_templates_cache()
                return {"ok": True}
            return {"ok": False, "error": _graph_error(resp)}
        except Exception as e:  # noqa: BLE001
            logger.warning("whatsapp_cloud delete_template failed", exc_info=True)
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _normalize_template(tpl: dict) -> dict:
        """Normalize a Graph template into the UI shape (lower-cased type/format)."""
        components = []
        for c in tpl.get("components") or []:
            nc: dict = {"type": (c.get("type") or "").lower()}
            if c.get("format"):
                nc["format"] = (c.get("format") or "").lower()
            if c.get("text") is not None:
                nc["text"] = c.get("text")
            if c.get("example") is not None:
                nc["example"] = c.get("example")
            if c.get("buttons") is not None:
                nc["buttons"] = c.get("buttons")
            components.append(nc)
        return {
            "name": tpl.get("name"),
            "language": tpl.get("language"),
            "category": tpl.get("category"),
            "status": tpl.get("status"),
            "components": components,
        }

    # ── Inbound media download (plano 11 Fase 5 / P16) ───────────────
    def download_media(self, media_id: str, dest_dir, *,
                       mime_type: Optional[str] = None) -> Optional[str]:
        """Resolve a Cloud media id → a cached local file in ``dest_dir``.

        Two Graph calls: ``GET /{media_id}`` returns a short-lived ``url`` +
        ``mime_type``; the bytes are then fetched from that url WITH the bearer
        token. Writes ``cloud_<media_id>.<ext>`` into ``dest_dir`` and returns the
        filename (the core composes the ``statics/media/<filename>`` media_path),
        or ``None`` on any failure (caller falls back to a media placeholder).
        """
        if not media_id or not self._access_token:
            return None
        try:
            meta_url = f"{self._base_url()}/{media_id}"
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                meta = client.get(meta_url, headers=self._headers())
                if meta.status_code != 200:
                    logger.warning("whatsapp_cloud media meta %s -> %s",
                                   media_id, meta.status_code)
                    return None
                info = meta.json()
                url = info.get("url")
                mime = info.get("mime_type") or mime_type or ""
                if not url:
                    return None
                # The CDN url requires the bearer token too (no Content-Type).
                blob = client.get(url, headers={"Authorization": f"Bearer {self._access_token}"})
                if blob.status_code != 200:
                    logger.warning("whatsapp_cloud media blob %s -> %s",
                                   media_id, blob.status_code)
                    return None
                data = blob.content
            ext = mimetypes.guess_extension((mime or "").split(";")[0].strip() or "") or ""
            # Normalise the two WhatsApp voice/ogg variants stdlib gets wrong.
            if not ext and "ogg" in mime:
                ext = ".ogg"
            if not ext:
                ext = ".bin"
            dest = Path(dest_dir)
            dest.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(c for c in str(media_id) if c.isalnum() or c in "-_")[:64]
            filename = f"cloud_{safe_id}{ext}"
            (dest / filename).write_bytes(data)
            return filename
        except Exception:  # noqa: BLE001
            logger.warning("whatsapp_cloud download_media failed for %s", media_id,
                           exc_info=True)
            return None

    # ── Inbound ──────────────────────────────────────────────────────
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Translate a Meta webhook payload into ``InboundEvent`` objects.

        Walks ``entry[].changes[].value`` and emits:
          * one ``kind="message"`` event per ``messages[]`` item
          * one ``kind="receipt"`` event per ``statuses[]`` item

        Media (image/audio/document/video/sticker) is recorded with its Cloud
        API ``media id`` in ``media_extras``; the core's inbound resolver then
        downloads + caches it to ``statics/media/`` via :meth:`download_media`.
        """
        events: list[InboundEvent] = []
        if not isinstance(raw, dict):
            return events

        entries = raw.get("entry") or []
        for entry in entries:
            changes = (entry or {}).get("changes") or []
            for change in changes:
                value = (change or {}).get("value") or {}
                metadata = value.get("metadata") or {}
                phone_number_id = metadata.get("phone_number_id", "")

                # Map waba phone-number-id → our channel id when it matches; we
                # keep this channel's own id otherwise (the core routes by the
                # webhook path, so channel_id is already known).
                channel_id = self.channel_id

                # contacts[].profile.name keyed by wa_id for sender_name lookup.
                name_by_wa: dict[str, str] = {}
                for contact in value.get("contacts") or []:
                    wa_id = contact.get("wa_id", "")
                    profile = contact.get("profile") or {}
                    if wa_id and profile.get("name"):
                        name_by_wa[wa_id] = profile["name"]

                # ── messages ──────────────────────────────────────────
                for msg in value.get("messages") or []:
                    events.append(
                        self._parse_message(
                            msg, channel_id, phone_number_id, name_by_wa, change
                        )
                    )

                # ── statuses (receipts) ───────────────────────────────
                for status in value.get("statuses") or []:
                    events.append(
                        InboundEvent(
                            channel_id=channel_id,
                            provider=self.provider,
                            kind="receipt",
                            direction="out",
                            external_msg_id=status.get("id", ""),
                            chat_id=status.get("recipient_id", ""),
                            sender_id=status.get("recipient_id", ""),
                            ts=_to_float(status.get("timestamp")),
                            media_extras={
                                "status": status.get("status"),
                                "conversation": status.get("conversation"),
                                "pricing": status.get("pricing"),
                                "errors": status.get("errors"),
                            },
                            raw=status,
                        )
                    )

        return events

    def _parse_message(self, msg: dict, channel_id: str, phone_number_id: str,
                       name_by_wa: dict, change: dict) -> InboundEvent:
        sender = msg.get("from", "")
        msg_type = msg.get("type", "")
        external_id = msg.get("id", "")
        ts = _to_float(msg.get("timestamp"))
        sender_name = name_by_wa.get(sender, "")

        text = ""
        media_type: Optional[str] = None
        media_extras: dict = {}

        if msg_type == "text":
            text = (msg.get("text") or {}).get("body", "")
        elif msg_type in ("image", "audio", "video", "document", "sticker", "voice"):
            media_type = "audio" if msg_type == "voice" else msg_type
            obj = msg.get(msg_type) or {}
            # Record the Cloud API media id; the core's inbound media resolver
            # (server/routes/webhook.py _resolve_inbound_media) calls
            # :meth:`download_media` to fetch + cache it into statics/media/ and
            # fills media_path. We only carry the id/metadata here.
            media_extras = {
                "media_id": obj.get("id"),
                "mime_type": obj.get("mime_type"),
                "sha256": obj.get("sha256"),
                "filename": obj.get("filename"),
                "voice": obj.get("voice"),
            }
            caption = obj.get("caption")
            if caption:
                text = caption
                media_extras["caption"] = caption
        elif msg_type == "location":
            loc = msg.get("location") or {}
            media_type = "location"
            media_extras = {
                "lat": loc.get("latitude"),
                "lng": loc.get("longitude"),
                "name": loc.get("name"),
                "address": loc.get("address"),
            }
        elif msg_type == "reaction":
            reaction = msg.get("reaction") or {}
            return InboundEvent(
                channel_id=channel_id,
                provider=self.provider,
                kind="reaction",
                external_msg_id=external_id,
                chat_id=sender,
                sender_id=sender,
                sender_name=sender_name,
                ts=ts,
                media_extras={
                    "emoji": reaction.get("emoji"),
                    "reacted_message_id": reaction.get("message_id"),
                },
                raw=msg,
            )
        elif msg_type in ("button", "interactive"):
            inter = msg.get(msg_type) or {}
            media_type = "interactive"
            media_extras = {"payload": inter}
            text = inter.get("text") or ""
        else:
            # Unknown/unsupported type — keep metadata, leave text empty.
            media_type = msg_type or None
            media_extras = {"unsupported_type": msg_type, "payload": msg.get(msg_type)}

        return InboundEvent(
            channel_id=channel_id,
            provider=self.provider,
            kind="message",
            direction="in",
            external_msg_id=external_id,
            chat_id=sender,
            sender_id=sender,
            sender_name=sender_name,
            is_group=False,            # Cloud API is 1:1 only
            text=text,
            media_type=media_type,
            media_path=None,           # filled by the core inbound media resolver
            media_extras=media_extras,
            ts=ts,
            raw=msg,
        )


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _graph_error(resp) -> str:
    """Best-effort human-readable error from a Graph API error response.

    Meta returns ``{"error": {"message": ..., "error_user_msg": ...}}``; prefer the
    user-facing message when present, falling back to the raw body."""
    try:
        err = (resp.json() or {}).get("error") or {}
        msg = err.get("error_user_msg") or err.get("message")
        if msg:
            return f"{msg}" + (f" ({err['error_user_title']})" if err.get("error_user_title") else "")
    except Exception:  # noqa: BLE001
        pass
    return f"http_{resp.status_code}: {resp.text[:300]}"


# Exported for the plugin loader (entry.channels → CHANNEL_PROVIDERS).
CHANNEL_PROVIDERS = [WhatsAppCloudChannel]
