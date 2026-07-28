"""REST endpoints do plugin instagram (/api/plugins/instagram).

Instagram via login do Facebook (legacy IG-via-FB, molde do Chatwoot
``Channel::FacebookPage``): uma Página do Facebook conectada à conta profissional
do Instagram. TUDO em ``graph.facebook.com`` com o Page Access Token — não há mais
``graph.instagram.com`` nem token IG de 60 dias.

Conveniências de UI do provider — o webhook em si é do CORE
(``/api/webhook/instagram/{channel_id}``). Aqui ficam as ferramentas que
CONFIGURAM esse webhook na Meta sozinhas ao criar o canal (igual ao Messenger):

* ``GET  /info`` — metadados pra tela de configuração;
* ``GET  /channels`` — canais deste provider + a URL de callback esperada;
* ``POST /autoconfigure`` — chamado pelo core ao CRIAR o canal: registra o
  Callback URL no app (object=instagram) E assina a Página, sem passo manual;
* ``POST /subscribe`` — só assina a Página (``{page_id}/subscribed_apps``);
* ``POST /set-webhook`` — repointar o callback do app pra ESTA instância (botão
  "Configurar webhook" do card);
* ``GET  /webhook-status`` — Página assinada + o callback do app aponta pra cá?
* ``GET  /diagnose`` — os 3 portões do recebimento com um veredito acionável.

Duas assinaturas na Meta, ambas em graph.facebook.com:
  * **App** (registro do Callback URL): ``POST /{app_id}/subscriptions`` com
    ``object=instagram`` e o APP access token ``{app_id}|{app_secret}``. (⚠️ Um app
    tem UM callback_url por objeto: registrar aqui SOBRESCREVE o de outro sistema
    que use o mesmo app pra webhooks de Instagram.)
  * **Página** (ligar a Página ao app): ``POST /{page_id}/subscribed_apps`` com o
    Page token. Pré-requisito: a conta do Instagram conectada à Página e o acesso
    a mensagens do Instagram permitido, senão a assinatura falha em silêncio.

Chamadas Graph são httpx INLINE aqui por simplicidade. Segredos NUNCA são
ecoados na resposta nem logados.
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

# Instagram via login do Facebook: app-level config E assinatura da Página vivem
# ambos em graph.facebook.com (o app é um app Meta e a conta é alcançada pela
# Página do Facebook conectada).
GRAPH_HOST = "graph.facebook.com"
DEFAULT_GRAPH_VERSION = "v25.0"
HTTP_TIMEOUT = 20.0
STATUS_TIMEOUT = 8.0
PROVIDER = "instagram"

# ⚠️ Os DOIS endpoints da Meta têm VOCABULÁRIOS de campos diferentes:
#
# * APP (``/{app_id}/subscriptions``, object=instagram) — campos do objeto
#   ``instagram`` (aceita ``message_echoes``/``messaging_seen``).
# * PÁGINA (``/{page_id}/subscribed_apps``) — campos do objeto ``page`` (Messenger):
#   ``messages``, ``message_deliveries``, ``message_reads``, ``message_echoes``,
#   ``messaging_postbacks``. É o mesmo edge que o Chatwoot usa no
#   ``after_create_commit :subscribe`` do ``Channel::FacebookPage``.
#
# Ambos são CONSERVADORES de propósito: campos de handover/standby/optin/referral
# exigem ACESSO AVANÇADO na App Review que a maioria dos apps não tem; pedir um sem
# permissão devolve ``(#200) … permissions`` e nada é assinado. Como salvaguarda,
# ``_subscribe``/``_register_app_webhook`` caem para um set mínimo se algum campo
# for barrado.
APP_SUBSCRIBED_FIELDS = (
    "messages,messaging_postbacks,message_reactions,message_echoes,messaging_seen"
)
PAGE_SUBSCRIBED_FIELDS = (
    "messages,messaging_postbacks,message_deliveries,message_reads,message_echoes"
)
# Compat: usado por /info e como rótulo geral.
SUBSCRIBED_FIELDS = APP_SUBSCRIBED_FIELDS


def _should_retry_without_fields(err: str) -> bool:
    """Erro que se resolve reassinando SEM lista de campos: permissão faltando OU
    um nome de campo inválido para aquele endpoint."""
    low = (err or "").lower()
    return ("permission" in low or "(#200)" in low or "code 200" in low
            or "could not subscribe" in low or "must be one of" in low
            or "subscribed_fields" in low)

router = APIRouter()

PLUGIN_ID = "instagram"

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


def _version(creds: dict) -> str:
    return (creds.get("graph_api_version") or "").strip() or DEFAULT_GRAPH_VERSION


def _graph_base(creds: dict) -> str:
    return f"https://{GRAPH_HOST}/{_version(creds)}"


def _auth_params(creds: dict, extra: dict | None = None) -> dict:
    """Auth da Página: Page token (via query) + appsecret_proof quando há secret."""
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


def _me(creds: dict) -> tuple[dict, str]:
    """Valida o Page token: ``GET /{page_id}?fields=name,instagram_business_account
    {username}``. Retorna ``(data, error)`` — ``error`` vazio em sucesso; prova que
    o token abre a Página e mostra qual conta do Instagram está conectada."""
    token = creds.get("page_access_token") or ""
    page_id = (creds.get("page_id") or "").strip()
    if not token:
        return {}, "sem Page Access Token"
    if not page_id:
        return {}, "sem page_id"
    try:
        with httpx.Client(timeout=STATUS_TIMEOUT) as client:
            resp = client.get(
                f"{_graph_base(creds)}/{page_id}",
                params=_auth_params(
                    creds, {"fields": "name,instagram_business_account{username}"}))
        if resp.status_code == 200:
            return (resp.json() or {}), ""
        return {}, _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        return {}, str(e)[:200]


def _recent_inbound_count(channel_id: str) -> int:
    """Quantos webhooks (que PASSARAM na assinatura) o core recebeu para este canal.

    Lê o buffer de debug do core (``server.routes.channel_webhook._RECENT``, últimos
    50). >0 ⇒ a Meta ESTÁ entregando e a assinatura confere → o problema (se houver)
    é a jusante. 0 ⇒ a Meta não entrega OU a assinatura está sendo rejeitada. Best-
    effort → 0 se o módulo não estiver disponível."""
    try:
        from server.routes.channel_webhook import _RECENT
        return sum(1 for p in _RECENT
                   if p.get("provider") == PROVIDER
                   and p.get("channel_id") == channel_id)
    except Exception:  # noqa: BLE001
        return 0


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
        "name": "Instagram",
        "graph_api_version": DEFAULT_GRAPH_VERSION,
        "subscribed_fields": SUBSCRIBED_FIELDS.split(","),
        "capabilities": {"qr": False, "templates": False, "groups": False,
                         "inbound_route": "path", "session_window_hours": 24},
        "credential_keys": ["page_id", "page_access_token", "app_secret",
                            "verify_token", "app_id"],
        "webhook_path_template": f"/api/webhook/{PROVIDER}/{{channel_id}}",
    }}


@router.get("/channels", dependencies=[core_permission("channel.manage")])
async def list_channels():
    channels = await asyncio.to_thread(_list_channels)
    return {"ok": True, "data": {"channels": channels}}


# ── app_id (detectado pelo dono do Page token, como no Messenger) ─────────────

def _app_id(creds: dict) -> tuple[str, str]:
    """``app_id`` da credencial ou, se vazio, detectado pelo dono do token.

    ``GET /app`` com o Page token devolve o app que o emitiu — evita pedir mais um
    campo ao operador. Retorna ``(app_id, erro)``."""
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


# ── Assinatura da PÁGINA (graph.facebook.com/{page_id}/subscribed_apps) ───────

def _post_subscribed_apps(creds: dict, page_id: str,
                          fields: str | None) -> tuple[bool, str]:
    """POST ``{page_id}/subscribed_apps`` — com ou sem ``subscribed_fields``."""
    url = f"{_graph_base(creds)}/{page_id}/subscribed_apps"
    extra = {"subscribed_fields": fields} if fields else None
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, params=_auth_params(creds, extra))
        if resp.status_code in (200, 201) and (resp.json() or {}).get("success", True):
            return True, ""
        return False, _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def _subscribe(channel_id: str) -> tuple[bool, str]:
    creds = _creds(channel_id)
    page_id = (creds.get("page_id") or "").strip()
    if not page_id or not creds.get("page_access_token"):
        return False, "canal sem page_id ou page_access_token"
    ok, err = _post_subscribed_apps(creds, page_id, PAGE_SUBSCRIBED_FIELDS)
    if not ok and _should_retry_without_fields(err):
        # Campo sem permissão OU nome inválido pra este endpoint — assina sem lista
        # de campos (a Página herda os que o app está configurado/aprovado para) pra
        # pelo menos receber as DMs. Melhor entregar mensagens do que travar tudo.
        logger.info("instagram: subscribed_apps sem campos (fallback) para %s: %s",
                    channel_id, err)
        ok, err2 = _post_subscribed_apps(creds, page_id, None)
        if ok:
            return True, ""
        err = err2 or err
    return ok, err


@router.post("/subscribe", dependencies=[core_permission("channel.manage")])
async def subscribe(body: dict):
    """Assina o app nos campos de webhook da Página (só a 2ª metade).

    Pré-requisito: a conta do Instagram conectada à Página e o acesso a mensagens
    do Instagram permitido."""
    channel_id = (body.get("channel_id") or "").strip()
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    ok, err = await asyncio.to_thread(_subscribe, channel_id)
    if ok:
        _audit("pagina.subscribe", channel_id,
               after={"fields": PAGE_SUBSCRIBED_FIELDS.split(",")})
    return {"ok": ok, "error": err or None,
            "data": {"fields": PAGE_SUBSCRIBED_FIELDS.split(",")} if ok else None}


# ── Registro do Callback no APP (graph.facebook.com/{app_id}/subscriptions) ───
# object=instagram; app access token {app_id}|{app_secret} (auto-assinado — NUNCA
# mandar appsecret_proof aqui). ⚠️ SOBRESCREVE o callback de object=instagram de
# qualquer outro sistema que use o mesmo app.

def _post_app_subscription(creds: dict, app_id: str, secret: str,
                           verify_token: str, callback_url: str,
                           fields: str) -> tuple[bool, str]:
    """POST ``{app_id}/subscriptions`` (object=instagram) com um set de campos."""
    url = f"{_graph_base(creds)}/{app_id}/subscriptions"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, params={
                "object": "instagram",
                "callback_url": callback_url,
                "verify_token": verify_token,
                "fields": fields,
                "include_values": "true",
                "access_token": f"{app_id}|{secret}",
            })
        if resp.status_code in (200, 201) and (resp.json() or {}).get("success", True):
            return True, ""
        return False, _graph_error(resp)
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def _register_app_webhook(channel_id: str, callback_url: str) -> tuple[bool, str]:
    creds = _creds(channel_id)
    secret = creds.get("app_secret") or ""
    verify_token = creds.get("verify_token") or ""
    if not secret:
        return False, ("canal sem App Secret — é ele que forma o app access token "
                       "necessário para registrar o callback")
    if not verify_token:
        return False, "canal sem verify_token"
    app_id, err = _app_id(creds)
    if not app_id:
        return False, err or "app_id indisponível"
    ok, err = _post_app_subscription(creds, app_id, secret, verify_token,
                                     callback_url, APP_SUBSCRIBED_FIELDS)
    if not ok and _should_retry_without_fields(err):
        # Campo sem permissão trava o registro todo — registra só ``messages``
        # (o essencial pra receber DMs). O resto o operador amplia quando o app
        # ganhar acesso avançado.
        logger.info("instagram: subscriptions só com 'messages' (fallback de "
                    "permissão) para %s: %s", channel_id, err)
        ok, err2 = _post_app_subscription(creds, app_id, secret, verify_token,
                                          callback_url, "messages")
        if ok:
            return True, ""
        err = err2 or err
    return ok, err


def _read_configured_url(creds: dict) -> tuple[str | None, str]:
    """URL de callback que a Meta tem no webhook ``object=instagram`` do app.

    ``GET /{app_id}/subscriptions`` (app access token ``{app_id}|{app_secret}``) →
    ``callback_url`` da entrada ``object=instagram``. Retorna ``(url|None, reason)``
    (``reason`` só em erro de leitura; ``(None, "")`` = nada cadastrado = unset)."""
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
            if item.get("object") == "instagram":
                return (item.get("callback_url") or None), ""
        return None, ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:200]


# ── URL normalization + classification (espelha whatsapp_cloud/messenger) ─────

def _normalize_url(u: str) -> dict:
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
    """Chamado pelo core ao CRIAR um canal Instagram — registra tudo sozinho.

    Faz o registro do Callback URL no app (object=instagram) e assina a Página, e
    devolve o resultado no formato que o ``AutoconfigureNotice`` genérico do core
    renderiza."""
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
                        "Instagram no App Dashboard da Meta, com o Verify Token do "
                        "canal, e assine os campos "
                        f"{SUBSCRIBED_FIELDS.replace(',', ', ')}."),
        }}

    if not base or not base.startswith("https://") or not _host_is_public(host):
        return _manual("esta instância não tem uma URL pública HTTPS "
                       "(configure o acesso pelo domínio antes)")

    ok, err = await asyncio.to_thread(_register_app_webhook, channel_id, webhook_url)
    if not ok:
        logger.warning("instagram: subscriptions falhou (%s): %s", channel_id, err)
        return _manual(err or "registro do callback falhou")

    sub_ok, sub_err = await asyncio.to_thread(_subscribe, channel_id)
    if not sub_ok:
        logger.warning("instagram: subscribed_apps falhou (%s): %s", channel_id, sub_err)
    # Registro automático no create: grava o callback do APP + assina a Página —
    # e SOBRESCREVE a callback_url de qualquer outro sistema no mesmo app.
    _audit("webhook.autoconfigure", channel_id,
           after={"mode": "webhook", "registered": True, "url": webhook_url,
                  "page_subscribed": sub_ok})
    return {"ok": True, "data": {
        "mode": "webhook", "registered": True, "webhook_url": webhook_url,
        "page_subscribed": sub_ok, "reason": "" if sub_ok else sub_err,
        "fields": SUBSCRIBED_FIELDS.split(","),
        "message": ("A Meta já está entregando as DMs da conta neste endereço. "
                    "Atenção: o callback do app (object=instagram) foi SOBRESCRITO "
                    "— outro sistema que usasse o mesmo app parou de receber."
                    + ("" if sub_ok else f" A assinatura da Página falhou: {sub_err} "
                       "(confira se a conta do Instagram está conectada à Página e "
                       "o acesso a mensagens está permitido)")),
    }}


def _webhook_status(channel_id: str, expected_url: str = "") -> dict:
    """Saúde do webhook do canal. Junta DUAS checagens:

    1. **Página assinada** nos campos (``{page_id}/subscribed_apps``, só o Page
       token) → ``subscribed`` / ``subscribed_fields``.
    2. **Callback do app** vs a URL que ESTA instância espera → ``configured_url`` +
       ``match`` (``ok``/``wrong_domain``/``wrong_path``/``unset``/``unknown``).
       ``can_set`` diz se dá pra repointar com 1 clique (precisa de app_secret +
       verify_token; o app_id é auto-detectado)."""
    creds = _creds(channel_id)
    expected = (expected_url or "").strip() or _expected_webhook_url(channel_id)
    page_id = (creds.get("page_id") or "").strip()
    can_set = bool(creds.get("app_secret") and creds.get("verify_token"))
    result = {"expected_url": expected, "subscribed_fields": [],
              "subscribed": False, "reason": "",
              "has_app_secret": bool(creds.get("app_secret")),
              "configured_url": None, "match": "unknown", "can_set": can_set,
              "page_id": page_id}
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
                result["reason"] = ("a Página não está assinada no campo 'messages' "
                                    "(confira se a conta do Instagram está conectada "
                                    "à Página e o acesso a mensagens permitido)")
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
    """Aponta o callback do webhook (nível do app, ``object=instagram``) para
    ``url`` e reassina a Página. Mesma mecânica do ``/autoconfigure``, com a URL
    vinda do frontend — é o botão "Configurar webhook" do card. Em sucesso, re-lê
    e devolve o novo ``match``."""
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


def _diagnose(channel_id: str) -> dict:
    """Checa os 3 portões do recebimento de DM, em ordem, e diz onde trava.

    1. **Page token válido** — ``/{page_id}`` responde (e mostra a conta IG conectada)?
    2. **Página assinada** — ``subscribed_apps`` tem o campo ``messages``?
    3. **Callback apontando pra cá** — o webhook object=instagram do app é a URL
       desta instância?
    E ``recent_inbound`` = quantos webhooks (já com assinatura válida) chegaram.

    Deriva um ``verdict`` acionável em PT-BR — o que fazer a seguir."""
    creds = _creds(channel_id)
    me, me_err = _me(creds)
    token_ok = bool(me.get("id") or me.get("name") or me.get("instagram_business_account"))
    iba = me.get("instagram_business_account") or {}
    status = _webhook_status(channel_id, "")
    recent = _recent_inbound_count(channel_id)

    checks = {
        "token_ok": token_ok,
        "username": iba.get("username") or me.get("name") or "",
        "token_error": "" if token_ok else me_err,
        "page_id": status.get("page_id") or "",
        "account_subscribed": bool(status.get("subscribed")),
        "subscribed_fields": status.get("subscribed_fields") or [],
        "callback_match": status.get("match"),
        "configured_url": status.get("configured_url"),
        "expected_url": status.get("expected_url"),
        "has_app_secret": bool(creds.get("app_secret")),
        "recent_inbound": recent,
        "reason": status.get("reason") or "",
    }

    # Veredito priorizado: o 1º portão fechado é o que importa.
    if not creds.get("app_secret"):
        verdict = ("⚠️ Sem App Secret: os webhooks recebidos NÃO são validados e a "
                   "Meta pode recusar a assinatura. Preencha o App Secret no canal.")
    elif not token_ok:
        verdict = (f"❌ Page token inválido/revogado ({me_err}). Cole um novo Page "
                   "Access Token da Página conectada ao Instagram.")
    elif checks["callback_match"] != "ok":
        verdict = ("❌ O callback do webhook do app NÃO aponta pra esta instância "
                   f"(estado: {checks['callback_match']}). Clique em 'Configurar "
                   "webhook' no card do canal.")
    elif not checks["account_subscribed"]:
        verdict = ("❌ A Página não está assinada no campo 'messages'. Clique em "
                   "'Registrar webhook na Meta' e confira: a conta do Instagram "
                   "conectada à Página (Página → Configurações → Contas vinculadas) "
                   "e o acesso a mensagens do Instagram permitido.")
    elif recent == 0:
        verdict = ("✅ Configuração OK, mas NENHUM webhook chegou ainda. Causa mais "
                   "provável: o app Meta está em modo DESENVOLVIMENTO — nesse modo a "
                   "Meta só entrega DMs de contas com papel no app (admin/dev/"
                   "testador). Publique o app (Live) com 'instagram_business_manage_"
                   "messages' aprovado, ou mande a DM de teste a partir de uma conta "
                   "que seja testadora do app.")
    else:
        verdict = (f"✅ Tudo certo e {recent} webhook(s) já foram recebidos por esta "
                   "instância. Se ainda falta algo no painel, o problema é a jusante "
                   "(não no recebimento).")
    checks["verdict"] = verdict
    return checks


@router.get("/diagnose", dependencies=[core_permission("channel.manage")])
async def diagnose(channel_id: str = ""):
    """Diagnóstico completo do recebimento de DMs (token, assinatura, callback,
    webhooks recebidos) com um veredito acionável."""
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    data = await asyncio.to_thread(_diagnose, channel_id)
    return {"ok": True, "data": data}
