"""Alerta de desconexão do WhatsApp (GOWA) via Telegram.

100% contido neste plugin — NÃO usa a caixa de entrada/canal Telegram do sistema.
O alerta vai direto à Bot API do Telegram (``api.telegram.org``) usando um token de
bot e um chat_id configurados NA tela deste plugin (``routes.py`` + ``gowa.js``),
independentes de qualquer canal.

Comportamento (decisões do usuário):
- Avisa quando o número CAI (transição conectado→desconectado). Só na queda: a
  recuperação não gera mensagem nova (apenas edita a mensagem existente, silencioso).
- Enquanto continuar fora do ar, RE-AVISA a cada 15 min (configurável) EDITANDO a
  MESMA mensagem no Telegram (``editMessageText``) — "substitui a notificação" em
  vez de mandar uma nova a cada ciclo, evitando flood.
- A mensagem inclui o nome da caixa de entrada (canal GOWA) e a URL direta para
  reconectar (base configurada + a tela do QR ``/gowa/config``).

O estado (message_id, desde quando caiu, último aviso, se já esteve conectado) fica
na tabela ``plugin_gowa_disconnect_alerts`` para sobreviver a restart do processo.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import text

from db.repositories import channel_repo, config_repo
from plugins.context import make_plugin_db

logger = logging.getLogger(__name__)

# Cadência do loop de verificação (a detecção da queda tem no máximo este atraso).
CHECK_INTERVAL = 30  # segundos
# Cadência default do RE-aviso enquanto continua caído (editando a mensagem).
DEFAULT_INTERVAL_MIN = 15
TELEGRAM_API = "https://api.telegram.org"
HTTP_TIMEOUT = 20.0

# Fuso horário exibido nas mensagens. O servidor roda em UTC, então a hora
# precisa ser convertida para o fuso do operador. Default = Brasília (UTC-3),
# que cobre a maior parte do país; configurável por instalação para as demais
# regiões (Manaus/UTC-4, Rio Branco/UTC-5, Fernando de Noronha/UTC-2).
DEFAULT_TZ = "America/Sao_Paulo"
_BRASILIA = timezone(timedelta(hours=-3))  # fallback se a tzdata não existir

_CFG = "plugin.gowa."  # prefixo das configs deste plugin


# ── Config (lida a cada ciclo → habilitar/editar não exige restart) ──────────

def _cfg(key: str, default=None):
    return config_repo.get(_CFG + key, default)


def _resolve_panel_url() -> str:
    """URL base do painel para o link de reconexão, sem o usuário precisar preencher.

    Ordem: override manual (se preenchido) → URL auto-detectada do navegador (salva
    pela rota /alert-settings ao abrir a tela de config) → env pública → localhost.
    """
    manual = (_cfg("disconnect_alert_panel_url", "") or "").strip().rstrip("/")
    if manual:
        return manual
    auto = (_cfg("disconnect_alert_panel_url_auto", "") or "").strip().rstrip("/")
    if auto:
        return auto
    env = (os.environ.get("WHATSBOT_PUBLIC_URL")
           or os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    port = config_repo.get("web_port", 8080) or 8080
    return f"http://localhost:{port}"


def _alert_config() -> dict:
    """Snapshot das configs de alerta (lido do banco a cada ciclo)."""
    try:
        interval = int(_cfg("disconnect_alert_interval_min", DEFAULT_INTERVAL_MIN) or DEFAULT_INTERVAL_MIN)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL_MIN
    return {
        "enabled": bool(_cfg("disconnect_alert_enabled", False)),
        "token": (_cfg("disconnect_alert_bot_token", "") or "").strip(),
        "chat_id": (str(_cfg("disconnect_alert_chat_id", "") or "")).strip(),
        "panel_url": _resolve_panel_url(),
        "interval_min": max(1, interval),
    }


def _primary_gowa_channel() -> tuple[str, str] | None:
    """(channel_id, display_name) do canal GOWA principal — ``default`` de preferência.

    A conexão GOWA é única (um subprocesso/um número), então basta o canal principal
    para dar nome à caixa de entrada na mensagem. Retorna ``None`` se não houver canal.
    """
    try:
        rows = channel_repo.list_all()
    except Exception:  # noqa: BLE001
        return None
    gowa = [r for r in rows if r.get("provider") == "gowa" and r.get("enabled", 1)]
    if not gowa:
        return None
    chan = next((r for r in gowa if r.get("id") == "default"), gowa[0])
    return chan["id"], (chan.get("display_name") or chan["id"])


# ── Estado persistido (tabela plugin_gowa_disconnect_alerts) ─────────────────

def _load_state(channel_id: str) -> dict:
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT ever_connected, disconnected_since, last_alert_ts, "
                 "telegram_chat_id, telegram_message_id, last_phone "
                 "FROM plugin_gowa_disconnect_alerts WHERE channel_id = :cid"),
            {"cid": channel_id},
        ).mappings().first()
    return dict(row) if row else {}


def _save_state(channel_id: str, **fields) -> None:
    """UPSERT parcial: cria a linha se não existir, senão atualiza os campos dados."""
    cols = ["ever_connected", "disconnected_since", "last_alert_ts",
            "telegram_chat_id", "telegram_message_id", "last_phone"]
    values = {c: fields.get(c) for c in cols if c in fields}
    if not values:
        return
    values["cid"] = channel_id
    insert_cols = ["channel_id"] + list(k for k in values if k != "cid")
    insert_vals = [":cid"] + [f":{k}" for k in values if k != "cid"]
    set_clause = ", ".join(f"{k} = :{k}" for k in values if k != "cid")
    with make_plugin_db() as conn:
        conn.execute(
            text(f"INSERT INTO plugin_gowa_disconnect_alerts ({', '.join(insert_cols)}) "
                 f"VALUES ({', '.join(insert_vals)}) "
                 f"ON CONFLICT (channel_id) DO UPDATE SET {set_clause}"),
            values,
        )


# ── Telegram (Bot API direta — sem tocar no canal Telegram do sistema) ───────

async def _tg_call(token: str, method: str, payload: dict) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
        data = resp.json()
        if not data.get("ok"):
            logger.warning("gowa alert: Telegram %s falhou: %s", method, data.get("description"))
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning("gowa alert: erro ao chamar Telegram %s: %s", method, e)
        return {"ok": False, "description": str(e)}


async def _tg_send(token: str, chat_id: str, text_html: str) -> int | None:
    data = await _tg_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if data.get("ok"):
        return data.get("result", {}).get("message_id")
    return None


async def _tg_edit(token: str, chat_id: str, message_id: int, text_html: str) -> bool:
    data = await _tg_call(token, "editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if data.get("ok"):
        return True
    desc = (data.get("description") or "").lower()
    if "not modified" in desc:  # conteúdo igual — considera sucesso
        return True
    return False


# ── Formatação das mensagens ─────────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}min"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _reconnect_url(panel_url: str) -> str:
    base = panel_url or "http://localhost:8080"
    return f"{base}/gowa/config"


def _resolve_tz_name() -> str:
    """Nome IANA do fuso para exibir a hora, sem o usuário precisar preencher.

    Ordem: override manual (se preenchido) → fuso auto-detectado do navegador (salvo
    pela rota /alert-settings ao abrir a tela) → default Brasília.
    """
    manual = (_cfg("disconnect_alert_timezone", "") or "").strip()
    if manual:
        return manual
    auto = (_cfg("disconnect_alert_timezone_auto", "") or "").strip()
    if auto:
        return auto
    return DEFAULT_TZ


def _tz() -> timezone | ZoneInfo:
    """Fuso configurado para exibir a hora (default Brasília).

    Cai no offset fixo UTC-3 se o nome for inválido ou a base de fusos (tzdata)
    não estiver disponível no sistema — assim a mensagem nunca sai em UTC."""
    for candidate in (_resolve_tz_name(), DEFAULT_TZ):
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    return _BRASILIA


def _now_str() -> str:
    return datetime.now(_tz()).strftime("%H:%M de %d/%m/%Y")


def _msg_down_first(name: str, phone: str, panel_url: str) -> str:
    return (
        "🔴 <b>WhatsApp desconectado</b>\n\n"
        f"Caixa de entrada: <b>{name}</b>\n"
        f"Número: {phone or '—'}\n"
        f"Caiu às {_now_str()}\n\n"
        f'🔗 <a href="{_reconnect_url(panel_url)}">Reconectar agora</a>'
    )


def _msg_down_repeat(name: str, phone: str, panel_url: str, down_for: float) -> str:
    return (
        "🔴 <b>WhatsApp ainda desconectado</b>\n\n"
        f"Caixa de entrada: <b>{name}</b>\n"
        f"Número: {phone or '—'}\n"
        f"Fora do ar há {_fmt_duration(down_for)}\n"
        f"Última verificação: {_now_str()}\n\n"
        f'🔗 <a href="{_reconnect_url(panel_url)}">Reconectar agora</a>'
    )


def _msg_recovered(name: str, down_for: float) -> str:
    return (
        "✅ <b>WhatsApp reconectado</b>\n\n"
        f"Caixa de entrada: <b>{name}</b>\n"
        f"Ficou fora do ar por {_fmt_duration(down_for)}.\n"
        f"Reconectado às {_now_str()}."
    )


# ── Loop principal ───────────────────────────────────────────────────────────

async def _tick(deps) -> None:
    cfg = await asyncio.to_thread(_alert_config)
    if not cfg["enabled"] or not cfg["token"] or not cfg["chat_id"]:
        return  # alertas desligados / não configurados

    channel = await asyncio.to_thread(_primary_gowa_channel)
    if not channel:
        return
    channel_id, display_name = channel

    connected = bool(getattr(deps.state, "connected", False))
    live_phone = getattr(deps.state, "bot_phone", "") or ""
    st = await asyncio.to_thread(_load_state, channel_id)
    now = time.time()

    # Número a exibir: o ao vivo (quando conectado) ou o último conhecido salvo — o
    # state.bot_phone é esvaziado enquanto o número fica fora do ar, então sem isto a
    # mensagem de repetição sairia com "Número: —".
    phone = live_phone or (st.get("last_phone") or "")

    if connected:
        # Marca que já esteve conectado e persiste o número atual como "último conhecido".
        if not st.get("ever_connected") or (live_phone and live_phone != st.get("last_phone")):
            await asyncio.to_thread(_save_state, channel_id, ever_connected=True,
                                    last_phone=live_phone or st.get("last_phone"))
        # Se estava caído, edita a mensagem existente p/ "reconectado" (silencioso) e limpa.
        if st.get("disconnected_since"):
            down_for = now - float(st["disconnected_since"])
            mid = st.get("telegram_message_id")
            if mid:
                await _tg_edit(cfg["token"], st.get("telegram_chat_id") or cfg["chat_id"],
                               int(mid), _msg_recovered(display_name, down_for))
            await asyncio.to_thread(
                _save_state, channel_id,
                disconnected_since=None, telegram_message_id=None, last_alert_ts=None)
        return

    # --- desconectado ---
    if not st.get("ever_connected"):
        return  # nunca esteve conectado (nunca pareado / boot) — não alarma

    if not st.get("disconnected_since"):
        # 1ª detecção da queda → envia a primeira mensagem e guarda o message_id.
        mid = await _tg_send(cfg["token"], cfg["chat_id"],
                             _msg_down_first(display_name, phone, cfg["panel_url"]))
        await asyncio.to_thread(
            _save_state, channel_id,
            disconnected_since=now, last_alert_ts=now,
            telegram_chat_id=cfg["chat_id"],
            telegram_message_id=mid,
            last_phone=phone or st.get("last_phone"))
        logger.info("gowa alert: canal '%s' desconectado — alerta enviado (msg_id=%s)",
                    channel_id, mid)
        return

    # Já estava caído: RE-avisa a cada interval_min editando a MESMA mensagem.
    last = float(st.get("last_alert_ts") or 0)
    if now - last < cfg["interval_min"] * 60:
        return
    down_for = now - float(st["disconnected_since"])
    body = _msg_down_repeat(display_name, phone, cfg["panel_url"], down_for)
    mid = st.get("telegram_message_id")
    chat = st.get("telegram_chat_id") or cfg["chat_id"]
    edited = await _tg_edit(cfg["token"], chat, int(mid), body) if mid else False
    if not edited:
        # Mensagem sumiu / muito antiga p/ editar → manda uma nova e re-ancora o id.
        mid = await _tg_send(cfg["token"], cfg["chat_id"], body)
        await asyncio.to_thread(_save_state, channel_id,
                                telegram_chat_id=cfg["chat_id"], telegram_message_id=mid)
    await asyncio.to_thread(_save_state, channel_id, last_alert_ts=now)


async def disconnect_alert_loop(deps) -> None:
    """Task supervisionada: verifica a conexão a cada ``CHECK_INTERVAL`` e alerta."""
    logger.info("gowa alert: loop de alerta de desconexão iniciado")
    while not deps.state.stop_event.is_set():
        try:
            await _tick(deps)
        except Exception as e:  # noqa: BLE001 — um erro num ciclo nunca derruba o loop
            logger.warning("gowa alert: erro no ciclo de verificação: %s", e)
        # state.stop_event é threading.Event (não asyncio) — não dá para await no wait();
        # dorme em fatias curtas e checa is_set() para sair rápido no shutdown.
        for _ in range(CHECK_INTERVAL):
            if deps.state.stop_event.is_set():
                break
            await asyncio.sleep(1)
