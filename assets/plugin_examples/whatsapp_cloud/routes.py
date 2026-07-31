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
import time
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import httpx
from fastapi import APIRouter, Body, Request

from db.repositories import channel_credential_repo, config_repo
from plugins.context import core_permission, make_plugin_db, plugin_permission

# Default Graph API version. Kept in sync with settings.Settings.graph_api_version
# (the real configured value is read from plugin settings by the core). Avoids a
# cross-module import, which is brittle under the plugin loader's file-based
# import (submodules are not on sys.path).
DEFAULT_GRAPH_API_VERSION = "v21.0"

GRAPH_BASE = "https://graph.facebook.com"
HTTP_TIMEOUT = 20.0

router = APIRouter()

PLUGIN_ID = "whatsapp_cloud"

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


def _audit_plugin(action: str, **kw) -> None:
    """Auditoria de config GLOBAL do plugin (não é por canal → ``plugin:<id>``).

    O alerta da conta Meta é uma configuração da instalação inteira (token do bot,
    grupo de destino, o que alertar), então cai no recurso do plugin — ao contrário
    das ações de webhook/template, que são sobre UM canal.
    """
    if _core_audit is None:
        return
    try:
        _core_audit(PLUGIN_ID, action, **kw)
    except Exception:  # noqa: BLE001
        pass


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

@router.get("/info", dependencies=[core_permission("channel.manage")])
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
                "app_id",
                "app_secret",
            ],
            "webhook_path_template": "/api/webhook/whatsapp_cloud/{channel_id}",
        },
    }


@router.get("/webhook-status", dependencies=[core_permission("channel.manage")])
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


@router.post("/set-webhook", dependencies=[core_permission("channel.manage")])
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
    # Override de callback NA WABA: aponta para onde a Meta entrega as mensagens
    # deste número. Errado, a caixa emudece — por isso fica na trilha.
    _audit("webhook.set", channel_id,
           after={"url": url, "waba_id": waba_id,
                  "match": _classify(configured_url, url)})
    return {"ok": True, "error": None, "data": {
        "match": _classify(configured_url, url), "configured_url": configured_url}}


@router.post("/delete-webhook", dependencies=[core_permission("channel.manage")])
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
    _audit("webhook.delete", channel_id,
           after={"waba_id": waba_id, "override_removido": True})
    return {"ok": True, "error": None}


# ── Preferências de template: favoritos (pessoais) e arquivados (globais) ────
#
# Plano 92 · D1. A LISTA de templates continua vindo do core (Graph API); aqui
# mora só a marcação local, que o modal funde no cliente. Duas semânticas:
#
#   favoritos  → PESSOAIS. Chave (user_id, channel_id, template_name). Sem
#                usuário logado (instalação aberta) não há a quem pertencer:
#                a leitura devolve vazio e a escrita responde 400, e a tela
#                simplesmente não mostra a estrela.
#   arquivados → GLOBAIS. Um atendente marca, some para todos. Por isso exige a
#                permissão `plugin.whatsapp_cloud.template_archive`, que nasce
#                sem dono (o administrador concede em Usuários → Cargos).
#
# Nada aqui chama a Graph API — é só banco, então não esbarra na restrição de
# import do loader descrita no topo do arquivo.

def _now() -> float:
    return time.time()


def _user_of(request) -> tuple:
    """``(id, nome)`` do usuário da request. ``(None, "")`` em instalação aberta."""
    user = getattr(request.state, "user", None) or {}
    uid = user.get("id")
    name = (user.get("name") or user.get("email") or "").strip()
    return (uid if isinstance(uid, int) else None, name)


async def _can_archive(request) -> bool:
    """A permissão de arquivar, como FLAG (sem levantar 403).

    ``plugin_permission`` é a dependency que barra a rota; aqui precisamos do
    booleano para a tela esconder o botão — o padrão do repo é "esconder, não
    desabilitar". Import defensivo: num core sem o helper, devolve True e o
    enforcement continua sendo o da rota.
    """
    try:
        from server.authz import acheck
    except ImportError:  # pragma: no cover — core antigo
        return True
    try:
        return await acheck(request, f"plugin.{PLUGIN_ID}.template_archive")
    except Exception:  # noqa: BLE001
        return True


def _read_prefs(channel_id: str, user_id) -> dict:
    from sqlalchemy import text as _sql
    favorites: list = []
    archived: list = []
    with make_plugin_db() as conn:
        if user_id is not None:
            favorites = [r[0] for r in conn.execute(_sql(
                "SELECT template_name FROM plugin_whatsapp_cloud_template_favorites "
                "WHERE user_id = :u AND channel_id = :c"),
                {"u": user_id, "c": channel_id}).fetchall()]
        archived = [r[0] for r in conn.execute(_sql(
            "SELECT template_name FROM plugin_whatsapp_cloud_template_archived "
            "WHERE channel_id = :c"), {"c": channel_id}).fetchall()]
    return {"favorites": favorites, "archived": archived}


