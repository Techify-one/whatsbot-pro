"""REST endpoints do plugin telegram (mountados em /api/plugins/telegram).

Conveniências para a tela de configuração — NÃO incluem o webhook em si (o
webhook do Telegram é do core: ``/api/webhook/telegram/{channel_id}``). Aqui
ficam: listar os canais telegram, validar o token (getMe) e registrar/remover o
webhook na Bot API usando o token do canal (que nunca vai ao frontend).

Plugin de 1ª parte: lê o token via o ``channel_credential_repo`` do core (mesma
fonte do registry). Chamadas HTTP à Bot API são síncronas e isoladas em
``asyncio.to_thread`` (o handler é async).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request

from db.repositories import channel_repo, channel_credential_repo
from whatsbot_plugins.telegram.mode import set_mode

logger = logging.getLogger(__name__)

router = APIRouter()

HTTP_TIMEOUT = 20.0

# Telegram só entrega webhook por HTTPS (portas 443/80/88/8443).
_ALLOWED_UPDATES = ["message", "edited_message", "channel_post",
                    "message_reaction", "callback_query"]


def _api_base() -> str:
    return os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


def _host_is_public(host: str) -> bool:
    """True se ``host`` é um domínio/IP público (não localhost/privado/.local).

    O webhook do Telegram precisa ser alcançável da internet; acesso local
    (desktop/EXE, LAN) não serve — nesses casos mantemos o long-poll.
    """
    host = (host or "").strip().lower().split(":")[0]
    if not host or host in ("localhost", "::1"):
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_global  # exclui loopback/privado/link-local
    except ValueError:
        # É um hostname (domínio). Exige um ponto e não ser um label só.
        return "." in host


def _forwarded_base(request: Request) -> tuple[str, bool]:
    """Deriva (base_url, is_https) do request, respeitando o proxy reverso.

    Coolify/Traefik setam ``X-Forwarded-Proto``/``X-Forwarded-Host``; caímos no
    ``Host`` e no ``request.url`` como fallback.
    """
    headers = request.headers
    proto = (headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (headers.get("x-forwarded-host") or headers.get("host") or request.url.netloc or "").split(",")[0].strip()
    base = f"{proto}://{host}".rstrip("/")
    return base, proto.lower() == "https"


def _call(token: str, method: str, payload: dict | None = None) -> dict:
    if not token:
        return {"ok": False, "error": "missing_bot_token"}
    url = f"{_api_base()}/bot{token}/{method}"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, json=payload or {})
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("ok"):
            return {"ok": True, "result": data.get("result")}
        return {"ok": False, "error": data.get("description") or f"http_{resp.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@router.get("/channels")
async def list_telegram_channels():
    """Canais com provider=telegram (id + display_name) para a tela de config."""
    rows = await asyncio.to_thread(channel_repo.list_all)
    out = [{"id": r["id"], "display_name": r.get("display_name") or r["id"],
            "enabled": bool(r.get("enabled", 1))}
           for r in rows if r.get("provider") == "telegram"]
    return {"ok": True, "data": out}


@router.get("/status")
async def channel_status(channel_id: str = ""):
    """Valida o token do canal (getMe) + estado do webhook (getWebhookInfo)."""
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": True, "data": {"configured": False}}
    me = await asyncio.to_thread(_call, token, "getMe")
    info = await asyncio.to_thread(_call, token, "getWebhookInfo")
    from whatsbot_plugins.telegram.mode import get_mode
    mode = await asyncio.to_thread(get_mode, channel_id)
    return {"ok": True, "data": {
        "configured": True,
        "mode": mode,
        "me": me.get("result") if me.get("ok") else None,
        "me_error": None if me.get("ok") else me.get("error"),
        "webhook": info.get("result") if info.get("ok") else None,
    }}


@router.get("/public-base")
async def public_base(request: Request):
    """Detecta se a instalação tem domínio público (HTTPS) apontado.

    Usado pela tela de config pra recomendar webhook quando viável. A decisão
    final do frontend também olha ``location.origin``; aqui corroboramos pelo
    proxy reverso (X-Forwarded-*)."""
    base, is_https = _forwarded_base(request)
    host = urlsplit(base).hostname or ""
    is_public = is_https and _host_is_public(host)
    reason = None
    if not is_https:
        reason = "sem HTTPS"
    elif not _host_is_public(host):
        reason = "host local/privado"
    return {"ok": True, "data": {
        "base_url": base,
        "is_https": is_https,
        "is_public": is_public,
        "reason": reason,
    }}


def _validate_https(url: str) -> str | None:
    """Retorna mensagem de erro se ``url`` não for um webhook válido pro Telegram."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        return "o Telegram exige uma URL HTTPS pública para o webhook"
    if not parts.hostname or not _host_is_public(parts.hostname):
        return "host não é público (localhost/IP privado não funciona como webhook)"
    return None


