"""Endpoints REST do plugin GOWA (mountados em /api/plugins/gowa).

Hoje expõem apenas a configuração do ALERTA DE DESCONEXÃO via Telegram — token de
bot + chat_id + URL do painel — persistida em ``config`` com prefixo
``plugin.gowa.``. Independente do canal Telegram do sistema: o alerta fala direto
com a Bot API usando este token dedicado.

O token é secreto: o GET nunca devolve o valor cru (só ``bot_token_set``); o PUT só
sobrescreve o token quando um valor não-vazio e não-mascarado é enviado.
"""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Body, Request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db.repositories import config_repo

router = APIRouter()

_CFG = "plugin.gowa."
_MASK = "••••••••"
HTTP_TIMEOUT = 20.0
_DEFAULT_TZ = "America/Sao_Paulo"
# Fusos do Brasil oferecidos como OVERRIDE manual na tela (nome IANA → rótulo).
# O fuso padrão é detectado do navegador; isto é só para quem quiser fixar outro.
_BR_TIMEZONES = {
    "America/Sao_Paulo": "Brasília (UTC-3) — maior parte do país",
    "America/Manaus": "Manaus (UTC-4) — AM, RR, RO, MT, MS",
    "America/Rio_Branco": "Rio Branco (UTC-5) — Acre e oeste do AM",
    "America/Noronha": "Fernando de Noronha (UTC-2)",
}


def _get(key: str, default=None):
    return config_repo.get(_CFG + key, default)


def _valid_tz(name: str) -> bool:
    """True se ``name`` é um fuso IANA válido (ex.: America/Sao_Paulo)."""
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def _origin_from_request(request: Request) -> str:
    """URL base que o navegador está usando para acessar o painel AGORA.

    Honra os headers de proxy reverso (Coolify/Nginx) para pegar o domínio público
    real; em acesso direto (LAN/localhost) cai no Host + scheme da requisição."""
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or request.url.netloc or "").split(",")[0].strip()
    return f"{proto}://{host}" if host else ""


@router.get("/alert-settings")
async def get_alert_settings(request: Request, tz: str = ""):
    """Configuração atual do alerta + auto-detecta URL do painel e fuso do navegador.

    Toda vez que a tela de config carrega, capturamos (a) a URL que o navegador está
    usando (headers) e (b) o fuso horário do navegador (query ``tz`` enviada pela
    tela). Ambos são persistidos como valores ``_auto`` — assim o loop de alerta
    (que roda em background, sem requisição) sempre tem a URL e o fuso corretos sem
    o usuário precisar preencher nada. O token continua mascarado."""
    detected_url = _origin_from_request(request)
    detected_tz = tz.strip() if _valid_tz(tz.strip()) else ""

    def _load():
        if detected_url:
            config_repo.set(_CFG + "disconnect_alert_panel_url_auto", detected_url)
        if detected_tz:
            config_repo.set(_CFG + "disconnect_alert_timezone_auto", detected_tz)
        token = (_get("disconnect_alert_bot_token", "") or "").strip()
        try:
            interval = int(_get("disconnect_alert_interval_min", 15) or 15)
        except (TypeError, ValueError):
            interval = 15
        manual = str(_get("disconnect_alert_panel_url", "") or "")
        auto = str(_get("disconnect_alert_panel_url_auto", "") or "") or detected_url
        tz_manual = str(_get("disconnect_alert_timezone", "") or "")
        tz_auto = str(_get("disconnect_alert_timezone_auto", "") or "") or detected_tz or _DEFAULT_TZ
        return {
            "enabled": bool(_get("disconnect_alert_enabled", False)),
            "bot_token_set": bool(token),
            "chat_id": str(_get("disconnect_alert_chat_id", "") or ""),
            "panel_url": manual,                 # override manual (opcional)
            "panel_url_auto": auto,              # URL detectada automaticamente
            "panel_url_effective": manual or auto,  # a que o alerta vai usar
            "interval_min": interval,
            "timezone": tz_manual,               # override manual do fuso (vazio = automático)
            "timezone_auto": tz_auto,            # fuso detectado do navegador
            "timezone_effective": tz_manual or tz_auto,  # fuso que o alerta vai usar
            "timezones": _BR_TIMEZONES,          # opções do override manual
        }
    data = await asyncio.to_thread(_load)
    return {"ok": True, "data": data}


@router.put("/alert-settings")
async def put_alert_settings(payload: dict = Body(...)):
    """Salva a configuração do alerta. Campos ausentes não são tocados."""
    def _save():
        updates: dict = {}
        if "enabled" in payload:
            updates[_CFG + "disconnect_alert_enabled"] = bool(payload["enabled"])
        if "chat_id" in payload:
            updates[_CFG + "disconnect_alert_chat_id"] = str(payload["chat_id"] or "").strip()
        if "panel_url" in payload:
            updates[_CFG + "disconnect_alert_panel_url"] = str(payload["panel_url"] or "").strip().rstrip("/")
        if "interval_min" in payload:
            try:
                updates[_CFG + "disconnect_alert_interval_min"] = max(1, int(payload["interval_min"]))
            except (TypeError, ValueError):
                pass
        if "timezone" in payload:
            tz = str(payload["timezone"] or "").strip()
            # Vazio = usar o fuso automático (detectado do navegador). Se preenchido,
            # precisa ser um fuso IANA válido; caso contrário cai para vazio (auto).
            updates[_CFG + "disconnect_alert_timezone"] = tz if _valid_tz(tz) else ""
        # Token só é gravado quando vem um valor real (não vazio e não a máscara).
        token = payload.get("bot_token")
        if token is not None:
            token = str(token).strip()
            if token and token != _MASK:
                updates[_CFG + "disconnect_alert_bot_token"] = token
        if updates:
            config_repo.set_many(updates)
    await asyncio.to_thread(_save)
    return {"ok": True}


@router.post("/alert-test")
async def alert_test(payload: dict = Body(default={})):
    """Envia uma mensagem de teste ao Telegram com o token/chat_id salvos (ou os
    enviados no corpo, ainda não salvos) para o usuário validar a configuração."""
    def _resolve():
        token = str(payload.get("bot_token") or "").strip()
        if not token or token == _MASK:
            token = (_get("disconnect_alert_bot_token", "") or "").strip()
        chat_id = str(payload.get("chat_id") or "").strip() or str(_get("disconnect_alert_chat_id", "") or "").strip()
        return token, chat_id
    token, chat_id = await asyncio.to_thread(_resolve)
    if not token or not chat_id:
        return {"ok": False, "error": "Informe o token do bot e o chat_id."}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": "✅ WhatsBot: alerta de desconexão configurado com sucesso.",
            })
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Falha ao contatar o Telegram: {e}"}
    if not data.get("ok"):
        return {"ok": False, "error": data.get("description") or "Erro do Telegram."}
    return {"ok": True}