@router.get("/template-prefs")
async def template_prefs(request: Request, channel_id: str = ""):
    """Favoritos do usuário + arquivados do canal. Leitura, sem gate próprio."""
    channel_id = (channel_id or "").strip()
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    user_id, _ = _user_of(request)
    data = await asyncio.to_thread(_read_prefs, channel_id, user_id)
    data["can_archive"] = await _can_archive(request)
    data["can_favorite"] = user_id is not None
    return {"ok": True, "data": data, "error": None}


def _set_favorite(channel_id: str, user_id: int, name: str, on: bool) -> None:
    from sqlalchemy import text as _sql
    with make_plugin_db() as conn:
        if on:
            # Idempotente: o índice único é a autoridade, o SELECT evita o erro.
            exists = conn.execute(_sql(
                "SELECT 1 FROM plugin_whatsapp_cloud_template_favorites "
                "WHERE user_id = :u AND channel_id = :c AND template_name = :n"),
                {"u": user_id, "c": channel_id, "n": name}).first()
            if not exists:
                conn.execute(_sql(
                    "INSERT INTO plugin_whatsapp_cloud_template_favorites "
                    "(user_id, channel_id, template_name, created_at) "
                    "VALUES (:u, :c, :n, :t)"),
                    {"u": user_id, "c": channel_id, "n": name, "t": _now()})
        else:
            conn.execute(_sql(
                "DELETE FROM plugin_whatsapp_cloud_template_favorites "
                "WHERE user_id = :u AND channel_id = :c AND template_name = :n"),
                {"u": user_id, "c": channel_id, "n": name})


@router.post("/template-prefs/favorite")
async def set_template_favorite(body: dict, request: Request):
    """Liga/desliga o favorito do USUÁRIO da request. Preferência pessoal ⇒ sem
    permissão e **sem auditoria** (o guia manda não auditar preferência por
    usuário)."""
    channel_id = (body.get("channel_id") or "").strip()
    name = (body.get("template_name") or body.get("name") or "").strip()
    if not channel_id or not name:
        return {"ok": False, "error": "channel_id e template_name são obrigatórios"}
    user_id, _ = _user_of(request)
    if user_id is None:
        return {"ok": False, "error": "Favoritos exigem um usuário logado."}
    on = bool(body.get("favorite", True))
    await asyncio.to_thread(_set_favorite, channel_id, user_id, name, on)
    return {"ok": True, "data": {"template_name": name, "favorite": on}, "error": None}


def _set_archived(channel_id: str, name: str, on: bool, user_id, user_name: str) -> None:
    from sqlalchemy import text as _sql
    with make_plugin_db() as conn:
        if on:
            exists = conn.execute(_sql(
                "SELECT 1 FROM plugin_whatsapp_cloud_template_archived "
                "WHERE channel_id = :c AND template_name = :n"),
                {"c": channel_id, "n": name}).first()
            if not exists:
                conn.execute(_sql(
                    "INSERT INTO plugin_whatsapp_cloud_template_archived "
                    "(channel_id, template_name, archived_by, archived_by_name, archived_at) "
                    "VALUES (:c, :n, :u, :un, :t)"),
                    {"c": channel_id, "n": name, "u": user_id,
                     "un": user_name, "t": _now()})
        else:
            conn.execute(_sql(
                "DELETE FROM plugin_whatsapp_cloud_template_archived "
                "WHERE channel_id = :c AND template_name = :n"),
                {"c": channel_id, "n": name})


@router.post("/template-prefs/archive",
             dependencies=[plugin_permission("template_archive")])
async def set_template_archived(body: dict, request: Request):
    """Arquiva/desarquiva um template para TODO MUNDO naquele canal.

    Não apaga nada na Meta — quem apaga de verdade é a lixeira do modal, gated
    em `template.delete` no core. Auditado como ação de CANAL (o filtro por canal
    devolve a história inteira dele, junto com os eventos `channel.*` do core).
    """
    channel_id = (body.get("channel_id") or "").strip()
    name = (body.get("template_name") or body.get("name") or "").strip()
    if not channel_id or not name:
        return {"ok": False, "error": "channel_id e template_name são obrigatórios"}
    on = bool(body.get("archived", True))
    user_id, user_name = _user_of(request)
    await asyncio.to_thread(_set_archived, channel_id, name, on, user_id, user_name)
    _audit("template.archive" if on else "template.unarchive", channel_id,
           after={"template": name, "arquivado": on})
    return {"ok": True, "data": {"template_name": name, "archived": on}, "error": None}


