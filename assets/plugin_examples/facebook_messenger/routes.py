"""REST endpoints do plugin facebook_messenger (/api/plugins/facebook_messenger).

Conveniências de UI do provider — o webhook em si é do CORE
(``/api/webhook/facebook_messenger/{channel_id}``, registrado no App Dashboard da
Meta). Aqui ficam:

* ``GET  /info`` — metadados pra tela de configuração;
* ``GET  /channels`` — canais deste provider + a URL de callback esperada;
* ``POST /subscribe`` — assina o app nos campos de webhook da Página
  (``POST /{page_id}/subscribed_apps``);
* ``GET  /webhook-status`` — lê os campos assinados e diz se está tudo certo.

Chamadas Graph são httpx INLINE aqui por simplicidade. (Importar um IRMÃO do
plugin FUNCIONA via o pacote registrado — ``from whatsbot_plugins.facebook_messenger
import channels`` — como o website faz em routes.py; o sys.path não tem os
submódulos, mas o pacote registrado sim.) Segredos NUNCA são ecoados na resposta
nem logados.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request

from db.repositories import channel_credential_repo, channel_repo, config_repo
from plugins.context import core_permission

logger = logging.getLogger(__name__)

GRAPH_HOST = "graph.facebook.com"
DEFAULT_GRAPH_VERSION = "v25.0"
HTTP_TIMEOUT = 20.0
PROVIDER = "facebook_messenger"

# Campos de webhook que uma caixa de entrada de Messenger precisa.
SUBSCRIBED_FIELDS = (
    "messages,messaging_postbacks,message_deliveries,message_reads,"
    "message_echoes,messaging_handovers,standby"
)

router = APIRouter()

PLUGIN_ID = "facebook_messenger"

# ── Trilha de auditoria (docs/PLUGINS_AUDITAVEIS.md) ──────────────────────────
# Import defensivo: o plugin é importável por .zip e pode cair num core anterior
# ao seam — sem o helper ele continua funcionando, só não registra.
try:
    from plugins.context import audit as _core_audit
except ImportError:  # pragma: no cover — core antigo
    _core_audit = None


def _audit(action: str, channel_id: str, **kw) -> None:
    """Registra uma ação deste plugin na Auditoria. Nunca quebra a rota.

    Plugin de CANAL: grava como ``channel:<channel_id>`` (não ``plugin:<id>``)
    para cair no MESMO recurso dos eventos de canal do core — um filtro por canal
    devolve a história inteira dele.
    """
    if _core_audit is None:
        return
    try:
        _core_audit(PLUGIN_ID, action, resource_type="channel",
                    resource_id=channel_id, **kw)
    except Exception:  # noqa: BLE001 — auditoria nunca derruba a ação auditada
        pass



def _creds(channel_id: str) -> dict:
    if not channel_id:
        return {}
    try:
        return channel_credential_repo.get_all(channel_id) or {}
    except Exception:  # noqa: BLE001
        return {}


def _graph_base(creds: dict) -> str:
    version = (creds.get("graph_api_version") or "").strip() or DEFAULT_GRAPH_VERSION
    return f"https://{GRAPH_HOST}/{version}"


def _auth_params(creds: dict, extra: dict | None = None) -> dict:
    token = creds.get("page_access_token") or ""
    secret = creds.get("app_secret") or ""
    params = {"access_token": token}
    if token and secret:
        params["appsecret_proof"] = hmac.new(
            secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
    if extra:
        params.update(extra)
    return params


def _graph_error(resp) -> str:
    try:
        err = (resp.json() or {}).get("error") or {}
        msg = err.get("error_user_msg") or err.get("message")
        if msg:
            return str(msg)
    except Exception:  # noqa: BLE001
        pass
    return f"http_{resp.status_code}: {resp.text[:300]}"


def _expected_webhook_url(channel_id: str) -> str:
    base = (config_repo.get("public_base_url", "") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/api/webhook/{PROVIDER}/{channel_id}"


def _list_channels() -> list[dict]:
    out = []
    for row in channel_repo.list_all() or []:
        if row.get("provider") != PROVIDER or row.get("archived"):
            continue
        out.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "enabled": bool(row.get("enabled")),
            "webhook_url": _expected_webhook_url(row.get("id") or ""),
        })
    return out


@router.get("/info", dependencies=[core_permission("channel.manage")])
async def info():
    """Metadados do provider para a tela de configuração."""
    return {"ok": True, "data": {
        "provider": PROVIDER,
        "name": "Facebook Messenger",
        "graph_api_version": DEFAULT_GRAPH_VERSION,
        "subscribed_fields": SUBSCRIBED_FIELDS.split(","),
        "capabilities": {"qr": False, "templates": False, "groups": False,
                         "inbound_route": "path", "session_window_hours": 24},
        "credential_keys": ["page_id", "page_access_token", "app_secret",
                            "verify_token"],
        "webhook_path_template": f"/api/webhook/{PROVIDER}/{{channel_id}}",
    }}


@router.get("/channels", dependencies=[core_permission("channel.manage")])
async def list_channels():
    channels = await asyncio.to_thread(_list_channels)
    return {"ok": True, "data": {"channels": channels}}


def _subscribe(channel_id: str) -> tuple[bool, str]:
    creds = _creds(channel_id)
    page_id = creds.get("page_id") or ""
    if not page_id or not creds.get("page_access_token"):
        return False, "canal sem page_id ou page_access_token"
    url = f"{_graph_base(creds)}/{page_id}/subscribed_apps"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, params=_auth_params(
                creds, {"subscribed_fields": SUBSCRIBED_FIELDS}))
        if resp.status_code in (200, 201) and (resp.json() or {}).get("success", True):
            return True, ""
        return False, _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


@router.post("/subscribe", dependencies=[core_permission("channel.manage")])
async def subscribe(body: dict):
    """Assina o app nos campos de webhook da Página (obrigatório uma vez)."""
    channel_id = (body.get("channel_id") or "").strip()
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    ok, err = await asyncio.to_thread(_subscribe, channel_id)
    if ok:
        _audit("pagina.subscribe", channel_id,
               after={"fields": SUBSCRIBED_FIELDS.split(",")})
    return {"ok": ok, "error": err or None,
            "data": {"fields": SUBSCRIBED_FIELDS.split(",")} if ok else None}


# ── Autoconfigure (post_create `autoconfigure`, plano 33) ─────────────────────
# São DUAS assinaturas distintas na Meta, e as duas são obrigatórias:
#   1. APP  → ``POST /{app_id}/subscriptions`` (object=page): define o callback_url
#      + verify_token + fields do APP inteiro. É o passo que antes era manual no
#      App Dashboard. Usa APP access token (``{app_id}|{app_secret}``) — NUNCA
#      mandar ``appsecret_proof`` aqui (o token já é auto-assinado; um proof
#      calculado com outro token devolve "Invalid appsecret_proof", code 100).
#   2. PÁGINA → ``POST /{page_id}/subscribed_apps``: liga ESTA Página ao app.
# ⚠️ Um app tem UM callback_url por objeto: registrar aqui SOBRESCREVE a URL de
# qualquer outro sistema que use o mesmo app para receber webhooks de Página.

def _app_id(creds: dict) -> tuple[str, str]:
    """``app_id`` da credencial ou, se vazio, detectado pelo dono do token.

    ``GET /app`` com o page access token devolve o app que o emitiu — evita pedir
    mais um campo ao operador. Retorna ``(app_id, erro)``."""
    explicit = (creds.get("app_id") or "").strip()
    if explicit:
        return explicit, ""
    token = creds.get("page_access_token") or ""
    if not token:
        return "", "canal sem page_access_token"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(f"{_graph_base(creds)}/app", params=_auth_params(creds))
        if resp.status_code != 200:
            return "", _graph_error(resp)
        app_id = str((resp.json() or {}).get("id") or "")
        return (app_id, "") if app_id else ("", "não foi possível detectar o app_id")
    except Exception as e:  # noqa: BLE001
        return "", str(e)[:200]


def _register_app_webhook(channel_id: str, callback_url: str) -> tuple[bool, str]:
    """Aponta o webhook ``object=page`` do app para ESTA instância do WhatsBot."""
    creds = _creds(channel_id)
    secret = creds.get("app_secret") or ""
    verify_token = creds.get("verify_token") or ""
    if not secret:
        return False, ("canal sem app_secret — é ele que forma o app access token "
                       "necessário para registrar o callback")
    if not verify_token:
        return False, "canal sem verify_token"
    app_id, err = _app_id(creds)
    if not app_id:
        return False, err or "app_id indisponível"
    url = f"{_graph_base(creds)}/{app_id}/subscriptions"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, params={
                "object": "page",
                "callback_url": callback_url,
                "verify_token": verify_token,
                "fields": SUBSCRIBED_FIELDS,
                "include_values": "true",
                "access_token": f"{app_id}|{secret}",
            })
        if resp.status_code in (200, 201) and (resp.json() or {}).get("success", True):
            return True, ""
        return False, _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def _read_configured_url(creds: dict) -> tuple[str | None, str]:
    """URL de callback que a Meta tem configurada no webhook ``object=page`` do app.

    Lê ``GET /{app_id}/subscriptions`` (precisa do app access token
    ``{app_id}|{app_secret}``) e devolve o ``callback_url`` da entrada
    ``object=page``. Retorna ``(callback_url|None, reason)``; ``reason`` só
    preenchido em erro de leitura. Sem ``callback_url`` cadastrado ⇒ ``(None, "")``
    = ``unset``."""
    secret = creds.get("app_secret") or ""
    if not secret:
        return None, "sem app_secret (necessário para ler o callback do app)"
    app_id, err = _app_id(creds)
    if not app_id:
        return None, err or "app_id indisponível"
    url = f"{_graph_base(creds)}/{app_id}/subscriptions"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(url, params={"access_token": f"{app_id}|{secret}"})
        if resp.status_code != 200:
            return None, _graph_error(resp)
        for item in ((resp.json() or {}).get("data") or []):
            if item.get("object") == "page":
                return (item.get("callback_url") or None), ""
        return None, ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:200]


# ── URL normalization + classification (espelha whatsapp_cloud · D3) ──────────

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
    """``ok`` / ``wrong_domain`` / ``wrong_path`` / ``unset`` / ``unknown``."""
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


def _base_from_request(request: Request) -> str:
    """Base pública: ``public_base_url`` da config; senão os headers de proxy."""
    saved = (config_repo.get("public_base_url", "") or "").strip().rstrip("/")
    if saved:
        return saved
    headers = request.headers
    proto = (headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/") if host else ""


def _host_is_public(host: str) -> bool:
    h = (host or "").lower()
    return bool(h) and h not in ("localhost", "127.0.0.1", "::1") and "." in h \
        and not h.endswith(".local") and not h.startswith(("10.", "192.168.", "172."))


@router.post("/autoconfigure", dependencies=[core_permission("channel.manage")])
async def autoconfigure(body: dict, request: Request):
    """Chamado pelo core ao CRIAR um canal Messenger — registra tudo sozinho.

    Faz as duas assinaturas (app + Página) e devolve o resultado no formato que o
    ``AutoconfigureNotice`` genérico do core renderiza."""
    channel_id = (body.get("channel_id") or "").strip()
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}

    base = await asyncio.to_thread(_base_from_request, request)
    host = urlsplit(base).hostname or ""
    webhook_url = f"{base}/api/webhook/{PROVIDER}/{channel_id}" if base else ""

    def _manual(reason: str) -> dict:
        return {"ok": True, "data": {
            "mode": "manual", "registered": False, "reason": reason,
            "webhook_url": webhook_url,
            "message": ("Cole esta URL como Callback URL do webhook do produto "
                        "Messenger no App Dashboard da Meta, com o Verify Token do "
                        "canal, e assine os campos "
                        f"{SUBSCRIBED_FIELDS.replace(',', ', ')}."),
        }}

    if not base or not base.startswith("https://") or not _host_is_public(host):
        return _manual("esta instância não tem uma URL pública HTTPS "
                       "(configure o acesso pelo domínio antes)")

    ok, err = await asyncio.to_thread(_register_app_webhook, channel_id, webhook_url)
    if not ok:
        logger.warning("facebook_messenger: subscriptions falhou (%s): %s",
                       channel_id, err)
        return _manual(err or "registro do callback falhou")

    sub_ok, sub_err = await asyncio.to_thread(_subscribe, channel_id)
    if not sub_ok:
        logger.warning("facebook_messenger: subscribed_apps falhou (%s): %s",
                       channel_id, sub_err)
    # Registro automático no create: grava o callback do APP + assina a Página —
    # e SOBRESCREVE a callback_url de qualquer outro sistema no mesmo app.
    _audit("webhook.autoconfigure", channel_id,
           after={"mode": "webhook", "registered": True, "url": webhook_url,
                  "page_subscribed": sub_ok})
    return {"ok": True, "data": {
        "mode": "webhook", "registered": True, "webhook_url": webhook_url,
        "page_subscribed": sub_ok, "reason": "" if sub_ok else sub_err,
        "fields": SUBSCRIBED_FIELDS.split(","),
        "message": ("A Meta já está entregando as mensagens da Página neste "
                    "endereço. Atenção: o callback do app foi SOBRESCRITO — outro "
                    "sistema que usasse o mesmo app para webhooks de Página parou "
                    "de receber."
                    + ("" if sub_ok else f" A assinatura da Página falhou: {sub_err}")),
    }}


def _webhook_status(channel_id: str, expected_url: str = "") -> dict:
    """Saúde do webhook do canal. Junta DUAS checagens:

    1. **Página assinada** nos campos de webhook (``/{page_id}/subscribed_apps``,
       só precisa do page token) → ``subscribed`` / ``subscribed_fields``.
    2. **Callback do app** vs a URL que ESTA instância espera → ``configured_url``
       + ``match`` (``ok``/``wrong_domain``/``wrong_path``/``unset``/``unknown``),
       espelhando o WebhookHealthRow do whatsapp_cloud. ``can_set`` diz se dá pra
       repointar com 1 clique (precisa de ``app_secret`` + ``verify_token``)."""
    creds = _creds(channel_id)
    expected = (expected_url or "").strip() or _expected_webhook_url(channel_id)
    page_id = creds.get("page_id") or ""
    can_set = bool(creds.get("app_secret") and creds.get("verify_token"))
    result = {"expected_url": expected, "subscribed_fields": [],
              "subscribed": False, "reason": "",
              "has_app_secret": bool(creds.get("app_secret")),
              "configured_url": None, "match": "unknown", "can_set": can_set}
    if not page_id or not creds.get("page_access_token"):
        result["reason"] = "canal sem page_id ou page_access_token"
        return result
    # 1. A Página está assinada nos campos deste app?
    url = f"{_graph_base(creds)}/{page_id}/subscribed_apps"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(url, params=_auth_params(creds))
        if resp.status_code == 200:
            fields: list[str] = []
            for item in ((resp.json() or {}).get("data") or []):
                fields.extend(item.get("subscribed_fields") or [])
            result["subscribed_fields"] = fields
            result["subscribed"] = "messages" in fields
            if not result["subscribed"]:
                result["reason"] = "o app não está assinado no campo 'messages' da Página"
        else:
            result["reason"] = _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        result["reason"] = str(e)[:200]
    # 2. O callback do app aponta pra ESTA instância?
    configured, cb_reason = _read_configured_url(creds)
    result["configured_url"] = configured
    if configured is None and cb_reason:
        result["match"] = "unknown"
        if not result["reason"]:
            result["reason"] = cb_reason
    else:
        result["match"] = _classify(configured, expected)
    return result


@router.get("/webhook-status", dependencies=[core_permission("channel.manage")])
async def webhook_status(channel_id: str = "", expected_url: str = ""):
    """Saúde do webhook: Página assinada + o callback do app aponta pra cá?"""
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    data = await asyncio.to_thread(_webhook_status, channel_id, expected_url)
    return {"ok": True, "data": data}


@router.post("/set-webhook", dependencies=[core_permission("channel.manage")])
async def set_webhook(body: dict):
    """Aponta o callback do webhook (nível do app, ``object=page``) para ``url`` e
    reassina a Página. Mesma mecânica do ``/autoconfigure``, mas com a URL vinda do
    frontend (a que ESTA instância espera) — é o botão "Configurar webhook" do card.
    Em sucesso, re-lê e devolve o novo ``match``."""
    channel_id = (body.get("channel_id") or "").strip()
    url = (body.get("url") or "").strip()
    if not channel_id or not url:
        return {"ok": False, "error": "channel_id e url são obrigatórios"}
    ok, err = await asyncio.to_thread(_register_app_webhook, channel_id, url)
    if not ok:
        return {"ok": False, "error": err or "falha ao configurar o webhook"}
    sub_ok, sub_err = await asyncio.to_thread(_subscribe, channel_id)
    data = await asyncio.to_thread(_webhook_status, channel_id, url)
    data["page_subscribed"] = sub_ok
    if not sub_ok and not data.get("reason"):
        data["reason"] = sub_err
    # Um app tem UMA callback_url por objeto: apontar aqui SOBRESCREVE a URL de
    # qualquer outro sistema que use o mesmo app. Fica na trilha com quem mandou.
    _audit("webhook.set", channel_id,
           after={"url": url, "page_subscribed": sub_ok,
                  "match": data.get("match")})
    return {"ok": True, "data": data}
