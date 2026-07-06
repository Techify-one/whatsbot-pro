"""Alerta de desconexão do WhatsApp (GOWA) via Telegram.

100% contido neste plugin — NÃO usa a caixa de entrada/canal Telegram do sistema.
O alerta vai direto à Bot API do Telegram (``api.telegram.org``) usando um token de
bot e um chat_id configurados NA tela deste plugin (``routes.py`` + ``gowa.js``),
independentes de qualquer canal.

Comportamento (decisões do usuário) — alerta AGREGADO por status de conexão:
- Verificação GLOBAL a cada ciclo. Entre TODAS as caixas GOWA com o alerta ligado,
  as que estão FORA DO AR (device não logado) são listadas numa ÚNICA mensagem no
  Telegram — um bloco (caixa + número) por caixa caída. Cai uma a mais → a mensagem
  ganha outro bloco. Uma volta → o bloco some.
- A mensagem é RE-ENVIADA (apaga a anterior via ``deleteMessage`` + manda nova via
  ``sendMessage``, voltando ao FIM do chat) quando (a) é a 1ª queda, (b) o CONJUNTO
  de caixas caídas muda, ou (c) passou o intervalo configurado (minutos, global).
- Quando TODAS voltam, a msg de queda é apagada e sai um "reconectado(s)".
- O número exibido é o do device do canal (``own_phone``); quando desconectado, o
  último número conhecido salvo (``last_phone``).
- Status por canal vem da instância viva do provider (``channel_registry.get(id).status()``),
  que consulta o ``/app/status`` do device — cada caixa GOWA é um device no MESMO
  subprocesso, então NÃO se usa mais o ``state.connected`` global.

O LIGA/DESLIGA do alerta é POR CANAL: fica em ``channels.config.disconnect_alert_enabled``,
editável na edição da caixa GOWA (tela Canais). O token/chat_id/intervalo/fuso seguem
globais na tela do plugin. Instalações antigas que ligavam pelo flag global do plugin
são migradas one-time para o config do canal no 1º ciclo.

Estado em ``plugin_gowa_disconnect_alerts``: uma linha por canal guarda ever_connected
+ last_phone; a linha reservada ``__all__`` guarda a mensagem agregada (message_id,
chat_id, último aviso, assinatura do conjunto caído) — tudo sobrevive a restart.
"""

from __future__ import annotations

import asyncio
import json
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
    """URL base do painel para o link de reconexão.

    Usa a variável GLOBAL do core ``public_base_url`` (capturada no 1º acesso ao
    painel — server/routes/config.py). Fallbacks: env pública → localhost:web_port.
    """
    base = (config_repo.get("public_base_url", "") or "").strip().rstrip("/")
    if base:
        return base
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