# NOTE: template listing/sending/creation is NOT here. It lives in the CORE,
# channel-aware and capability-gated, under
# ``/api/conversations/{conv_id}/templates`` (GET list, POST send-template, POST
# create, DELETE), backed by ``OutboundRouter`` → ``WhatsAppCloudChannel`` Graph
# calls. The old plugin ``GET /templates`` stub was removed to avoid confusion —
# it always returned ``[]`` and nothing consumed it.


# ── Alertas da conta Meta via Telegram (plano 84) ────────────────────────────
# A configuração inteira do alerta mora AQUI, na aba "Configurar" deste plugin
# (regra do CLAUDE.md: opção de plugin nunca vira aba no painel de Configurações
# do core). Espelha o contrato já em produção no plugin ``gowa``: o token é
# secreto, então o GET devolve só ``bot_token_set`` e o PUT só grava quando vem
# um valor real — recarregar a tela nunca vaza o segredo e salvar sem digitar
# não apaga o token guardado.

_ALERT_MASK = "••••••••"
_DEFAULT_TZ = "America/Sao_Paulo"

# Lista completa de fusos IANA (zoneinfo — offline, autoritativa), cacheada por
# processo; o rótulo traz o offset atual para o usuário se localizar.
_TZ_CACHE: list[dict] | None = None


def _alert_cfg(key: str, default=None):
    return config_repo.get(f"plugin.{PLUGIN_ID}.{key}", default)


