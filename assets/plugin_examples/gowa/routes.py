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

PLUGIN_ID = "gowa"

# ── Trilha de auditoria (docs/PLUGINS_AUDITAVEIS.md) ──────────────────────────
# Import defensivo: o plugin é bundled/importável por .zip e pode cair num core
# anterior ao seam — sem o helper ele continua funcionando, só não registra.
try:
    from plugins.context import audit as _core_audit
except ImportError:  # pragma: no cover — core antigo
    _core_audit = None


def _audit(action: str, **kw) -> None:
    """Registra uma ação deste plugin na Auditoria. Nunca quebra a rota."""
    if _core_audit is None:
        return
    try:
        _core_audit(PLUGIN_ID, action, **kw)
    except Exception:  # noqa: BLE001 — auditoria nunca derruba a ação auditada
        pass


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
    """Configuração atual do alerta + o fuso detectado do navegador (SÓ LEITURA).

    A URL do painel NÃO é capturada aqui: ela é a variável global do core
    ``public_base_url`` (capturada no 1º acesso ao painel), lida direto pelo loop de
    alerta. O token continua mascarado.

    ⚠️ ESTE GET NÃO ESCREVE NADA (plano 148 §4.10). O fuso do navegador (query
    ``tz``) era persistido aqui: só de ABRIR a aba, o horário exibido em todo
    alerta mudava — sem o operador salvar nada e sem dono na trilha. Auditar o GET
    seria pior (viraria log de navegação), então a escrita saiu. O fuso detectado
    continua voltando em ``timezone_auto``, a tela pré-preenche o seletor com ele e
    o valor entra em ``disconnect_alert_timezone`` pelo PUT — que JÁ é auditado.
    """
    detected_tz = tz.strip() if _valid_tz(tz.strip()) else ""

    def _load():
        token = (_get("disconnect_alert_bot_token", "") or "").strip()
        try:
            interval = int(_get("disconnect_alert_interval_min", 15) or 15)
        except (TypeError, ValueError):
            interval = 15
        base = str(config_repo.get("public_base_url", "") or "").rstrip("/")
        tz_manual = str(_get("disconnect_alert_timezone", "") or "")
        # ⚠️ SUGESTÃO ≠ EFETIVO, e a diferença nasceu quando este GET parou de
        # escrever. ``tz_auto`` é o que a TELA usa para pré-selecionar o seletor,
        # e por isso ainda cai no fuso do navegador; o fuso que o ALERTA usa é só
        # o que está SALVO. Somar o detectado no "efetivo" faria a resposta jurar
        # America/Manaus enquanto o alerta imprime America/Sao_Paulo.
        tz_auto_salvo = str(_get("disconnect_alert_timezone_auto", "") or "")
        tz_auto = tz_auto_salvo or detected_tz or _DEFAULT_TZ
        return {
            "enabled": bool(_get("disconnect_alert_enabled", False)),
            "bot_token_set": bool(token),
            "chat_id": str(_get("disconnect_alert_chat_id", "") or ""),
            "panel_url_effective": base,         # variável global do core (só leitura)
            "interval_min": interval,
            "timezone": tz_manual,               # override manual do fuso (vazio = automático)
            "timezone_auto": tz_auto,            # SUGESTÃO para o seletor da tela
            # Espelha, campo a campo, ``alerts._resolve_tz_name()`` — o que o loop
            # de alerta realmente imprime. Nada de detected_tz aqui.
            "timezone_effective": tz_manual or tz_auto_salvo or _DEFAULT_TZ,
            "timezones": _all_timezones(),       # lista completa (IANA) para o seletor
        }
    data = await asyncio.to_thread(_load)
    return {"ok": True, "data": data}


def _alert_audit_view() -> dict:
    """Config do alerta SEM o token em claro — só se ele está definido."""
    return {
        "enabled": bool(_get("disconnect_alert_enabled", False)),
        "chat_id": str(_get("disconnect_alert_chat_id", "") or ""),
        "interval_min": _get("disconnect_alert_interval_min", None),
        "timezone": str(_get("disconnect_alert_timezone", "") or ""),
        # O fuso EFETIVO é o manual acima ou, vazio ele, este automático. Sem a
        # segunda chave o diff do PUT esconde qual horário o alerta passa a usar.
        "timezone_auto": str(_get("disconnect_alert_timezone_auto", "") or ""),
        "bot_token_definido": bool(_get("disconnect_alert_bot_token", "")),
    }


@router.put("/alert-settings", dependencies=[core_permission("channel.manage")])
async def put_alert_settings(payload: dict = Body(...)):
    """Salva a configuração do alerta. Campos ausentes não são tocados."""
    before = await asyncio.to_thread(_alert_audit_view)

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
    _audit("alerta.config", before=before,
           after=await asyncio.to_thread(_alert_audit_view))
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
        # Snapshot do "antes" no MESMO hop de thread, ANTES de qualquer escrita:
        # a migração de supergrupo abaixo troca o chat_id salvo em config.
        return token, chat_id, _alert_audit_view()
    token, chat_id, before = await asyncio.to_thread(_resolve)
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
                # A config JÁ mudou — registra AQUI, antes do reenvio. Se o retry
                # estourar, o ``except`` devolve erro mas o chat_id novo continua
                # salvo, e a trilha tem de contar isso. O ``after`` é derivado do
                # snapshot (nada de reler o banco dentro do ``try``, onde uma
                # falha viraria a mensagem enganosa "Falha ao contatar o Telegram").
                # ⚠️ Este é o caminho RARO (o operador clicando "Testar alerta").
                # O comum é o loop de fundo, e ele grava a MESMA ação com ator
                # ``system`` — ver ``alerts._tg_call``. Cobrir só um dos dois
                # faria /audit sugerir que o destino nunca mudou.
                _audit("alerta.chat_id_migrado", before=before,
                       after={**before, "chat_id": new_id})
                resp = await client.post(url, json={**body, "chat_id": new_id})
                data = resp.json()
    except Exception:  # noqa: BLE001
        # O texto de uma exceção httpx costuma carregar a request URL
        # ``https://api.telegram.org/bot{token}/sendMessage``. Refleti-la na
        # resposta anularia o mascaramento que o GET faz de propósito.
        return {"ok": False, "error": "Falha ao contatar o Telegram."}
    if not data.get("ok"):
        return {"ok": False, "error": data.get("description") or "Erro do Telegram."}
    return {"ok": True}
