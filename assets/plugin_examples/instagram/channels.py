"""Instagram Direct channel provider (plano 46 · sub-plano 03).

DMs of an Instagram professional account reached **through a connected Facebook
Page** — the "Instagram messaging via Facebook Login" path (host
``graph.facebook.com``, a Page Access Token). This is exactly how Chatwoot's
``Channel::FacebookPage`` handles Instagram (page connected to an IG professional
account; dedup by ``page_id``; the connected IG account id kept only for display).
It replaces the older "Instagram API with Instagram Login" path
(``graph.instagram.com`` + a 60-day IG User token that had to be refreshed).

Almost everything is inherited from :class:`MetaGraphChannel` (the plugin's own
copy of the Meta base): the ``entry[].messaging[]`` parser, media by public URL,
``X-Hub-Signature-256`` verification, profile enrichment, the 24h window with a
``HUMAN_AGENT`` escape hatch. What lives here is only what is SPECIFIC to
Instagram-via-Facebook:

* credential/config shape — a Facebook Page (``page_id`` + ``page_access_token`` +
  ``app_secret`` + ``verify_token``; ``app_id`` optional, auto-detected from the
  page token), same as Messenger;
* the send path is Messenger's own (``graph.facebook.com/me/messages`` with the
  Page token, body ``{recipient, messaging_type:"RESPONSE", message}``) — Chatwoot
  reuses ``Facebook::SendOnFacebookService`` verbatim for legacy IG-via-FB, so
  ``_message_envelope`` is INHERITED unchanged (⚠️ unlike the old
  ``graph.instagram.com`` path, which had to drop ``messaging_type``). The 24h
  window fallback swaps in ``messaging_type=MESSAGE_TAG`` + ``tag=HUMAN_AGENT``;
* ``status()`` pings the Page node (``/{page_id}?fields=…instagram_business_account``)
  and shows the connected @username;
* account identity is the **``page_id``** (Chatwoot's ``Channel::FacebookPage``
  is unique on ``page_id`` — ``instagram_id`` is a secondary, non-unique attribute
  used only for inbound routing, which the core does by ``channel_id`` in the URL
  path anyway). Known at create time, like Messenger's ``page_id``.

Because the token is a Facebook **Page Access Token** (long-lived / System User in
production), it does NOT die after 60 days — there is NO token-refresh loop here
(that was only needed for the old ``graph.instagram.com`` IG User token).

Identity: an IGSID is app+account-scoped — the same human has a different IGSID on
another account — so the contact key is ``(channel_id, IGSID)``, exactly what the
core already uses (D9). The dedup key is the ``page_id``.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from channels.base import AccountIdentity, ChannelCapabilities, SendResult
from .meta_graph import (DEFAULT_GRAPH_VERSION, HTTP_TIMEOUT, MEDIA_TIMEOUT,
                         MetaGraphChannel, graph_error)

logger = logging.getLogger(__name__)

INSTAGRAM_GRAPH_HOST = "graph.facebook.com"

# Tag da conversa que o core aplica no handoff pra humano (CLAUDE.md "Gate de
# humano"). É o sinal que libera — e SÓ ele — a tag HUMAN_AGENT fora das 24h.
TRANSFER_TAG = "transferido_atendente"

# Erros da Meta que significam "fora da janela de mensagens" (mesma família do
# Messenger). 10 = permission/policy; 2018278 = subcódigo da janela de 24h.
_WINDOW_ERROR_MARKERS = ("outside of allowed window", "outside the allowed window",
                         "24-hour", "24 hour", "2018278")
_WINDOW_CODE_RE = re.compile(r"\bcode 10(?:/(\d+))?\b")

# Erro de token inválido/revogado (OAuth) — dispara o estado "reautorização".
# Diferente do fluxo antigo, um Page Access Token não EXPIRA por tempo; ele só
# fica inválido se for revogado (senha trocada, permissão removida, app em modo
# dev). Aí o operador reconecta a Página — não há refresh automático.
_AUTH_ERROR_MARKERS = ("code 190", "invalid oauth", "access token", "expired",
                       "session has expired", "reauthorize")

# Limites de anexo do Instagram (a POLÍTICA é do provider; o core só avalia —
# plano 65). Imagem ≤8 MB; áudio/vídeo/documento ≤25 MB. Import defensivo: core
# anterior ao plano 65 não tem ``MediaLimits`` e o plugin segue carregando.
try:
    from channels.base import MediaLimits, VideoLimits

    _IMAGE_CAP = 8 * 1024 * 1024
    _ATTACHMENT_CAP = 25 * 1024 * 1024
    _MEDIA_LIMITS = {
        "image": MediaLimits(max_bytes=_IMAGE_CAP),
        "audio": MediaLimits(max_bytes=_ATTACHMENT_CAP),
        "document": MediaLimits(max_bytes=_ATTACHMENT_CAP),
    }
    # Vídeo: a Meta busca o arquivo pela URL e entrega no player, que na prática
    # só reproduz MP4 H.264/AAC. Declarar os codecs opta pelo transcode do core.
    _MEDIA_LIMITS["video"] = VideoLimits(
        max_bytes=_ATTACHMENT_CAP,
        extensions=(".mp4",),
        video_codecs=("h264",),
        audio_codecs=("aac",),
        max_audio_streams=1,
        transcode=True,
    )
except ImportError:  # pragma: no cover - core antigo
    _MEDIA_LIMITS = None


class InstagramChannel(MetaGraphChannel):
    provider = "instagram"
    graph_host = INSTAGRAM_GRAPH_HOST
    token_credential_key = "page_access_token"   # the Facebook Page access token
    # ``GET /{IGSID}?fields=name,username`` enriches the sender's display name.
    profile_fields = ("name", "username")

    # ── Contact type (marca por canal — plano tipos-de-contato) ──────
    @classmethod
    def contact_type(cls) -> str:
        return "instagram"

    def __init__(self, channel_id: str, registry=None,
                 credentials: Optional[dict] = None):
        super().__init__(
            channel_id,
            ChannelCapabilities(
                qr=False,
                templates=False,       # Instagram has no HSM equivalent
                groups=False,
                presence=True,         # sender_action typing_on/off
                reactions=True,
                media=True,
                revoke=False,          # no delete-for-everyone on the Send API
                edit_message=False,
                inbound_route="path",
                # NO token_refresh: a Page Access Token does not expire by time.
                session_window_hours=24,
                # HUMAN_AGENT tag: 7 days, HUMANS ONLY (see _post_with_window_fallback).
                human_window_hours=24 * 7,
                required_credentials=("page_id", "page_access_token", "app_secret",
                                      "verify_token"),
                **({"media_limits": _MEDIA_LIMITS} if _MEDIA_LIMITS else {}),
            ),
            registry=registry,
            credentials=credentials,
        )

    # ── Provider descriptor (plano 33) ───────────────────────────────
    @classmethod
    def provider_descriptor(cls) -> dict:
        return {
            "provider": "instagram",
            "label": "Instagram",
            "color": "pink",
            "credential_fields": [
                {"key": "page_id", "label": "ID da Página do Facebook",
                 "type": "text", "required": True,
                 "placeholder": "ID numérico da Página",
                 "help": "Facebook → sua Página → Sobre → ID da Página. É a Página "
                         "conectada à conta profissional do Instagram que recebe "
                         "as DMs (Instagram via login do Facebook)."},
                {"key": "page_access_token", "label": "Page Access Token",
                 "type": "secret", "required": True, "placeholder": "EAAB...",
                 "help": "Token da Página (derivado de um token de usuário "
                         "long-lived, ou de um System User em produção). Não "
                         "expira por tempo — sem renovação automática."},
                {"key": "app_secret", "label": "App Secret", "type": "secret",
                 "required": True, "placeholder": "segredo do app na Meta",
                 "help": "Obrigatório: forma o app access token ({app_id}|{app_secret}) "
                         "que registra o Callback URL do webhook AUTOMATICAMENTE ao "
                         "salvar o canal, além de validar a assinatura "
                         "X-Hub-Signature-256 dos webhooks. App Dashboard → "
                         "Configurações → Básico → App Secret."},
                {"key": "verify_token", "label": "Verify Token",
                 "type": "token_suggest", "required": True,
                 "placeholder": "token de verificação do webhook",
                 "help": "Gerado aqui e registrado automaticamente na Meta ao criar "
                         "o canal. Só precisa ser colado à mão se o registro "
                         "automático falhar."},
                {"key": "app_id", "label": "App ID (opcional)", "type": "text",
                 "required": False, "placeholder": "detectado automaticamente",
                 "help": "Usado para registrar o Callback URL do webhook no app. "
                         "Deixe vazio: é detectado a partir do Page Access Token."},
            ],
            "config_fields": [
                {"key": "graph_api_version", "label": "Versão da Graph API",
                 "type": "text", "default": DEFAULT_GRAPH_VERSION,
                 "placeholder": DEFAULT_GRAPH_VERSION},
                {"key": "human_agent_tag", "type": "bool", "default": False,
                 "label": "Responder fora das 24h como agente humano",
                 "help": "Permite que um ATENDENTE humano responda até 7 dias "
                         "depois (tag HUMAN_AGENT da Meta). A IA nunca usa essa "
                         "tag — usá-la fora de atendimento humano viola a política "
                         "da Meta."},
            ],
            "capabilities": {"needs_qr": False, "templates": False},
            "ai_sequential_default": False,
            "contact_type": cls.contact_type(),
            # Registra o Callback URL no app da Meta (object=instagram) + assina a
            # Página via Graph API ao CRIAR o canal — sem passo manual (igual ao
            # Messenger). Cai num aviso "cole a URL à mão" só se a instância não
            # tiver HTTPS público.
            "post_create": {
                "kind": "autoconfigure",
                "endpoint": "/api/plugins/instagram/autoconfigure",
                "webhook_path": "/api/webhook/instagram/{channel_id}",
            },
            "form_component": None,
        }

    # ── Account identity (dedup — plano 32) ──────────────────────────
    @classmethod
    def identity_from_credentials(cls, creds: dict) -> Optional[AccountIdentity]:
        """A Instagram-via-Facebook account IS the Page (Chatwoot dedups by
        ``page_id``, not by the connected instagram_id): two channels on the same
        ``page_id`` are the same inbox connected twice → 409 before persisting.
        Known at create time, like Messenger's ``page_id``."""
        page_id = (creds.get("page_id") or "").strip()
        return AccountIdentity("page_id", page_id) if page_id else None

    @property
    def _page_id(self) -> str:
        return self._cred("page_id")

    # ── Status ───────────────────────────────────────────────────────
    def status(self) -> dict:
        page_id, token = self._page_id, self._access_token
        if not page_id or not token:
            return {"connected": False, "logged_in": False, "needs_qr": False,
                    "error": "missing_credentials"}
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.get(
                    f"{self._graph_base()}/{page_id}",
                    params=self._auth_params(
                        {"fields": "name,instagram_business_account{username,name}"}))
            if resp.status_code == 200:
                data = resp.json() or {}
                iba = data.get("instagram_business_account") or {}
                # Prefer the connected IG @username; fall back to the Page name.
                name = (("@" + iba["username"]) if iba.get("username")
                        else iba.get("name") or data.get("name"))
                return {"connected": True, "logged_in": True, "needs_qr": False,
                        "error": None, "verified_name": name, "own_phone": None}
            return {"connected": False, "logged_in": False, "needs_qr": False,
                    "error": graph_error(resp)}
        except Exception as e:  # noqa: BLE001
            return {"connected": False, "logged_in": False, "needs_qr": False,
                    "error": str(e)}

    # ── Outbound: 24h window + HUMAN_AGENT fallback (02.2) ───────────
    # NB: ``_message_envelope`` is inherited from MetaGraphChannel — the Messenger
    # body ``{recipient, messaging_type:"RESPONSE", message}``. Legacy IG-via-FB
    # goes through the Facebook Page send path, which requires ``messaging_type``
    # (Chatwoot's SendOnFacebookService sets it to RESPONSE).
    def _post_with_window_fallback(self, chat_id: str, payload: dict, *,
                                   timeout: float = HTTP_TIMEOUT) -> SendResult:
        """Send, and on a "outside the 24h window" refusal retry ONCE as
        ``MESSAGE_TAG`` + ``tag=HUMAN_AGENT`` — but only when the toggle is on AND
        the conversation is really with a human attendant (the AI never is).

        Also flags the channel for reauthorization on an OAuth error (190) so the
        card surfaces "reconectar a Página" instead of a silent failure."""
        result = self._post_message(payload, timeout=timeout)
        if result.ok:
            return result
        self._maybe_flag_reauth(result.error)
        if not _is_window_error(result.error):
            return result
        if not self._config_bool("human_agent_tag", False):
            return SendResult(ok=False, error=_window_message(result.error))
        if not self._conversation_with_human(chat_id):
            return SendResult(ok=False, error=_window_message(result.error))
        retry = dict(payload)
        retry["messaging_type"] = "MESSAGE_TAG"
        retry["tag"] = "HUMAN_AGENT"
        logger.info("[instagram] fora da janela de 24h — reenviando como "
                    "HUMAN_AGENT (atendimento humano) para %s", chat_id)
        return self._post_message(retry)

    def _conversation_with_human(self, chat_id: str) -> bool:
        """Whether this contact is currently in HUMAN hands (handoff), read from
        the core's own ``transferido_atendente`` tag. Fails CLOSED (no tag)."""
        try:
            from db.repositories import contact_repo, tag_repo

            contact = contact_repo.get_by_phone(chat_id)
            if not contact:
                return False
            tags = tag_repo.get_contact_tags(contact["id"]) or []
            names = {(t.get("name") if isinstance(t, dict) else str(t)) for t in tags}
            return TRANSFER_TAG in names
        except Exception:  # noqa: BLE001
            logger.debug("[instagram] handoff lookup failed", exc_info=True)
            return False

    def _maybe_flag_reauth(self, error: str) -> None:
        """On an OAuth/token error (190/revoked), mark the channel needing
        reauthorization (``last_error`` + ``logged_in=0``) so the card shows it.
        Non-destructive (keeps ``enabled=1``); best-effort. A Page token never
        expires by time, so this only fires when the token was actually revoked —
        the operator reconnects the Page (there is no automatic refresh)."""
        if not _is_auth_error(error) or self.registry is None:
            return
        try:
            self.registry.set_status(
                self.channel_id, logged_in=0,
                last_error="Token da Página inválido ou revogado — reautorize o "
                           "canal (cole um novo Page Access Token da Página do "
                           "Facebook conectada ao Instagram).")
        except Exception:  # noqa: BLE001
            logger.debug("[instagram] flag reauth failed", exc_info=True)

    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        payload = self._message_envelope(chat_id, {"text": text})
        if reply_to:
            payload["message"]["reply_to"] = {"mid": reply_to}
        return self._post_with_window_fallback(chat_id, payload)

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        payload = self._media_payload(chat_id, kind, path_or_url, filename=filename)
        if isinstance(payload, SendResult):
            return payload
        result = self._post_with_window_fallback(chat_id, payload,
                                                 timeout=MEDIA_TIMEOUT)
        if result.ok and caption:
            self.send_text(chat_id, caption)
        return result


def _is_window_error(error: str) -> bool:
    low = (error or "").lower()
    if any(marker in low for marker in _WINDOW_ERROR_MARKERS):
        return True
    return bool(_WINDOW_CODE_RE.search(low))


def _is_auth_error(error: str) -> bool:
    low = (error or "").lower()
    return any(marker in low for marker in _AUTH_ERROR_MARKERS)


def _window_message(original: str) -> str:
    return ("Fora da janela de 24h do Instagram: só um atendente humano pode "
            "responder (ative 'Responder fora das 24h como agente humano' na "
            f"configuração do canal e transfira o atendimento). [{original}]")


# Exported for the plugin loader (entry.channels → CHANNEL_PROVIDERS).
CHANNEL_PROVIDERS = [InstagramChannel]