def _valid_tz(name: str) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def _all_timezones() -> list[dict]:
    global _TZ_CACHE
    if _TZ_CACHE is not None:
        return _TZ_CACHE
    now = datetime.now()
    items: list[tuple[int, str, dict]] = []
    for name in available_timezones():
        try:
            off = now.astimezone(ZoneInfo(name)).utcoffset()
        except Exception:  # noqa: BLE001
            continue
        mins = int(off.total_seconds() // 60) if off else 0
        sign = "+" if mins >= 0 else "-"
        hh, mm = divmod(abs(mins), 60)
        items.append((mins, name, {
            "value": name, "label": f"(UTC{sign}{hh:02d}:{mm:02d}) {name.replace('_', ' ')}"}))
    items.sort(key=lambda t: (t[0], t[1]))
    _TZ_CACHE = [it[2] for it in items]
    return _TZ_CACHE


def _alert_groups_view() -> list[dict]:
    """Catálogo de grupos de alerta + o estado efetivo de cada um.

    A fonte do catálogo é ``alerts.ALERT_GROUPS`` (um lugar só): a tela renderiza
    o que o motor conhece, então acrescentar um grupo novo não exige mexer no JS.
    """
    from . import alerts
    saved = alerts._parse_groups(_alert_cfg("alert_groups", None))
    return [{"key": key, "label": meta["label"],
             "enabled": alerts.group_enabled(saved, key),
             "default": bool(meta["default"])}
            for key, meta in alerts.ALERT_GROUPS.items()]


@router.get("/alert-settings", dependencies=[core_permission("channel.manage")])
async def get_alert_settings(tz: str = ""):
    """Configuração atual do alerta (token MASCARADO) + catálogo de grupos.

    O fuso do navegador (query ``tz``) é persistido para o alerta exibir a hora
    certa sem o usuário precisar escolher (o servidor roda em UTC)."""
    detected_tz = tz.strip() if _valid_tz(tz.strip()) else ""

    def _load():
        from . import alerts
        if detected_tz:
            config_repo.set(f"plugin.{PLUGIN_ID}.alert_timezone_auto", detected_tz)
        token = (_alert_cfg("alert_bot_token", "") or "").strip()
        cfg = alerts.alert_config()
        tz_manual = str(_alert_cfg("alert_timezone", "") or "")
        tz_auto = (str(_alert_cfg("alert_timezone_auto", "") or "")
                   or detected_tz or _DEFAULT_TZ)
        return {
            "enabled": cfg["enabled"],
            "bot_token_set": bool(token),
            # Últimos 4 dígitos: confere QUAL bot está salvo sem revelar o token.
            "bot_token_hint": (token[-4:] if len(token) > 4 else ""),
            "chat_id": cfg["chat_id"],
            "interval_min": cfg["interval_min"],
            "quality_poll_min": cfg["quality_poll_min"],
            "timezone": tz_manual,
            "timezone_auto": tz_auto,
            "timezone_effective": tz_manual or tz_auto,
            "timezones": _all_timezones(),
            "groups": _alert_groups_view(),
        }
    return {"ok": True, "data": await asyncio.to_thread(_load)}


def _alert_audit_view() -> dict:
    """Config do alerta SEM o token em claro — só se ele está definido."""
    return {
        "enabled": bool(_alert_cfg("alert_enabled", False)),
        "chat_id": str(_alert_cfg("alert_chat_id", "") or ""),
        "interval_min": _alert_cfg("alert_interval_min", None),
        "quality_poll_min": _alert_cfg("alert_quality_poll_min", None),
        "timezone": str(_alert_cfg("alert_timezone", "") or ""),
        "grupos": _alert_cfg("alert_groups", None),
        "bot_token_definido": bool(_alert_cfg("alert_bot_token", "")),
    }


@router.put("/alert-settings", dependencies=[core_permission("channel.manage")])
async def put_alert_settings(payload: dict = Body(...)):
    """Salva a configuração do alerta. Campo ausente não é tocado."""
    before = await asyncio.to_thread(_alert_audit_view)

    def _save():
        from . import alerts
        prefix = f"plugin.{PLUGIN_ID}."
        updates: dict = {}
        if "enabled" in payload:
            updates[prefix + "alert_enabled"] = bool(payload["enabled"])
        if "chat_id" in payload:
            updates[prefix + "alert_chat_id"] = str(payload["chat_id"] or "").strip()
        if "interval_min" in payload:
            try:
                updates[prefix + "alert_interval_min"] = max(1, int(payload["interval_min"]))
            except (TypeError, ValueError):
                pass
        if "quality_poll_min" in payload:
            try:
                updates[prefix + "alert_quality_poll_min"] = max(
                    alerts.MIN_QUALITY_POLL_MIN, int(payload["quality_poll_min"]))
            except (TypeError, ValueError):
                pass
        if "timezone" in payload:
            tz = str(payload["timezone"] or "").strip()
            updates[prefix + "alert_timezone"] = tz if _valid_tz(tz) else _DEFAULT_TZ
        if isinstance(payload.get("groups"), dict):
            # Só chaves conhecidas: um POST torto não polui a config nem esconde
            # um grupo de alerta que o motor conhece.
            merged = alerts._parse_groups(_alert_cfg("alert_groups", None))
            merged.update({k: bool(v) for k, v in payload["groups"].items()
                           if k in alerts.ALERT_GROUPS})
            updates[prefix + "alert_groups"] = merged
        # Token só é gravado quando vem um valor real (não vazio, não a máscara).
        token = payload.get("bot_token")
        if token is not None:
            token = str(token).strip()
            if token and token != _ALERT_MASK:
                updates[prefix + "alert_bot_token"] = token
        if updates:
            config_repo.set_many(updates)
    await asyncio.to_thread(_save)
    _audit_plugin("alerta.config", before=before,
                  after=await asyncio.to_thread(_alert_audit_view))
    return {"ok": True}


@router.post("/alert-test", dependencies=[core_permission("channel.manage")])
async def alert_test(payload: dict = Body(default={})):
    """Manda uma mensagem de teste ao Telegram com o token/chat_id salvos (ou os
    enviados no corpo, ainda não salvos) para o operador validar a configuração."""
    def _resolve():
        token = str(payload.get("bot_token") or "").strip()
        if not token or token == _ALERT_MASK:
            token = (_alert_cfg("alert_bot_token", "") or "").strip()
        chat_id = (str(payload.get("chat_id") or "").strip()
                   or str(_alert_cfg("alert_chat_id", "") or "").strip())
        return token, chat_id
    token, chat_id = await asyncio.to_thread(_resolve)
    if not token or not chat_id:
        return {"ok": False, "error": "Informe o token do bot e o chat_id."}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": chat_id,
            "text": "✅ WhatsBot: alertas da conta Meta configurados com sucesso."}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=body)
            data = resp.json()
            # Grupo promovido a supergrupo: persiste o novo id e reenvia uma vez.
            new_id = ((data.get("parameters") or {}).get("migrate_to_chat_id")
                      if not data.get("ok") else None)
            if new_id:
                new_id = str(new_id)
                await asyncio.to_thread(
                    config_repo.set, f"plugin.{PLUGIN_ID}.alert_chat_id", new_id)
                resp = await client.post(url, json={**body, "chat_id": new_id})
                data = resp.json()
    except Exception:  # noqa: BLE001
        # O texto de uma exceção httpx pode carregar a URL ``/bot{token}/``.
        # Nunca reflita esse detalhe na resposta da API.
        return {"ok": False, "error": "Falha ao contatar o Telegram."}
    if not data.get("ok"):
        return {"ok": False, "error": data.get("description") or "Erro do Telegram."}
    return {"ok": True}
