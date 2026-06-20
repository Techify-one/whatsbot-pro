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
import os

import httpx
from fastapi import APIRouter

from db.repositories import channel_repo, channel_credential_repo

router = APIRouter()

HTTP_TIMEOUT = 20.0


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
    return {"ok": True, "data": {
        "configured": True,
        "me": me.get("result") if me.get("ok") else None,
        "me_error": None if me.get("ok") else me.get("error"),
        "webhook": info.get("result") if info.get("ok") else None,
    }}


@router.post("/set-webhook")
async def set_webhook(body: dict):
    channel_id = (body.get("channel_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not channel_id or not url:
        return {"ok": False, "error": "channel_id e url são obrigatórios"}
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": False, "error": "canal sem bot_token"}
    res = await asyncio.to_thread(_call, token, "setWebhook", {
        "url": url,
        "allowed_updates": ["message", "edited_message", "channel_post",
                            "message_reaction", "callback_query"],
    })
    return {"ok": bool(res.get("ok")), "error": res.get("error"), "data": res.get("result")}


@router.post("/delete-webhook")
async def delete_webhook(body: dict):
    channel_id = (body.get("channel_id") or "").strip()
    token = await asyncio.to_thread(channel_credential_repo.get, channel_id, "bot_token")
    if not token:
        return {"ok": False, "error": "canal sem bot_token"}
    res = await asyncio.to_thread(_call, token, "deleteWebhook", {"drop_pending_updates": False})
    return {"ok": bool(res.get("ok")), "error": res.get("error")}