async def _set_webhook(channel_id: str, url: str) -> dict:
    """setWebhook + confirma via getWebhookInfo. Não mexe no inbound_mode."""
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": False, "error": "canal sem bot_token"}
    err = _validate_https(url)
    if err:
        return {"ok": False, "error": err}
    res = await asyncio.to_thread(_call, token, "setWebhook", {
        "url": url,
        "allowed_updates": _ALLOWED_UPDATES,
    })
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    info = await asyncio.to_thread(_call, token, "getWebhookInfo")
    return {"ok": True, "data": res.get("result"),
            "webhook": info.get("result") if info.get("ok") else None}


@router.post("/set-webhook")
async def set_webhook(body: dict):
    channel_id = (body.get("channel_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not channel_id or not url:
        return {"ok": False, "error": "channel_id e url são obrigatórios"}
    res = await _set_webhook(channel_id, url)
    return {"ok": bool(res.get("ok")), "error": res.get("error"),
            "data": res.get("data"), "webhook": res.get("webhook")}


@router.post("/autoconfigure")
async def autoconfigure(body: dict, request: Request):
    """Chamado pelo core ao CRIAR uma inbox Telegram (default = webhook se der).

    Detecta domínio público (HTTPS) pelo próprio request (X-Forwarded-*). Se houver,
    registra o webhook automaticamente e marca o canal como ``webhook``; se não houver
    — ou se o ``setWebhook`` falhar (cert/host inacessível) — cai em long-poll. Sem
    restart: o poll loop relê o modo por-canal ao vivo. Devolve a URL do webhook e o
    modo final pro frontend mostrar/confirmar."""
    channel_id = (body.get("channel_id") or "").strip()
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": False, "error": "canal sem bot_token"}

    base, is_https = _forwarded_base(request)
    host = urlsplit(base).hostname or ""
    is_public = is_https and _host_is_public(host)
    webhook_url = f"{base}/api/webhook/telegram/{channel_id}"

    async def _fallback_poll(reason: str) -> dict:
        # Clear any pre-existing webhook so getUpdates (long-poll) actually works
        # (an active webhook makes Telegram answer getUpdates with 409). No-op on a
        # fresh bot; defensive when re-running on a channel that had a webhook.
        await asyncio.to_thread(_call, token, "deleteWebhook", {"drop_pending_updates": False})
        await asyncio.to_thread(set_mode, channel_id, "poll")
        _ensure_poll_running()
        return {"ok": True, "data": {"mode": "poll", "registered": False,
                                     "reason": reason, "webhook_url": webhook_url}}

    if not is_public:
        return await _fallback_poll("sem HTTPS" if not is_https else "host local/privado")

    res = await _set_webhook(channel_id, webhook_url)
    if not res.get("ok"):
        return await _fallback_poll(res.get("error") or "setWebhook falhou")

    await asyncio.to_thread(set_mode, channel_id, "webhook")
    return {"ok": True, "data": {"mode": "webhook", "registered": True,
                                 "webhook_url": webhook_url,
                                 "webhook": res.get("webhook")}}


# NB: o modo de recebimento (webhook/long-poll) é decidido AUTOMATICAMENTE em
# ``/autoconfigure`` (chamado pelo core ao criar a inbox). NÃO há endpoint de troca
# manual de modo — por decisão de produto, o operador não configura webhook/long-poll
# pela tela do plugin.


def _ensure_poll_running() -> None:
    """Re-arm the long-poll task after a channel flips to poll (best-effort)."""
    try:
        from whatsbot_plugins.telegram.lifecycle import ensure_poll_running
        ensure_poll_running()
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram: could not re-arm poll loop: %s", e)