def _parse_config(raw) -> dict:
    """Config do canal (coluna ``channels.config``) como dict — aceita str JSON ou dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _all_gowa_channels() -> list[dict]:
    """Todos os canais GOWA ativos: [{id, name, config, row_logged_in, row_own_phone}].

    Com múltiplas caixas GOWA (N devices no MESMO subprocesso), o alerta é agregado:
    varre TODOS os canais e, entre os com alerta ligado, lista os que estão fora do ar
    numa única mensagem. O ``config`` traz o liga/desliga por canal.
    """
    try:
        rows = channel_repo.list_all()
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for r in rows:
        if r.get("provider") != "gowa" or not r.get("enabled", 1):
            continue
        out.append({
            "id": r["id"],
            "name": r.get("display_name") or r["id"],
            "config": _parse_config(r.get("config")),
            "row_logged_in": bool(r.get("logged_in")),
            "row_own_phone": r.get("own_phone") or "",
        })
    return out


AGGREGATE_KEY = "__all__"  # channel_id reservado da linha de estado da MSG agregada


def _channel_live_status(deps, channel_id: str, fallback_logged_in: bool,
                         fallback_phone: str) -> tuple[bool, str]:
    """(logged_in, own_phone) do device do canal, via instância viva no registry.

    Cada canal GOWA tem seu ``GOWAClient`` device-bound; ``status()`` consulta o
    ``/app/status`` daquele device. Sem instância viva (canal recém-criado antes do
    restart), cai no status persistido na linha do canal.
    """
    registry = getattr(deps, "channel_registry", None)
    inst = registry.get(channel_id) if registry is not None else None
    if inst is None or not hasattr(inst, "status"):
        return fallback_logged_in, fallback_phone
    try:
        st = inst.status() or {}
        return bool(st.get("logged_in")), (st.get("own_phone") or "")
    except Exception:  # noqa: BLE001 — status best-effort; usa o fallback da linha
        return fallback_logged_in, fallback_phone


def _migrate_enable_to_channel(channel_id: str, current_cfg: dict, value: bool) -> None:
    """Grava ``disconnect_alert_enabled`` no config do canal, preservando as demais chaves.

    Migração one-time: instalações que ligavam o alerta pelo flag GLOBAL do plugin
    herdam esse valor no config do canal (o liga/desliga passou a ser por canal).
    """
    cfg = dict(current_cfg or {})
    cfg["disconnect_alert_enabled"] = bool(value)
    try:
        channel_repo.update(channel_id, config=json.dumps(cfg))
    except Exception:  # noqa: BLE001 — migração best-effort; fallback ao global cobre o ciclo
        pass


# ── Estado persistido (tabela plugin_gowa_disconnect_alerts) ─────────────────

def _load_state(channel_id: str) -> dict:
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT ever_connected, disconnected_since, last_alert_ts, "
                 "telegram_chat_id, telegram_message_id, last_phone, down_signature "
                 "FROM plugin_gowa_disconnect_alerts WHERE channel_id = :cid"),
            {"cid": channel_id},
        ).mappings().first()
    return dict(row) if row else {}


def _save_state(channel_id: str, **fields) -> None:
    """UPSERT parcial: cria a linha se não existir, senão atualiza os campos dados."""
    cols = ["ever_connected", "disconnected_since", "last_alert_ts",
            "telegram_chat_id", "telegram_message_id", "last_phone", "down_signature"]
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


async def _tg_delete(token: str, chat_id: str, message_id: int) -> bool:
    data = await _tg_call(token, "deleteMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
    })
    if data.get("ok"):
        return True
    # Mensagem já apagada / velha demais / inexistente — não é erro fatal para o
    # fluxo: seguimos e enviamos a nova mesmo assim.
    return False


# ── Formatação das mensagens ─────────────────────────────────────────────────

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


def _msg_disconnected(down: list[dict], panel_url: str) -> str:
    """Mensagem AGREGADA: um bloco (caixa + número) por canal fora do ar.

    ``down`` = [{name, phone}, ...] das caixas GOWA desconectadas (alerta ligado).
    O singular/plural do título é resolvido pela quantidade.
    """
    plural = "s" if len(down) > 1 else ""
    blocks = "\n\n".join(
        f"Caixa de entrada: <b>{ch['name']}</b>\n"
        f"Número: {ch.get('phone') or '—'}"
        for ch in down
    )
    return (
        f"🔴 <b>WhatsApp desconectado{plural}</b>\n\n"
        f"{blocks}\n\n"
        f'🔗 <a href="{_reconnect_url(panel_url)}">Reconectar agora</a>'
    )


def _msg_recovered_all() -> str:
    return (
        "✅ <b>WhatsApp reconectado(s)</b>\n\n"
        "Todas as caixas monitoradas voltaram a ficar online.\n"
        f"Reconectado às {_now_str()}."
    )


# ── Loop principal ───────────────────────────────────────────────────────────

async def _tick(deps) -> None:
    cfg = await asyncio.to_thread(_alert_config)
    if not cfg["token"] or not cfg["chat_id"]:
        return  # bot/destino não configurados (tela Plugins → WhatsApp (GOWA))

    channels = await asyncio.to_thread(_all_gowa_channels)
    if not channels:
        return
    now = time.time()

    # Passo 1 — por canal: resolve o liga/desliga (por canal, com migração do flag
    # global legado), lê o status AO VIVO do device e persiste ever_connected/last_phone.
    # Monta a lista de caixas FORA DO AR (alerta ligado + já conectou alguma vez +
    # agora não logada). Uma caixa nunca pareada nunca entra (evita alarme de warm-up).
    down: list[dict] = []
    for ch in channels:
        cid = ch["id"]
        chan_cfg = ch["config"]
        ch_enabled = chan_cfg.get("disconnect_alert_enabled")
        if ch_enabled is None:
            ch_enabled = cfg["enabled"]
            if cfg["enabled"]:
                await asyncio.to_thread(_migrate_enable_to_channel, cid, chan_cfg, True)
        if not ch_enabled:
            continue

        st = await asyncio.to_thread(_load_state, cid)
        logged_in, live_phone = await asyncio.to_thread(
            _channel_live_status, deps, cid, ch["row_logged_in"], ch["row_own_phone"])
        phone = live_phone or (st.get("last_phone") or "")

        if logged_in:
            if not st.get("ever_connected") or (live_phone and live_phone != st.get("last_phone")):
                await asyncio.to_thread(_save_state, cid, ever_connected=True,
                                        last_phone=live_phone or st.get("last_phone"))
            continue

        if not st.get("ever_connected"):
            continue  # nunca pareado / warm-up de boot — não alarma
        down.append({"id": cid, "name": ch["name"], "phone": phone})

    # Passo 2 — mensagem AGREGADA (uma só, estado na linha reservada __all__).
    agg = await asyncio.to_thread(_load_state, AGGREGATE_KEY)
    signature = ",".join(sorted(c["id"] for c in down))

    if not down:
        # Nada fora do ar. Se havia alerta ativo, apaga a msg de queda e avisa recuperação.
        old_mid = agg.get("telegram_message_id")
        if old_mid:
            old_chat = agg.get("telegram_chat_id") or cfg["chat_id"]
            await _tg_delete(cfg["token"], old_chat, int(old_mid))
            await _tg_send(cfg["token"], cfg["chat_id"], _msg_recovered_all())
            await asyncio.to_thread(_save_state, AGGREGATE_KEY,
                                    telegram_message_id=None, disconnected_since=None,
                                    last_alert_ts=None, down_signature=None)
        return

    # Há caixas fora do ar. Reenvia quando: (a) 1ª queda, (b) o CONJUNTO de caixas
    # caídas mudou, ou (c) passou o intervalo configurado — sempre APAGANDO a anterior
    # e mandando uma nova (a notificação volta ao fim do chat, nunca fica soterrada).
    last = float(agg.get("last_alert_ts") or 0)
    changed = (agg.get("down_signature") or "") != signature
    due = (now - last) >= cfg["interval_min"] * 60
    has_msg = bool(agg.get("telegram_message_id"))
    if has_msg and not changed and not due:
        return

    body = _msg_disconnected(down, cfg["panel_url"])
    old_mid = agg.get("telegram_message_id")
    old_chat = agg.get("telegram_chat_id") or cfg["chat_id"]
    if old_mid:
        await _tg_delete(cfg["token"], old_chat, int(old_mid))
    mid = await _tg_send(cfg["token"], cfg["chat_id"], body)
    await asyncio.to_thread(
        _save_state, AGGREGATE_KEY,
        disconnected_since=(agg.get("disconnected_since") or now),
        last_alert_ts=now, telegram_chat_id=cfg["chat_id"],
        telegram_message_id=mid, down_signature=signature)
    logger.info("gowa alert: %d caixa(s) fora do ar — alerta agregado (msg_id=%s, sig=%s)",
                len(down), mid, signature)


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
