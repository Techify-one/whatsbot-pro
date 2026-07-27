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
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from db.repositories import config_repo
from plugins.context import core_permission

router = APIRouter()

_CFG = "plugin.gowa."
_MASK = "••••••••"
HTTP_TIMEOUT = 20.0
_DEFAULT_TZ = "America/Sao_Paulo"

# Lista COMPLETA de fusos do mundo — a base IANA embutida no Python (zoneinfo),
# a fonte autoritativa e offline (sem API externa). Cacheada por processo; o rótulo
# traz o offset atual (UTC±HH:MM) para o usuário se localizar.
_TZ_CACHE: list[dict] | None = None


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


def _all_timezones() -> list[dict]:
    """Todos os fusos IANA, ordenados por offset e nome: [{value, label}]."""
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
        label = f"(UTC{sign}{hh:02d}:{mm:02d}) {name.replace('_', ' ')}"
        items.append((mins, name, {"value": name, "label": label}))
    items.sort(key=lambda t: (t[0], t[1]))
    _TZ_CACHE = [it[2] for it in items]
    return _TZ_CACHE


@router.get("/alert-settings", dependencies=[core_permission("channel.manage")])
async def get_alert_settings(request: Request, tz: str = ""):
    """Configuração atual do alerta + auto-detecta o fuso do navegador.

    A URL do painel NÃO é capturada aqui: ela é a variável global do core
    ``public_base_url`` (capturada no 1º acesso ao painel), lida direto pelo loop de
    alerta. O fuso do navegador (query ``tz``) é persistido para o loop usar sem o
    usuário precisar escolher. O token continua mascarado."""
    detected_tz = tz.strip() if _valid_tz(tz.strip()) else ""

    def _load():
        if detected_tz:
            config_repo.set(_CFG + "disconnect_alert_timezone_auto", detected_tz)
        token = (_get("disconnect_alert_bot_token", "") or "").strip()
        try:
            interval = int(_get("disconnect_alert_interval_min", 15) or 15)
        except (TypeError, ValueError):
            interval = 15
        base = str(config_repo.get("public_base_url", "") or "").rstrip("/")
        tz_manual = str(_get("disconnect_alert_timezone", "") or "")
        tz_auto = str(_get("disconnect_alert_timezone_auto", "") or "") or detected_tz or _DEFAULT_TZ
        return {
            "enabled": bool(_get("disconnect_alert_enabled", False)),
            "bot_token_set": bool(token),
            "chat_id": str(_get("disconnect_alert_chat_id", "") or ""),
            "panel_url_effective": base,         # variável global do core (só leitura)
            "interval_min": interval,
            "timezone": tz_manual,               # override manual do fuso (vazio = automático)
            "timezone_auto": tz_auto,            # fuso detectado do navegador
            "timezone_effective": tz_manual or tz_auto,  # fuso que o alerta vai usar
            "timezones": _all_timezones(),       # lista completa (IANA) para o seletor
        }
    data = await asyncio.to_thread(_load)
    return {"ok": True, "data": data}


@router.put("/alert-settings", dependencies=[core_permission("channel.manage")])
async def put_alert_settings(payload: dict = Body(...)):
    """Salva a configuração do alerta. Campos ausentes não são tocados."""
    def _save():
        updates: dict = {}
        if "enabled" in payload:
            updates[_CFG + "disconnect_alert_enabled"] = bool(payload["enabled"])
        if "chat_id" in payload:
            updates[_CFG + "disconnect_alert_chat_id"] = str(payload["chat_id"] or "").strip()
        if "interval_min" in payload:
            try:
                updates[_CFG + "disconnect_alert_interval_min"] = max(1, int(payload["interval_min"]))
            except (TypeError, ValueError):
                pass
        if "timezone" in payload:
            tz = str(payload["timezone"] or "").strip()
            # Fuso é sempre um valor fixo (não há mais modo automático); valor
            # inválido/vazio cai no default Brasília.
            updates[_CFG + "disconnect_alert_timezone"] = tz if _valid_tz(tz) else _DEFAULT_TZ
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


@router.post("/alert-test", dependencies=[core_permission("channel.manage")])
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
    body = {
        "chat_id": chat_id,
        "text": "✅ WhatsBot: alerta de desconexão configurado com sucesso.",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=body)
            data = resp.json()
            # Grupo virou supergrupo: o chat_id mudou. Persiste o novo id e reenvia
            # uma vez — mesma cortesia da interceptação central do loop (alerts.py).
            new_id = data.get("parameters", {}).get("migrate_to_chat_id") if not data.get("ok") else None
            if new_id:
                new_id = str(new_id)
                await asyncio.to_thread(config_repo.set, _CFG + "disconnect_alert_chat_id", new_id)
                resp = await client.post(url, json={**body, "chat_id": new_id})
                data = resp.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Falha ao contatar o Telegram: {e}"}
    if not data.get("ok"):
        return {"ok": False, "error": data.get("description") or "Erro do Telegram."}
    return {"ok": True}
