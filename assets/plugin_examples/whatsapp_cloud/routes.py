"""REST endpoints do plugin whatsapp_cloud (mountados em /api/plugins/whatsapp_cloud).

São endpoints de AJUDA/UI do provider — NÃO incluem o webhook. O webhook do
WhatsApp Cloud API é do core (path ``/api/webhook/whatsapp_cloud/{channel_id}``,
registrado/verificado pela Meta). Aqui ficam só conveniências para a tela de
configuração do plugin.

Plano 26 — saúde do webhook: ``GET /webhook-status`` lê o que a Meta tem
configurado (via Graph API, com o ``access_token`` salvo do canal) e classifica
contra a URL que ESTA instância espera; ``POST /set-webhook`` aponta o override
de volta pra cá (``POST /{waba_id}/subscribed_apps``); ``POST /delete-webhook``
remove o override. As chamadas Graph são httpx INLINE (não importar a classe
``WhatsAppCloudChannel`` — submódulos não estão no ``sys.path`` sob o plugin
loader; ver comentário histórico abaixo). Segredos (``access_token`` /
``verify_token``) NUNCA são ecoados na resposta nem logados.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter

from db.repositories import channel_credential_repo, config_repo

# Default Graph API version. Kept in sync with settings.Settings.graph_api_version
# (the real configured value is read from plugin settings by the core). Avoids a
# cross-module import, which is brittle under the plugin loader's file-based
# import (submodules are not on sys.path).
DEFAULT_GRAPH_API_VERSION = "v21.0"

GRAPH_BASE = "https://graph.facebook.com"
HTTP_TIMEOUT = 20.0

router = APIRouter()


# ── Graph helpers (inline — não importar de channels.py, ver docstring) ─────

def _graph_version() -> str:
    """Versão da Graph API: setting declarativa do plugin, fallback ao default."""
    val = config_repo.get("plugin.whatsapp_cloud.graph_api_version")
    return (val or "").strip() or DEFAULT_GRAPH_API_VERSION


def _graph_base() -> str:
    return f"{GRAPH_BASE}/{_graph_version()}"


def _creds(channel_id: str) -> dict:
    """Todas as credenciais do canal numa query (access_token/phone_number_id/…)."""
    if not channel_id:
        return {}
    try:
        return channel_credential_repo.get_all(channel_id) or {}
    except Exception:  # noqa: BLE001
        return {}


def _graph_error(resp) -> str:
    """Mensagem humana a partir de um erro da Graph API.

    Meta retorna ``{"error": {"message": ..., "error_user_msg": ...}}``; prefere
    a mensagem voltada ao usuário quando presente."""
    try:
        err = (resp.json() or {}).get("error") or {}
        msg = err.get("error_user_msg") or err.get("message")
        if msg:
            return f"{msg}" + (
                f" ({err['error_user_title']})" if err.get("error_user_title") else "")
    except Exception:  # noqa: BLE001
        pass
    return f"http_{resp.status_code}: {resp.text[:300]}"


def _graph_get(path: str, access_token: str, params: dict | None = None) -> tuple[dict | None, str]:
    url = f"{_graph_base()}/{path}"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(
                url, headers={"Authorization": f"Bearer {access_token}"}, params=params or {})
        if resp.status_code == 200:
            return (resp.json() if resp.content else {}), ""
        return None, _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:200]


def _graph_post(path: str, access_token: str, payload: dict) -> tuple[dict | None, str]:
    url = f"{_graph_base()}/{path}"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }, json=payload)
        if resp.status_code in (200, 201):
            return (resp.json() if resp.content else {}), ""
        return None, _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:200]


def _graph_delete(path: str, access_token: str) -> tuple[dict | None, str]:
    url = f"{_graph_base()}/{path}"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.delete(url, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code == 200:
            return (resp.json() if resp.content else {}), ""
        return None, _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:200]


# ── URL normalization + classification (D3 — compara URL inteira) ───────────

def _normalize_url(u: str) -> dict:
    """Normaliza pra comparação: host lowercase (+porta), path sem trailing ``/``,
    ignora querystring/fragment. Retorna ``{host, full}``."""
    try:
        parts = urlsplit((u or "").strip())
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").lower()
        if parts.port:
            host = f"{host}:{parts.port}"
        path = (parts.path or "").rstrip("/")
        return {"host": host, "full": f"{scheme}://{host}{path}"}
    except Exception:  # noqa: BLE001
        return {"host": "", "full": (u or "")}


def _classify(configured: str | None, expected: str) -> str:
    """``ok`` / ``wrong_domain`` / ``wrong_path`` / ``unset`` conforme D3."""
    if not configured:
        return "unset"
    if not expected:
        return "unknown"
    c = _normalize_url(configured)
    e = _normalize_url(expected)
    if c["full"] == e["full"]:
        return "ok"
    if c["host"] != e["host"]:
        return "wrong_domain"
    return "wrong_path"


def _read_via_subscribed_apps(access_token: str, waba_id: str,
                              fallback_reason: str = "") -> tuple[str | None, str]:
    """Lê o ``override_callback_uri`` de ``/{waba_id}/subscribed_apps``.

    Read OK sem override ⇒ ``(None, "")`` = ``unset``. Erro HTTP ⇒ ``(None, err)``."""
    data, err = _graph_get(f"{waba_id}/subscribed_apps", access_token)
    if data is None:
        return None, (err or fallback_reason)
    for item in (data.get("data") or []):
        uri = item.get("override_callback_uri")
        if uri:
            return uri, ""
    return None, ""


def _read_configured_url(access_token: str, phone_number_id: str,
                         waba_id: str) -> tuple[str | None, str]:
    """URL de webhook que a Meta tem configurada para este número/WABA.

    Primário: ``GET /{phone_number_id}?fields=webhook_configuration`` (só precisa
    de ``phone_number_id``; já reflete o override da WABA — ver plano P3).
    Fallback (sem ``phone_number_id`` ou erro): ``/{waba_id}/subscribed_apps``.
    Retorna ``(configured_url|None, reason)``; ``reason`` só preenchido em erro."""
    if phone_number_id:
        data, err = _graph_get(phone_number_id, access_token, {"fields": "webhook_configuration"})
        if data is None:
            if waba_id:
                return _read_via_subscribed_apps(access_token, waba_id, fallback_reason=err)
            return None, err
        wc = data.get("webhook_configuration") or {}
        configured = wc.get("whatsapp_business_account") or wc.get("application")
        return (configured or None), ""
    if waba_id:
        return _read_via_subscribed_apps(access_token, waba_id)
    return None, "sem phone_number_id nem waba_id"


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/info")
async def info():
    """Metadados do provider para a tela de configuração."""
    return {
        "ok": True,
        "data": {
            "provider": "whatsapp_cloud",
            "name": "WhatsApp Cloud API",
            "graph_api_version": DEFAULT_GRAPH_API_VERSION,
            "capabilities": {
                "qr": False,
                "templates": True,
                "groups": False,
                "inbound_route": "path",
            },
            "credential_keys": [
                "phone_number_id",
                "waba_id",
                "access_token",
                "verify_token",
                "app_secret",
            ],
            "webhook_path_template": "/api/webhook/whatsapp_cloud/{channel_id}",
        },
    }


@router.get("/webhook-status")
async def webhook_status(channel_id: str = "", expected_url: str = ""):
    """Saúde do webhook: lê o que a Meta tem configurado e classifica contra
    ``expected_url`` (a URL que ESTA instância espera, vinda do frontend)."""
    creds = await asyncio.to_thread(_creds, channel_id)
    access_token = creds.get("access_token") or ""
    phone_number_id = creds.get("phone_number_id") or ""
    waba_id = creds.get("waba_id") or ""
    verify_token = creds.get("verify_token") or ""
    can_set = bool(waba_id and verify_token)

    if not access_token:
        return {"ok": True, "data": {
            "configured_url": None, "expected_url": expected_url,
            "match": "unknown", "can_set": can_set, "reason": "sem access_token"}}

    configured_url, reason = await asyncio.to_thread(
        _read_configured_url, access_token, phone_number_id, waba_id)

    # configured ausente COM motivo ⇒ erro de leitura (unknown); sem motivo ⇒ unset.
    if configured_url is None and reason:
        return {"ok": True, "data": {
            "configured_url": None, "expected_url": expected_url,
            "match": "unknown", "can_set": can_set, "reason": reason}}

    match = _classify(configured_url, expected_url)
    return {"ok": True, "data": {
        "configured_url": configured_url, "expected_url": expected_url,
        "match": match, "can_set": can_set, "reason": ""}}


@router.post("/set-webhook")
async def set_webhook(body: dict):
    """Aponta o webhook (override no nível da WABA) de volta para ``url``.

    Usa SEMPRE o ``verify_token`` salvo do canal (senão o handshake da Meta falha,
    ver ``channel_webhook.py``). Em sucesso, re-lê e devolve o novo ``match``."""
    channel_id = (body.get("channel_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not channel_id or not url:
        return {"ok": False, "error": "channel_id e url são obrigatórios"}

    creds = await asyncio.to_thread(_creds, channel_id)
    access_token = creds.get("access_token") or ""
    waba_id = creds.get("waba_id") or ""
    verify_token = creds.get("verify_token") or ""
    if not access_token:
        return {"ok": False, "error": "canal sem access_token"}
    if not waba_id or not verify_token:
        return {"ok": False, "error": "configure WABA ID e Verify Token antes de apontar o webhook"}

    data, err = await asyncio.to_thread(
        _graph_post, f"{waba_id}/subscribed_apps", access_token,
        {"override_callback_uri": url, "verify_token": verify_token})
    if data is None:
        return {"ok": False, "error": err, "data": {"match": "unknown"}}

    configured_url, _reason = await asyncio.to_thread(
        _read_configured_url, access_token, creds.get("phone_number_id") or "", waba_id)
    return {"ok": True, "error": None, "data": {
        "match": _classify(configured_url, url), "configured_url": configured_url}}


@router.post("/delete-webhook")
async def delete_webhook(body: dict):
    """Remove o override de webhook da WABA (volta pro webhook do App)."""
    channel_id = (body.get("channel_id") or "").strip()
    creds = await asyncio.to_thread(_creds, channel_id)
    access_token = creds.get("access_token") or ""
    waba_id = creds.get("waba_id") or ""
    if not access_token or not waba_id:
        return {"ok": False, "error": "canal sem access_token ou waba_id"}

    data, err = await asyncio.to_thread(_graph_delete, f"{waba_id}/subscribed_apps", access_token)
    if data is None:
        return {"ok": False, "error": err}
    return {"ok": True, "error": None}


# NOTE: template listing/sending/creation is NOT here. It lives in the CORE,
# channel-aware and capability-gated, under
# ``/api/conversations/{conv_id}/templates`` (GET list, POST send-template, POST
# create, DELETE), backed by ``OutboundRouter`` → ``WhatsAppCloudChannel`` Graph
# calls. The old plugin ``GET /templates`` stub was removed to avoid confusion —
# it always returned ``[]`` and nothing consumed it.
