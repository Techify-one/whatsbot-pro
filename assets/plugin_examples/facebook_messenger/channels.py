"""Facebook Messenger channel provider (plano 46 · sub-plano 02).

A Page inbox on the Messenger Platform (Meta Graph API, webhook push). Almost
everything is inherited from :class:`channels.providers.meta_graph.MetaGraphChannel`
(01-B): the ``entry[].messaging[]`` parser, the Send API envelope, media by public
URL, ``X-Hub-Signature-256`` verification, profile enrichment. What lives here is
only what is SPECIFIC to Messenger:

* credential/config shape (``page_id`` + ``page_access_token`` + ``app_secret`` +
  ``verify_token``; ``graph_api_version`` and ``human_agent_tag`` as config);
* ``status()`` — pings the Page node;
* the 24h window fallback: outside it Meta refuses a plain ``RESPONSE``; a real
  HUMAN reply may go out as ``messaging_type=MESSAGE_TAG`` + ``tag=HUMAN_AGENT``
  (7 days). ⚠️ Compliance tripwire: the AI must NEVER use that tag, so the retry
  only happens when the conversation is actually in human hands.

Identity: a PSID is **page-scoped** — the same human has a different PSID on
another Page — so the contact key is ``(channel_id, PSID)``, which is exactly
what the core already uses (D9). The account dedup key is the ``page_id``
(known at create time, like whatsapp_cloud's ``phone_number_id``).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from channels.base import AccountIdentity, ChannelCapabilities
from channels.base import SendResult
from .meta_graph import (DEFAULT_GRAPH_VERSION, HTTP_TIMEOUT,
                         MEDIA_TIMEOUT, MetaGraphChannel,
                         graph_error)

logger = logging.getLogger(__name__)

# Tag da conversa que o core aplica no handoff pra humano (CLAUDE.md "Gate de
# humano"). É o sinal que libera — e SÓ ele — a tag HUMAN_AGENT fora das 24h.
TRANSFER_TAG = "transferido_atendente"

# Erros da Meta que significam "fora da janela de mensagens": 10 =
# permission/policy (o clássico "message sent outside of allowed window"),
# 2018278 = subcódigo específico da janela de 24h.
_WINDOW_ERROR_MARKERS = ("outside of allowed window", "outside the allowed window",
                         "24-hour", "24 hour", "2018278")
# O código vem de ``graph_error`` como "(code 10)" / "(code 10/2018278)". Casar por
# substring ("code 10") pegava TODO erro code 100 — o genérico da Graph API — e
# mascarava a causa real (ex.: anexo recusado) como "fora das 24h".
_WINDOW_CODE_RE = re.compile(r"\bcode 10(?:/(\d+))?\b")

# Limites de anexo da Messenger Platform (a POLÍTICA é do provider; o core só
# avalia — plano 65). A Meta aceita até 25 MB por anexo, qualquer container comum;
# acima disso o painel bloqueia o anexo com popup em vez de deixar o envio falhar.
# Import defensivo: em core anterior ao plano 65 ``MediaLimits`` não existe e o
# plugin continua carregando (sem limites declarados = nada bloqueado).
try:
    from channels.base import MediaLimits, VideoLimits

    _ATTACHMENT_CAP = 25 * 1024 * 1024
    _MEDIA_LIMITS = {kind: MediaLimits(max_bytes=_ATTACHMENT_CAP)
                     for kind in ("image", "audio", "document")}
    # Vídeo: a Meta busca o arquivo pela URL e entrega no player do Messenger, que
    # na prática só reproduz MP4 H.264/AAC (um .mov/.f4v vira "não foi possível
    # reproduzir"). Declarar os codecs opta o canal pelo transcode automático do
    # core (ffmpeg) em vez de mandar um arquivo que o destinatário não abre.
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


class FacebookMessengerChannel(MetaGraphChannel):
    provider = "facebook_messenger"
    graph_host = "graph.facebook.com"
    token_credential_key = "page_access_token"
    profile_fields = ("first_name", "last_name", "profile_pic")

    # ── Contact type (marca por canal — plano tipos-de-contato) ──────
    @classmethod
    def contact_type(cls) -> str:
        return "facebook"

    def __init__(self, channel_id: str, registry=None,
                 credentials: Optional[dict] = None):
        super().__init__(
            channel_id,
            ChannelCapabilities(
                qr=False,
                templates=False,       # Messenger has no HSM equivalent
                groups=False,
                presence=True,         # sender_action typing_on/off
                reactions=True,
                media=True,
                revoke=False,          # no delete-for-everyone on the Send API
                edit_message=False,
                inbound_route="path",
                session_window_hours=24,
                # HUMAN_AGENT tag: 7 days, HUMANS ONLY (see _post_with_window_fallback).
                human_window_hours=24 * 7,
                required_credentials=("page_id", "page_access_token", "verify_token"),
                **({"media_limits": _MEDIA_LIMITS} if _MEDIA_LIMITS else {}),
            ),
            registry=registry,
            credentials=credentials,
        )

    # ── Provider descriptor (plano 33) ───────────────────────────────
    @classmethod
    def provider_descriptor(cls) -> dict:
        return {
            "provider": "facebook_messenger",
            "label": "Facebook Messenger",
            "color": "blue",
            "credential_fields": [
                {"key": "page_id", "label": "ID da Página", "type": "text",
                 "required": True, "placeholder": "ID numérico da Página",
                 "help": "Facebook → sua Página → Sobre → ID da Página. É a conta "
                         "que recebe as mensagens."},
                {"key": "page_access_token", "label": "Page Access Token",
                 "type": "secret", "required": True, "placeholder": "EAAB...",
                 "help": "Token da Página (derivado de um token de usuário "
                         "long-lived, ou de um System User em produção). Não "
                         "expira por tempo."},
                {"key": "app_secret", "label": "App Secret", "type": "secret",
                 "required": False, "placeholder": "segredo do app na Meta",
                 "help": "Usado para validar a assinatura X-Hub-Signature-256 dos "
                         "webhooks e assinar as chamadas (appsecret_proof). Sem "
                         "ele o webhook aceita qualquer POST — recomendado."},
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
            # Registra o Callback URL + Verify Token + campos no app da Meta via
            # Graph API ao criar o canal; cai num aviso "cole a URL à mão" se a
            # instância não tiver HTTPS público ou faltar app_secret.
            "post_create": {
                "kind": "autoconfigure",
                "endpoint": "/api/plugins/facebook_messenger/autoconfigure",
                "webhook_path": "/api/webhook/facebook_messenger/{channel_id}",
            },
            "form_component": None,
        }

    # ── Account identity (dedup — plano 32) ──────────────────────────
    @classmethod
    def identity_from_credentials(cls, creds: dict) -> Optional[AccountIdentity]:
        """A Messenger account IS the Page: two channels on the same ``page_id``
        are the same inbox connected twice (409 before persisting)."""
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
                resp = client.get(f"{self._graph_base()}/{page_id}",
                                  params=self._auth_params({"fields": "name"}))
            if resp.status_code == 200:
                data = resp.json() or {}
                return {"connected": True, "logged_in": True, "needs_qr": False,
                        "error": None, "verified_name": data.get("name"),
                        "own_phone": None}
            return {"connected": False, "logged_in": False, "needs_qr": False,
                    "error": graph_error(resp)}
        except Exception as e:  # noqa: BLE001
            return {"connected": False, "logged_in": False, "needs_qr": False,
                    "error": str(e)}

    # ── Outbound: 24h window + HUMAN_AGENT fallback (02.2) ───────────
    def _post_with_window_fallback(self, chat_id: str, payload: dict, *,
                                   timeout: float = HTTP_TIMEOUT) -> SendResult:
        """Send, and on a "outside the 24h window" refusal retry ONCE as
        ``MESSAGE_TAG`` + ``tag=HUMAN_AGENT`` — but only when the toggle is on AND
        the conversation is really with a human attendant.

        Doing it reactively (on Meta's own error) instead of pre-computing the
        window keeps the provider stateless: no clock skew, no extra DB read on the
        happy path, and the tag is used in exactly the case Meta allows.
        """
        result = self._post_message(payload, timeout=timeout)
        if result.ok or not _is_window_error(result.error):
            return result
        if not self._config_bool("human_agent_tag", False):
            return SendResult(ok=False, error=_window_message(result.error))
        if not self._conversation_with_human(chat_id):
            return SendResult(ok=False, error=_window_message(result.error))
        retry = dict(payload)
        retry["messaging_type"] = "MESSAGE_TAG"
        retry["tag"] = "HUMAN_AGENT"
        logger.info("[facebook_messenger] fora da janela de 24h — reenviando como "
                    "HUMAN_AGENT (atendimento humano) para %s", chat_id)
        return self._post_message(retry)

    def _conversation_with_human(self, chat_id: str) -> bool:
        """Whether this contact is currently in HUMAN hands (handoff).

        Uses the core's own handoff signal (the ``transferido_atendente`` tag the
        gate in ``messaging_service`` reads) — so the AI, which never carries it,
        can never trigger the HUMAN_AGENT tag. Fails CLOSED: any error means "not
        human", i.e. no tag.
        """
        try:
            from db.repositories import contact_repo, tag_repo

            contact = contact_repo.get_by_phone(chat_id)
            if not contact:
                return False
            tags = tag_repo.get_contact_tags(contact["id"]) or []
            names = {(t.get("name") if isinstance(t, dict) else str(t)) for t in tags}
            return TRANSFER_TAG in names
        except Exception:  # noqa: BLE001
            logger.debug("[facebook_messenger] handoff lookup failed", exc_info=True)
            return False

    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        payload = self._message_envelope(chat_id, {"text": text})
        if reply_to:
            payload["message"]["reply_to"] = {"mid": reply_to}
        return self._post_with_window_fallback(chat_id, payload)

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        # O tipo do anexo sai do MIME real do arquivo (base, D4): a Meta recusa um
        # .mp4 declarado como ``file`` com code 100/2018007.
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


def _window_message(original: str) -> str:
    return ("Fora da janela de 24h do Messenger: só um atendente humano pode "
            "responder (ative 'Responder fora das 24h como agente humano' na "
            f"configuração do canal e transfira o atendimento). [{original}]")


# Exported for the plugin loader (entry.channels → CHANNEL_PROVIDERS).
CHANNEL_PROVIDERS = [FacebookMessengerChannel]
