"""REST endpoints do plugin telegram (mountados em /api/plugins/telegram).

Conveniências para a tela de configuração — NÃO incluem o webhook em si (o
webhook do Telegram é do core: ``/api/webhook/telegram/{channel_id}``). Aqui
ficam: listar os canais telegram, validar o token (getMe), autoconfigurar o modo
de recebimento ao criar o canal e registrar/remover o webhook na Bot API usando
o token do canal (que nunca vai ao frontend).

Plugin de 1ª parte: lê o token via o ``channel_credential_repo`` do core (mesma
fonte do registry). Chamadas HTTP à Bot API são síncronas e isoladas em
``asyncio.to_thread`` (o handler é async).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os

import httpx
from fastapi import APIRouter, Request

from db.repositories import channel_repo, channel_credential_repo, config_repo

logger = logging.getLogger(__name__)

router = APIRouter()

HTTP_TIMEOUT = 20.0
# Updates the bot subscribes to (shared by autoconfigure/set-webhook and the
# long-poll loop in lifecycle.py).
_ALLOWED_UPDATES = ["message", "edited_message", "channel_post",
                    "message_reaction", "callback_query"]


def _api_base() -> str:
    return os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


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


def _set_inbound_mode(mode: str) -> None:
    """Persist the resolved inbound mode (best-effort) so a later restart's
    lifecycle starts (poll) or skips (webhook) the long-poll loop accordingly."""
    try:
        config_repo.set("plugin.telegram.inbound_mode", mode)
    except Exception:  # noqa: BLE001
        logger.debug("telegram: failed to persist inbound_mode=%s", mode, exc_info=True)


def _is_public_https(scheme: str, host: str) -> bool:
    """True only for an HTTPS host Telegram would accept as a webhook target.

    Telegram requires HTTPS and rejects loopback/private hosts. A bare domain is
    assumed public (it has a real cert if it is serving HTTPS); a private/loopback
    IP literal is rejected so a LAN/desktop install correctly falls to long-poll."""
    if (scheme or "").lower() != "https":
        return False
    hostname = (host or "").split(":")[0].strip().lower()
    if not hostname or hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return True  # a domain name, not an IP → assume public


def _public_base_url(request: Request) -> str | None:
    """Resolve a public HTTPS base URL for the webhook, or None for long-poll.

    Prefers an explicit override (``WHATSBOT_PUBLIC_URL``/``PUBLIC_BASE_URL``),
    else derives from the request honoring the reverse-proxy headers a public
    deploy (Coolify) sets. Returns None when no public HTTPS origin is available
    (desktop/EXE/LAN), so autoconfigure falls back to long-poll."""
    env = (os.environ.get("WHATSBOT_PUBLIC_URL")
           or os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env if env.startswith("https://") else None
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "")
    proto = proto.split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or request.url.netloc or "")
    host = host.split(",")[0].strip()
    if _is_public_https(proto, host):
        return f"https://{host}"
    return None


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
    """Valida o token do canal (getMe) + estado do webhook (getWebhookInfo).

    ``mode`` é derivado POR CANAL do getWebhookInfo: ``webhook`` quando o Telegram
    reporta uma URL registrada, senão ``poll`` (o frontend usa isso para mostrar a
    URL copiável só quando em webhook)."""
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": True, "data": {"configured": False, "mode": "poll"}}
    me = await asyncio.to_thread(_call, token, "getMe")
    info = await asyncio.to_thread(_call, token, "getWebhookInfo")
    webhook = info.get("result") if info.get("ok") else None
    mode = "webhook" if (webhook and webhook.get("url")) else "poll"
    return {"ok": True, "data": {
        "configured": True,
        "mode": mode,
        "me": me.get("result") if me.get("ok") else None,
        "me_error": None if me.get("ok") else me.get("error"),
        "webhook": webhook,
    }}


@router.post("/autoconfigure")
async def autoconfigure(body: dict, request: Request):
    """Escolhe o modo de recebimento ao criar a inbox (chamado pelo frontend).

    Detecta um domínio público HTTPS → registra o webhook (``mode=webhook``);
    senão usa long-poll (``mode=poll``, com ``reason``). Retorna
    ``{mode, webhook_url?, reason?}`` — o frontend mostra a URL copiável quando em
    webhook, ou avisa que está em long-poll. Não é erro quando não há host
    público: long-poll é o caminho normal em desktop/EXE."""
    channel_id = (body.get("channel_id") or "").strip()
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": False, "error": "canal sem bot_token"}

    base = _public_base_url(request)
    if base:
        url = f"{base}/api/webhook/telegram/{channel_id}"
        res = await asyncio.to_thread(_call, token, "setWebhook", {
            "url": url, "allowed_updates": _ALLOWED_UPDATES})
        if res.get("ok"):
            await asyncio.to_thread(_set_inbound_mode, "webhook")
            return {"ok": True, "data": {"mode": "webhook", "webhook_url": url}}
        # Public origin but Telegram refused — fall back to long-poll cleanly.
        await asyncio.to_thread(_call, token, "deleteWebhook", {"drop_pending_updates": False})
        await asyncio.to_thread(_set_inbound_mode, "poll")
        return {"ok": True, "data": {"mode": "poll",
                                     "reason": res.get("error") or "setWebhook falhou"}}

    # No public HTTPS origin (desktop/EXE/LAN) → long-poll. Ensure no stale webhook
    # stays registered, so getUpdates is not blocked by a previous webhook.
    await asyncio.to_thread(_call, token, "deleteWebhook", {"drop_pending_updates": False})
    await asyncio.to_thread(_set_inbound_mode, "poll")
    return {"ok": True, "data": {"mode": "poll", "reason": "sem domínio público (HTTPS)"}}


@router.post("/set-webhook")
async def set_webhook(body: dict):
    channel_id = (body.get("channel_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not channel_id or not url:
        return {"ok": False, "error": "channel_id e url são obrigatórios"}
    # Telegram only accepts an HTTPS public URL — reject a LAN/HTTP URL up front
    # with a clear message instead of leaking the raw "bad webhook" Bot API error.
    if not url.lower().startswith("https://"):
        return {"ok": False, "error": "A URL do webhook precisa ser HTTPS pública. "
                "Sem um domínio público, use o modo long-poll (padrão)."}
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": False, "error": "canal sem bot_token"}
    res = await asyncio.to_thread(_call, token, "setWebhook", {
        "url": url, "allowed_updates": _ALLOWED_UPDATES})
    if res.get("ok"):
        await asyncio.to_thread(_set_inbound_mode, "webhook")
    return {"ok": bool(res.get("ok")), "error": res.get("error"), "data": res.get("result")}


@router.post("/delete-webhook")
async def delete_webhook(body: dict):
    channel_id = (body.get("channel_id") or "").strip()
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": False, "error": "canal sem bot_token"}
    res = await asyncio.to_thread(_call, token, "deleteWebhook", {"drop_pending_updates": False})
    if res.get("ok"):
        await asyncio.to_thread(_set_inbound_mode, "poll")
    return {"ok": bool(res.get("ok")), "error": res.get("error")}
