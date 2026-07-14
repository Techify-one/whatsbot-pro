"""Website widget channel provider (plano 46 · sub-plano 05).

The *inverse* channel: inbound is a browser POST (see ``routes.py`` — it builds the
raw dict and calls :meth:`WebsiteChannel.parse_inbound` → ``ctx.ingest_event``,
reusing the whole funnel); outbound ``send_text``/``send_media``/``send_presence``
DELIVER to the browser over the per-visitor WebSocket hub (``bridge.hub``) instead
of hitting an external API. Persistence + the operator broadcast already happen in
the core pipeline (``send_reply`` saves the assistant message with THIS method's
returned ``external_msg_id``); this method's only job is to push the same message —
same id — to the visitor's socket, so offline re-sync dedups cleanly.

``parse_inbound`` and the descriptor carry no DB/hub import, so this module loads
and unit-tests standalone (the hub is imported lazily inside the send methods).
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Optional

from channels.base import Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent

logger = logging.getLogger(__name__)

PROVIDER = "website"


class WebsiteChannel(Channel):
    provider = PROVIDER

    def __init__(self, channel_id: str, registry=None,
                 credentials: Optional[dict] = None):
        super().__init__(
            channel_id,
            ChannelCapabilities(
                qr=False,
                templates=False,
                groups=False,
                presence=True,        # agent-typing → visitor (inverse presence)
                reactions=False,
                media=True,
                inbound_route="path",  # browser POSTs a public route (routes.py)
                session_window_hours=0,
                # widget_token lives in config (auto-minted, public) and hmac_token
                # is optional — nothing the operator MUST type, so a channel is
                # never born "dead".
                required_credentials=(),
            ),
        )
        self.registry = registry
        self._credentials = dict(credentials or {})

    # ── Provider descriptor (plano 33) ───────────────────────────────
    @classmethod
    def provider_descriptor(cls) -> dict:
        return {
            "provider": PROVIDER,
            "label": "Site (Widget)",
            "color": "teal",
            "credential_fields": [
                {"key": "hmac_token", "label": "HMAC token (opcional)",
                 "type": "token_suggest", "required": False,
                 "help": "Segredo para validar a identidade de um usuário logado "
                         "(setUser). Só é necessário se você for identificar "
                         "visitantes pelo seu backend. Clique em Sugerir para gerar."},
            ],
            "config_fields": [
                {"key": "widget_token", "label": "Token do widget", "type": "generated",
                 "prefix": "wgt_",
                 "help": "Id público do widget — vai no snippet do seu site. Não é "
                         "segredo (só seleciona este canal)."},
                {"key": "allowed_domains", "label": "Domínios autorizados",
                 "type": "text", "default": "",
                 "placeholder": "http://localhost:5500, https://meusite.com",
                 "help": "Separados por vírgula. Só estes sites poderão embutir o "
                         "widget. Vazio = qualquer site (bom para testes/localhost)."},
                {"key": "welcome_title", "label": "Título do widget", "type": "text",
                 "default": "Fale conosco",
                 "placeholder": "Fale conosco"},
                {"key": "welcome_tagline", "label": "Subtítulo do widget", "type": "text",
                 "default": "Responderemos em instantes",
                 "placeholder": "Responderemos em instantes"},
                {"key": "widget_color", "label": "Cor do widget", "type": "text",
                 "default": "#0ea5a4",
                 "placeholder": "#0ea5a4",
                 "help": "Cor da bolha e do cabeçalho (hex)."},
                {"key": "hmac_mandatory", "label": "Exigir identidade (HMAC)",
                 "type": "bool", "default": False,
                 "help": "Se ligado, só aceita sessões com setUser verificado. "
                         "Requer o HMAC token acima."},
                {"key": "rate_limit_enabled", "label": "Proteção contra abuso (rate-limit)",
                 "type": "bool", "default": True,
                 "help": "Limita novas sessões/mensagens por IP. Desligue para "
                         "testes de carga a partir de uma única máquina."},
            ],
            "capabilities": {"needs_qr": False, "templates": False},
            "ai_sequential_default": False,
            # New post-create kind: render the <script> embed snippet with the
            # channel's widget_token + public base URL (frontend addition).
            "post_create": {"kind": "embed_snippet",
                            "widget_path": "/api/plugins/website/public/widget"},
            "form_component": None,
        }

    # ── Credential access (mirror of the telegram provider) ──────────
    def _cred(self, key: str) -> str:
        if self.registry is not None:
            try:
                val = self.registry.get_credential(self.channel_id, key)
            except Exception:  # noqa: BLE001
                val = None
            if val:
                return val
        return self._credentials.get(key, "") or ""

    # ── Status ───────────────────────────────────────────────────────
    def status(self) -> dict:
        """Always "connected": the widget has no external session to log into — it
        works as soon as the snippet is embedded. ``logged_in`` mirrors it so the
        card shows a green dot and the operator UI treats it as live."""
        return {"connected": True, "logged_in": True, "needs_qr": False, "error": None}

    # ── Outbound → deliver to the browser over the WS hub ────────────
    @staticmethod
    def _hub():
        """Lazily fetch the process singleton hub (kept out of module import so this
        file loads standalone for unit tests)."""
        try:
            from whatsbot_plugins.website.bridge import hub
            return hub
        except Exception:  # noqa: BLE001
            try:
                from bridge import hub  # standalone import fallback
                return hub
            except Exception:  # noqa: BLE001
                return None

    @staticmethod
    def _synthetic_id() -> str:
        return "web_" + secrets.token_hex(10)

    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        msg_id = self._synthetic_id()
        hub = self._hub()
        if hub is not None:
            hub.deliver(chat_id, {
                "type": "message", "role": "assistant", "content": text,
                "msg_id": msg_id, "ts": time.time(),
            })
        # Always ok=True: the message is persisted by the pipeline regardless of
        # whether a socket is currently open (offline queue → re-synced on reconnect).
        return SendResult(ok=True, external_msg_id=msg_id)

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        msg_id = self._synthetic_id()
        url = _to_browser_url(path_or_url)
        hub = self._hub()
        if hub is not None:
            hub.deliver(chat_id, {
                "type": "message", "role": "assistant", "content": caption or "",
                "media_type": kind, "media_url": url, "msg_id": msg_id,
                "ts": time.time(),
            })
        return SendResult(ok=True, external_msg_id=msg_id)

    def send_presence(self, chat_id: str, state: str) -> None:
        """Inverse presence: agent typing → visitor. ``composing`` shows the typing
        dots in the widget; anything else clears them."""
        hub = self._hub()
        if hub is None:
            return
        hub.deliver(chat_id, {
            "type": "typing", "state": "composing" if state == "composing" else "paused",
            "ts": time.time(),
        })

    # ── Inbound ──────────────────────────────────────────────────────
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Convert a browser message dict → a single inbound ``message`` event.

        ``routes.py`` assembles ``raw`` from the validated POST (``chat_id`` already
        resolved to the session's chat id) and calls this, then feeds the event to
        ``ctx.ingest_event`` — the same funnel every provider uses."""
        if not isinstance(raw, dict):
            return []
        chat_id = str(raw.get("chat_id") or raw.get("session_token") or "").strip()
        text = (raw.get("text") or "").strip()
        media_type = raw.get("media_type")
        if not chat_id or (not text and not media_type):
            return []
        return [InboundEvent(
            channel_id=self.channel_id,
            provider=self.provider,
            kind="message",
            direction="in",
            external_msg_id=str(raw.get("msg_id") or ""),
            chat_id=chat_id,
            sender_id=chat_id,
            sender_name=(raw.get("name") or "").strip(),
            is_group=False,
            text=text,
            media_type=media_type,
            media_path=raw.get("media_path"),
            media_extras=raw.get("media_extras") or {},
            ts=_to_float(raw.get("ts")) or time.time(),
            raw=raw,
        )]


def _to_browser_url(path_or_url: str) -> str:
    """A URL the iframe (same-origin as WhatsBot) can load. An http(s) URL passes
    through; any local path is mapped to a root-relative ``/statics/…`` URL.

    Operator media reaches ``send_media`` as an ABSOLUTE filesystem path (e.g.
    ``/app/statics/outbox/x.png`` — ``settings.data_dir`` is absolute), while the
    static mount is ``/statics``. Returning that absolute path verbatim would 404 in
    the browser, so we rebuild the URL from the ``statics/`` segment. A relative
    ``statics/media/x.jpg`` maps the same way; a path without ``statics/`` is served
    root-relative as-is."""
    p = str(path_or_url or "").strip()
    if not p or p.startswith(("http://", "https://")):
        return p
    norm = p.replace("\\", "/")
    idx = norm.rfind("statics/")
    if idx >= 0:
        return "/" + norm[idx:]
    return norm if norm.startswith("/") else "/" + norm.lstrip("/")


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# Exported for the plugin loader (entry.channels → CHANNEL_PROVIDERS).
CHANNEL_PROVIDERS = [WebsiteChannel]
