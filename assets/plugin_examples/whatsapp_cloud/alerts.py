"""Alertas da CONTA Meta (WhatsApp Cloud API) num grupo do Telegram — plano 84.

O painel era cego para tudo que a Meta diz sobre a *conta*: template pausado por
baixa qualidade, número caindo de qualidade, tier de mensagens cortado, conta
restrita. O operador só descobria quando um envio começava a falhar. Este módulo
transforma esses avisos em uma mensagem num grupo do Telegram.

100% contido neste plugin — NÃO usa a caixa de entrada/canal Telegram do sistema
(um alerta não é um atendimento). Fala direto com a Bot API (``api.telegram.org``)
usando token + chat_id configurados na aba **Configurar** deste plugin. O motor é
um port do precedente em produção (``gowa/alerts.py``); duas cópias é o preço de
um zip autossuficiente (plano 76 · D2·F9 — plugin não importa de plugin).

Três fontes alimentam o alerta:

1. **Webhook da conta** — o observador de ``filter.webhook.payload`` em
   ``filters.py`` extrai todo ``change["field"]`` que não seja ``messages`` e
   despacha o alerta diretamente, sem criar ``InboundEvent`` nem ramo de evento
   no core. Exige WABA ID exato + App Secret no canal (HMAC autenticado) e que os
   campos estejam ASSINADOS no App Dashboard da Meta (WhatsApp → Configuração →
   Webhooks); sem isso esta fonte fica muda — e é por isso que a fonte 2 existe.
2. **Polling de qualidade** — ``status()`` do canal já lê ``quality_rating`` e
   jogava fora. O loop supervisionado (``lifecycle.py``) compara com o último
   valor visto e alerta na variação. Não depende de assinatura nenhuma.
3. **Bus ``message.failed``** — o core já avisa quando o provedor recusou a
   entrega. Agregado por código (senão 15 falhas do mesmo fluxo viram 15
   mensagens no grupo).

Anti-spam (R1 do plano): o alerta é deduplicado por VALOR e agregado por janela.
A 1ª ocorrência manda a mensagem; repetições idênticas dentro da janela apenas
EDITAM a mensagem existente com o contador; passada a janela, sai uma nova. Isso
também neutraliza a reentrega de rotina da Meta — sem depender de dedupe no core.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import text

from db.repositories import channel_repo, config_repo
from plugins.context import make_plugin_db

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
HTTP_TIMEOUT = 20.0

# Janela de agregação/reaviso (minutos). Repetição idêntica dentro dela só
# incrementa o contador da mensagem já enviada.
DEFAULT_INTERVAL_MIN = 15
# Cadência do polling de qualidade. Minutos, não segundos: qualidade muda devagar
# e cada tique é uma chamada Graph que consome rate limit da conta (R8).
DEFAULT_QUALITY_POLL_MIN = 10
MIN_QUALITY_POLL_MIN = 5

DEFAULT_TZ = "America/Sao_Paulo"
_BRASILIA = timezone(timedelta(hours=-3))  # fallback se a tzdata não existir

_CFG = "plugin.whatsapp_cloud."
PROVIDER = "whatsapp_cloud"

# Serializa o read-modify-write do estado: dois eventos do mesmo alerta podem
# chegar ao mesmo tempo (a Meta entrega em paralelo) e a contagem não pode correr.
_LOCK = asyncio.Lock()


# ── Catálogo de grupos de alerta (§6 do plano) ───────────────────────────────
# Cada grupo tem liga/desliga próprio na tela. O default de ``send_failure_24h``
# é OFF de propósito: 131047 é erro de OPERAÇÃO (mandar texto livre fora da
# janela de 24h), mede-se às dezenas por dia e vira ruído.
ALERT_GROUPS: dict[str, dict] = {
    "template_down": {
        "label": "Template pausado, reprovado ou desabilitado", "default": True},
    "template_up": {
        "label": "Template aprovado (voltou)", "default": True},
    "template_category": {
        "label": "Template recategorizado (muda o custo)", "default": True},
    "quality": {
        "label": "Qualidade do número caiu ou voltou", "default": True},
    "limit": {
        "label": "Limite de mensagens (tier) mudou", "default": True},
    "account": {
        "label": "Conta restrita, banida ou em revisão", "default": True},
    "send_failure": {
        "label": "Falha de envio relevante (bloqueio, limite, template)",
        "default": True},
    "send_failure_24h": {
        "label": "Falha por janela de 24h (131047) — costuma ser ruído",
        "default": False},
    "unknown": {
        "label": "Outros avisos da Meta (campo novo, não catalogado)",
        "default": True},
}

# Códigos de falha de envio que valem um alerta fora do painel. 131047 tem grupo
# PRÓPRIO (é operação, não saúde da conta) — ver §6 do plano.
FAILURE_CODES_RELEVANT = {131049, 131026, 130429, 132015, 132016, 131056}
FAILURE_CODE_24H = 131047


# ── Config (lida a cada evento → editar não exige restart) ───────────────────

def _cfg(key: str, default=None):
    return config_repo.get(_CFG + key, default)


def _int_cfg(key: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(_cfg(key, default) or default))
    except (TypeError, ValueError):
        return default


def group_enabled(groups: dict, group: str) -> bool:
    """Liga/desliga de um grupo, caindo no default do catálogo quando ausente.

    Grupo desconhecido (plugin novo × config antiga) é tratado como LIGADO: um
    aviso que a Meta passe a mandar nunca fica invisível por omissão (R4).
    """
    if group in (groups or {}):
        return bool(groups[group])
    meta = ALERT_GROUPS.get(group)
    return bool(meta["default"]) if meta else True


def _parse_groups(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def alert_config() -> dict:
    """Snapshot da configuração do alerta (lido do banco a cada uso)."""
    return {
        "enabled": bool(_cfg("alert_enabled", False)),
        "token": (_cfg("alert_bot_token", "") or "").strip(),
        "chat_id": str(_cfg("alert_chat_id", "") or "").strip(),
        "interval_min": _int_cfg("alert_interval_min", DEFAULT_INTERVAL_MIN),
        "quality_poll_min": _int_cfg("alert_quality_poll_min",
                                     DEFAULT_QUALITY_POLL_MIN,
                                     minimum=MIN_QUALITY_POLL_MIN),
        "groups": _parse_groups(_cfg("alert_groups", None)),
    }


def _resolve_tz_name() -> str:
    manual = (_cfg("alert_timezone", "") or "").strip()
    if manual:
        return manual
    auto = (_cfg("alert_timezone_auto", "") or "").strip()
    return auto or DEFAULT_TZ


def _tz():
    """Fuso configurado (default Brasília) — o servidor roda em UTC."""
    for candidate in (_resolve_tz_name(), DEFAULT_TZ):
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    return _BRASILIA


def _now_str() -> str:
    return datetime.now(_tz()).strftime("%H:%M de %d/%m/%Y")


# ── Estado persistido (plugin_whatsapp_cloud_alert_state) ────────────────────

def load_state(alert_key: str) -> dict:
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT alert_key, last_value, last_alert_ts, occurrences, "
                 "telegram_chat_id, telegram_message_id "
                 "FROM plugin_whatsapp_cloud_alert_state WHERE alert_key = :k"),
            {"k": alert_key},
        ).mappings().first()
    return dict(row) if row else {}


def save_state(alert_key: str, **fields) -> None:
    """UPSERT parcial: cria a linha se não existir, senão atualiza os campos dados."""
    cols = ["last_value", "last_alert_ts", "occurrences",
            "telegram_chat_id", "telegram_message_id"]
    values = {c: fields[c] for c in cols if c in fields}
    if not values:
        return
    values["k"] = alert_key
    insert_cols = ["alert_key"] + [c for c in values if c != "k"]
    insert_vals = [":k"] + [f":{c}" for c in values if c != "k"]
    set_clause = ", ".join(f"{c} = :{c}" for c in values if c != "k")
    with make_plugin_db() as conn:
        conn.execute(
            text(f"INSERT INTO plugin_whatsapp_cloud_alert_state "
                 f"({', '.join(insert_cols)}) VALUES ({', '.join(insert_vals)}) "
                 f"ON CONFLICT (alert_key) DO UPDATE SET {set_clause}"),
            values,
        )


# ── Telegram (Bot API direta — não toca no canal Telegram do sistema) ────────

async def _tg_call(token: str, method: str, payload: dict,
                   _migrated: bool = False) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
        data = resp.json()
        if not data.get("ok"):
            # Grupo promovido a supergrupo → o chat_id mudou. O Telegram devolve o
            # novo id em ``parameters.migrate_to_chat_id``: persistimos e refazemos
            # a chamada UMA vez, transparente ao caller.
            new_id = (data.get("parameters") or {}).get("migrate_to_chat_id")
            if new_id and payload.get("chat_id") is not None and not _migrated:
                new_id = str(new_id)
                await asyncio.to_thread(config_repo.set, _CFG + "alert_chat_id", new_id)
                logger.info("whatsapp_cloud alert: grupo virou supergrupo — "
                            "chat_id %s → %s (auto-atualizado)",
                            payload["chat_id"], new_id)
                return await _tg_call(token, method, {**payload, "chat_id": new_id},
                                      _migrated=True)
            logger.warning("whatsapp_cloud alert: Telegram %s falhou: %s",
                           method, data.get("description"))
        if data.get("ok") and payload.get("chat_id") is not None:
            # Valor efetivamente usado na chamada confirmada. Em uma migração a
            # recursão devolve o chat novo, não o id antigo do primeiro request.
            data["_effective_chat_id"] = str(payload["chat_id"])
        return data
    except Exception as e:  # noqa: BLE001
        # A URL da Bot API contém o token. Exceções de transporte podem incluir
        # a request URL no próprio texto, portanto jamais ecoamos/logamos ``e``.
        logger.warning("whatsapp_cloud alert: erro de transporte ao chamar "
                       "Telegram %s (%s)", method, type(e).__name__)
        return {"ok": False, "description": "Falha de transporte no Telegram."}


async def _tg_send(token: str, chat_id: str,
                   text_html: str) -> tuple[int, str] | None:
    data = await _tg_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if data.get("ok"):
        message_id = (data.get("result") or {}).get("message_id")
        if message_id is not None:
            return int(message_id), str(data.get("_effective_chat_id") or chat_id)
    return None


async def _tg_edit(token: str, chat_id: str, message_id: int,
                   text_html: str) -> tuple[bool, str]:
    data = await _tg_call(token, "editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if data.get("ok"):
        return True, str(data.get("_effective_chat_id") or chat_id)
    desc = (data.get("description") or "").lower()
    if "not modified" in desc:  # conteúdo igual — considera sucesso
        return True, str(chat_id)
    return False, str(chat_id)


# ── Classificação do aviso da conta (PURA — testável sem rede/DB) ────────────

_TEMPLATE_DOWN_EVENTS = {"PAUSED", "REJECTED", "DISABLED", "PENDING_DELETION",
                         "FLAGGED"}
_TEMPLATE_UP_EVENTS = {"APPROVED"}

# ``account_update.event`` que fala de RESTRIÇÃO da conta (o pior caso).
_ACCOUNT_BAD_EVENTS = {"ACCOUNT_RESTRICTION", "ACCOUNT_VIOLATION", "DISABLED_UPDATE",
                       "ACCOUNT_DELETED", "ACCOUNT_BAN", "BUSINESS_BAN"}
# ``account_update.event`` que fala do TIER de mensagens.
_ACCOUNT_LIMIT_EVENTS = {"MESSAGE_TEMPLATE_LIMIT_UPDATE", "PHONE_NUMBER_QUALITY_UPDATE",
                         "TIER_UPDATE", "MESSAGING_LIMIT_UPDATE",
                         "BUSINESS_CAPABILITY_UPDATE"}

_QUALITY_EMOJI = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "UNKNOWN": "⚪"}


def _s(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _h(value) -> str:
    """Escape a dynamic value for Telegram ``parse_mode=HTML``."""
    return html.escape(str(value), quote=True)


def _tpl_label(value: dict) -> str:
    name = _s(value.get("message_template_name"))
    lang = _s(value.get("message_template_language"))
    if name and lang:
        return f"{name} ({lang})"
    return name or _s(value.get("message_template_id")) or "—"


def describe_account_event(field: str, value: dict) -> dict | None:
    """Classifica um ``change`` da conta → descritor do alerta (função PURA).

    Devolve ``{group, key, signature, emoji, title, lines}`` ou ``None`` quando o
    aviso não merece alerta (evento neutro, tipo ``PENDING``).

    * ``group``     — chave do catálogo (liga/desliga na tela)
    * ``key``       — identidade do alerta (dedupe/agregação por template, por
                      número, por código…), sem o prefixo do canal
    * ``signature`` — o VALOR do aviso: mudou ⇒ alerta novo; igual ⇒ agrega

    ⚠️ Campo desconhecido NUNCA é ignorado (R4): cai no grupo ``unknown`` com o
    JSON resumido, para que um campo novo da Meta apareça em vez de sumir.
    """
    field = _s(field)
    value = value if isinstance(value, dict) else {}
    if not field:
        return None

    # ── Template mudou de status ──────────────────────────────────────
    if field == "message_template_status_update":
        event = _s(value.get("event")).upper()
        label = _tpl_label(value)
        reason = _s(value.get("reason")) or _s(
            (value.get("disable_info") or {}).get("disable_date"))
        if event in _TEMPLATE_DOWN_EVENTS:
            lines = [f"Template: <b>{_h(label)}</b>",
                     f"Novo status: <b>{_h(event)}</b>"]
            if reason and reason.upper() != "NONE":
                lines.append(f"Motivo informado: {_h(reason)}")
            if event == "PAUSED":
                lines.append("Enquanto pausado, envios com este template falham "
                             "(códigos 132015/132016).")
            return {"group": "template_down", "key": f"template:{label}",
                    "signature": event, "emoji": "🔴",
                    "title": "Template fora do ar", "lines": lines}
        if event in _TEMPLATE_UP_EVENTS:
            return {"group": "template_up", "key": f"template:{label}",
                    "signature": event, "emoji": "🟢",
                    "title": "Template aprovado",
                    "lines": [f"Template: <b>{_h(label)}</b>",
                              "Voltou a poder ser usado."]}
        return None  # PENDING & cia. — ruído

    # ── Qualidade do template ─────────────────────────────────────────
    if field == "message_template_quality_update":
        label = _tpl_label(value)
        prev = _s(value.get("previous_quality_score")).upper() or "—"
        new = _s(value.get("new_quality_score")).upper() or "—"
        worse = new in ("RED", "YELLOW") and new != prev
        return {"group": "template_down" if worse else "template_up",
                "key": f"template_quality:{label}", "signature": f"{prev}->{new}",
                "emoji": _QUALITY_EMOJI.get(new, "🟡"),
                "title": "Qualidade do template mudou",
                "lines": [f"Template: <b>{_h(label)}</b>",
                          f"Qualidade: {_h(prev)} → <b>{_h(new)}</b>",
                          "Qualidade vermelha leva à pausa do template."]}

    # ── Recategorização (muda o custo e expõe ao cap 131049) ──────────
    if field == "template_category_update":
        label = _tpl_label(value)
        prev = _s(value.get("previous_category")).upper() or "—"
        new = _s(value.get("new_category")).upper() or "—"
        return {"group": "template_category", "key": f"template_category:{label}",
                "signature": f"{prev}->{new}", "emoji": "🟡",
                "title": "Template recategorizado",
                "lines": [f"Template: <b>{_h(label)}</b>",
                          f"Categoria: {_h(prev)} → <b>{_h(new)}</b>",
                          "A categoria define o preço da conversa e o limite por "
                          "cliente."]}

    # ── Qualidade / limite do NÚMERO ──────────────────────────────────
    if field == "phone_number_quality_update":
        event = _s(value.get("event")).upper()
        limit = _s(value.get("current_limit")).upper()
        number = _s(value.get("display_phone_number"))
        lines = [f"Número: {_h(number or '—')}"]
        if event:
            lines.append(f"Evento: <b>{_h(event)}</b>")
        if limit:
            lines.append(f"Limite atual: <b>{_h(limit)}</b>")
        flagged = event == "FLAGGED"
        return {"group": "quality", "key": f"phone_quality:{number or 'default'}",
                "signature": f"{event}|{limit}",
                "emoji": "🔴" if flagged else "🟢",
                "title": ("Número sinalizado pela Meta" if flagged
                          else "Qualidade do número atualizada"),
                "lines": lines}

    # ── Capacidade da conta (tier de mensagens) ───────────────────────
    if field == "business_capability_update":
        per_phone = _s(value.get("max_daily_conversation_per_phone"))
        max_numbers = _s(value.get("max_phone_numbers_per_business"))
        lines = []
        if per_phone:
            lines.append(f"Conversas por número/dia: <b>{_h(per_phone)}</b>")
        if max_numbers:
            lines.append(f"Números permitidos: {_h(max_numbers)}")
        if not lines:
            lines.append("A Meta alterou a capacidade da conta.")
        return {"group": "limit", "key": "capability", "emoji": "🟡",
                "signature": f"{per_phone}|{max_numbers}",
                "title": "Limite de mensagens mudou", "lines": lines}

    # ── Conta: restrição, ban, revisão ────────────────────────────────
    if field == "account_update":
        event = _s(value.get("event")).upper()
        number = _s(value.get("phone_number"))
        lines = [f"Evento: <b>{_h(event or '—')}</b>"]
        if number:
            lines.append(f"Número: {_h(number)}")
        for extra_key in ("ban_info", "restriction_info", "violation_info"):
            extra = value.get(extra_key)
            if extra:
                serialized = json.dumps(extra, ensure_ascii=False)[:400]
                lines.append(f"{extra_key}: {_h(serialized)}")
        bad = event in _ACCOUNT_BAD_EVENTS
        group = "limit" if event in _ACCOUNT_LIMIT_EVENTS else "account"
        return {"group": group, "key": f"account:{event or 'update'}",
                "signature": json.dumps(value, ensure_ascii=False, sort_keys=True)[:900],
                "emoji": "🔴" if bad else "🟡",
                "title": ("Conta com restrição" if bad
                          else "Aviso da conta na Meta"), "lines": lines}

    if field == "account_review_update":
        decision = _s(value.get("decision")).upper() or "—"
        approved = decision == "APPROVED"
        return {"group": "account", "key": "account_review", "signature": decision,
                "emoji": "🟢" if approved else "🔴",
                "title": "Revisão da conta concluída",
                "lines": [f"Decisão: <b>{_h(decision)}</b>"]}

    # ── Campo não catalogado — NUNCA ignorar (R4) ─────────────────────
    resumo = json.dumps(value, ensure_ascii=False, sort_keys=True)[:600] or "{}"
    return {"group": "unknown", "key": f"field:{field}", "signature": resumo,
            "emoji": "🟡", "title": f"Aviso da Meta: {field}",
            "lines": [f"<code>{_h(resumo)}</code>"]}


def describe_failure_event(payload: dict) -> dict | None:
    """Classifica um ``message.failed`` do bus → descritor do alerta (PURA).

    ``None`` quando o código não merece alerta. O guard de reentrega
    (``is_redelivery``) fica no chamador — este módulo só classifica.
    """
    payload = payload if isinstance(payload, dict) else {}
    raw_code = payload.get("error_code")
    try:
        code = int(str(raw_code).strip())
    except (TypeError, ValueError):
        code = None
    if code is None:
        return None
    if code == FAILURE_CODE_24H:
        group = "send_failure_24h"
    elif code in FAILURE_CODES_RELEVANT:
        group = "send_failure"
    else:
        return None

    try:  # frase PT-BR do core — não reescrever (F5·4 do plano)
        from server.message_errors import describe_failure
        reason = describe_failure(code, payload.get("error_title") or "",
                                  payload.get("error_details") or "")
    except Exception:  # noqa: BLE001 — core antigo / import indisponível
        reason = _s(payload.get("error_title")) or f"código {code}"

    return {"group": group, "key": f"failure:{code}", "signature": str(code),
            "emoji": "🔴", "title": "Falha de envio",
            "lines": [_h(reason), f"Código: <b>{code}</b>"]}


def describe_quality_change(previous: str, current: str, *, number: str = "") -> dict | None:
    """Descritor do alerta de qualidade vindo do POLLING (PURA).

    ``None`` quando não houve mudança (ou não há valor novo). A 1ª leitura de um
    canal (sem ``previous``) grava a linha de base sem alertar — quem faz isso é
    o loop; aqui basta que ``previous`` vazio não gere alerta.
    """
    previous = _s(previous).upper()
    current = _s(current).upper()
    if not current or not previous or previous == current:
        return None
    lines = [f"Número: {_h(number or '—')}",
             f"Qualidade: {_h(previous)} → <b>{_h(current)}</b>"]
    if current == "RED":
        lines.append("Qualidade vermelha leva a corte de tier e pausa de templates.")
    return {"group": "quality", "key": f"quality_poll:{number or 'default'}",
            "signature": f"{previous}->{current}",
            "emoji": _QUALITY_EMOJI.get(current, "🟡"),
            "title": "Qualidade do número mudou", "lines": lines}


# ── Agregação / cooldown (PURA) ──────────────────────────────────────────────

def should_alert(state: dict | None, signature: str, now: float,
                 cooldown_sec: float) -> tuple[str, int]:
    """Decide o que fazer com uma ocorrência: ``("send"|"edit", contagem)``.

    * valor NOVO (ou 1ª vez) ⇒ ``send`` com contagem 1;
    * valor IGUAL dentro da janela ⇒ ``edit`` da mensagem existente com a
      contagem incrementada (é isso que impede 15 falhas virarem 15 mensagens);
    * valor IGUAL com a janela vencida ⇒ ``send`` de novo (o problema persiste e
      a mensagem antiga já ficou soterrada no grupo);
    * valor IGUAL, dentro da janela e sem mensagem confirmada ⇒ tenta ``send``
      de novo (uma falha de transporte nunca consome cooldown).
    """
    state = state or {}
    last_ts = float(state.get("last_alert_ts") or 0)
    if not last_ts or (state.get("last_value") or "") != (signature or ""):
        return "send", 1
    count = int(state.get("occurrences") or 1) + 1
    if (now - last_ts) >= cooldown_sec:
        return "send", count
    if state.get("telegram_message_id"):
        return "edit", count
    return "send", count


def format_alert(desc: dict, *, channel_label: str = "", when: str = "",
                 count: int = 1) -> str:
    """Monta o texto HTML da mensagem do Telegram (PURA).

    ``desc`` é o descritor devolvido pelos ``describe_*``. O canal entra SEMPRE
    no texto (P5 do plano: alerta global, mas o operador precisa saber de qual
    número/caixa se trata).
    """
    desc = desc or {}
    emoji = _h(desc.get("emoji") or "🟡")
    title = _h(desc.get("title") or "Aviso da Meta")
    parts = [f"{emoji} <b>{title}</b>"]
    body = "\n".join(str(line) for line in (desc.get("lines") or []) if line)
    if channel_label:
        safe_label = _h(channel_label)
        body = (f"Canal: <b>{safe_label}</b>\n{body}"
                if body else f"Canal: <b>{safe_label}</b>")
    if body:
        parts.append(body)
    rodape = []
    if count and count > 1:
        rodape.append(f"Ocorrências: {count}")
    if when:
        rodape.append(_h(when))
    if rodape:
        parts.append(" · ".join(rodape))
    return "\n\n".join(parts)


# ── Entrega (impura: config + estado + rede) ─────────────────────────────────

def _channel_label(channel_id: str) -> str:
    """"Nome da caixa (número)" para o texto do alerta — best-effort."""
    try:
        row = channel_repo.get(channel_id) or {}
    except Exception:  # noqa: BLE001
        return channel_id
    name = row.get("display_name") or channel_id
    phone = row.get("own_phone") or ""
    return f"{name} ({phone})" if phone else str(name)


async def deliver(desc: dict | None, channel_id: str) -> str:
    """Entrega um descritor ao grupo do Telegram. Devolve a ação executada.

    ``"off"`` (alerta desligado/sem credencial), ``"muted"`` (grupo de alerta
    desligado), ``"edit"``/``"send"`` conforme a agregação, ou ``"failed"``
    quando o Telegram não confirmou a entrega. Nunca levanta: um alerta que
    falha não pode derrubar o webhook nem o loop.
    """
    if not desc:
        return "skip"
    cfg = await asyncio.to_thread(alert_config)
    if not cfg["enabled"] or not cfg["token"] or not cfg["chat_id"]:
        return "off"
    if not group_enabled(cfg["groups"], desc.get("group") or "unknown"):
        return "muted"

    alert_key = f"{channel_id or 'default'}|{desc.get('key') or 'evento'}"
    signature = str(desc.get("signature") or "")
    now = time.time()

    async with _LOCK:
        state = await asyncio.to_thread(load_state, alert_key)
        action, count = should_alert(state, signature, now, cfg["interval_min"] * 60)
        label = await asyncio.to_thread(_channel_label, channel_id)
        body = format_alert(desc, channel_label=label,
                            when=await asyncio.to_thread(_now_str), count=count)

        if action == "edit":
            chat = state.get("telegram_chat_id") or cfg["chat_id"]
            edited = await _tg_edit(cfg["token"], chat,
                                    int(state["telegram_message_id"]), body)
            if isinstance(edited, tuple):
                ok, confirmed_chat_id = edited
            else:
                # Compatibilidade com overrides/mocks anteriores que devolvem bool.
                ok, confirmed_chat_id = bool(edited), chat
            if ok:
                await asyncio.to_thread(
                    save_state, alert_key, occurrences=count,
                    telegram_chat_id=confirmed_chat_id)
                return "edit"
            action = "send"  # mensagem sumiu do grupo — manda uma nova

        sent = await _tg_send(cfg["token"], cfg["chat_id"], body)
        if sent is None:
            # Não consumir o cooldown de algo que nunca chegou. Persistir
            # last_alert_ts/last_value aqui faria a próxima ocorrência idêntica
            # parecer repetição e ficar muda durante toda a janela.
            logger.warning("whatsapp_cloud alert: Telegram não confirmou envio (%s)",
                           alert_key)
            return "failed"
        # Compatibilidade com transportes mockados/overrides anteriores que
        # devolvem apenas o int. A implementação real devolve também o chat que
        # o Telegram confirmou após eventual migrate_to_chat_id.
        if isinstance(sent, tuple):
            mid, confirmed_chat_id = sent
        else:
            mid, confirmed_chat_id = sent, cfg["chat_id"]
        await asyncio.to_thread(
            save_state, alert_key, last_value=signature, last_alert_ts=now,
            occurrences=count, telegram_chat_id=confirmed_chat_id,
            telegram_message_id=mid)
        logger.info("whatsapp_cloud alert: %s (%s) enviado ao Telegram (msg_id=%s)",
                    desc.get("title"), alert_key, mid)
        return "send"


# ── Handlers do bus (assinados em events.py) ─────────────────────────────────


async def handle_account_change(field: str, value: dict, waba_id: str = "",
                                channel_id: str | None = None) -> str:
    """Aviso da CONTA capturado no webhook cru (fonte 1) → alerta no Telegram.

    Chamado pelo observador ``filters.observe``, FORA do caminho do request.
    Sem evento/ramo específico no dispatch: a procedência e o HMAC chegam pelo
    seam genérico do filtro cru no core.
    """
    try:
        desc = describe_account_event(field, value)
        if desc is None:
            return "skip"
        # O filtro só chama este handler depois de autenticar a assinatura e casar
        # o WABA com a credencial do canal. Sem canal exato, fail-closed.
        if not channel_id:
            return "skip"
        return await deliver(desc, channel_id)
    except Exception:  # noqa: BLE001 — alerta nunca derruba o webhook
        logger.warning("whatsapp_cloud alert: falha ao tratar aviso da conta (%s)",
                       field, exc_info=True)
        return "failed"


async def on_message_failed(ctx, payload: dict) -> None:
    """``message.failed`` — o provedor recusou a entrega (fonte 3).

    O bus é fire-and-forget e não possui outbox durável: uma falha de transporte
    nesta ocorrência não é confirmada/persistida por :func:`deliver`, portanto
    uma NOVA falha do mesmo código tentará novamente. Reentrega confirmada do
    mesmo receipt continua descartada para não duplicar alerta.
    """
    payload = payload or {}
    try:
        # O core snapshotou isto ANTES do fan-out fire-and-forget. Não reconsultar
        # a row aqui: ela pode surgir entre o emit e este handler, e o retry que a
        # pinta não reemite o bus. Campo ausente (core antigo) é conservadoramente
        # tratado como falha real; a agregação abaixo absorve eventual reentrega.
        if payload.get("is_redelivery") is True:
            return

        desc = describe_failure_event(payload)
        if desc is None:
            return
        channel_id = payload.get("channel_id") or ""
        # O evento é de TODO canal (o bus não filtra por provider): só alertamos
        # quando a falha é de um canal Cloud — é a conta Meta que este plugin
        # monitora.
        if not channel_id or not await asyncio.to_thread(
                _is_cloud_channel, channel_id):
            return
        await deliver(desc, channel_id)
    except Exception:  # noqa: BLE001
        logger.warning("whatsapp_cloud alert: falha ao tratar message.failed",
                       exc_info=True)


def _is_cloud_channel(channel_id: str) -> bool:
    try:
        row = channel_repo.get(channel_id) or {}
    except Exception:  # noqa: BLE001
        return False
    return (row.get("provider") or "") == PROVIDER


# ── Polling de qualidade (fonte 2 — independe de assinatura na Meta) ─────────

def _cloud_channels() -> list[dict]:
    try:
        rows = channel_repo.list_all()
    except Exception:  # noqa: BLE001
        return []
    return [{"id": r["id"], "own_phone": r.get("own_phone") or ""}
            for r in rows
            if r.get("provider") == PROVIDER and r.get("enabled", 1)]


def _live_quality(deps, channel_id: str) -> tuple[str, str]:
    """(quality_rating, own_phone) da instância viva do canal — best-effort."""
    registry = getattr(deps, "channel_registry", None)
    inst = registry.get(channel_id) if registry is not None else None
    if inst is None or not hasattr(inst, "status"):
        return "", ""
    try:
        st = inst.status() or {}
    except Exception:  # noqa: BLE001
        return "", ""
    return _s(st.get("quality_rating")), _s(st.get("own_phone"))


async def _quality_tick(deps) -> None:
    cfg = await asyncio.to_thread(alert_config)
    if not cfg["enabled"] or not cfg["token"] or not cfg["chat_id"]:
        return
    if not group_enabled(cfg["groups"], "quality"):
        return
    for ch in await asyncio.to_thread(_cloud_channels):
        cid = ch["id"]
        rating, phone = await asyncio.to_thread(_live_quality, deps, cid)
        if not rating:
            continue
        number = phone or ch["own_phone"]
        base_key = f"{cid}|quality_seen"
        state = await asyncio.to_thread(load_state, base_key)
        previous = _s(state.get("last_value"))
        if previous == rating:
            continue

        # A 1ª leitura é só baseline: instalar o plugin não deve anunciar uma
        # "mudança" que ele nunca observou.
        if not previous:
            await asyncio.to_thread(save_state, base_key, last_value=rating,
                                    last_alert_ts=time.time())
            continue

        desc = describe_quality_change(previous, rating, number=number)
        if desc is not None:
            action = await deliver(desc, cid)
            # ``quality_seen`` é o cursor da fonte, não uma tentativa. Avançá-lo
            # antes da confirmação do Telegram faria uma falha de transporte perder
            # o alerta para sempre, porque o próximo tick enxergaria valores iguais.
            if action in {"send", "edit"}:
                await asyncio.to_thread(
                    save_state, base_key, last_value=rating,
                    last_alert_ts=time.time())


async def quality_poll_loop(deps) -> None:
    """Task supervisionada: lê ``quality_rating`` do número e alerta na variação.

    Existe porque a fonte 1 (webhook) só funciona depois que os campos forem
    assinados no App Dashboard da Meta — esta não depende de nada (R3).
    """
    logger.info("whatsapp_cloud alert: loop de qualidade iniciado")
    stop_event = getattr(getattr(deps, "state", None), "stop_event", None)

    def _stopped() -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    while not _stopped():
        minutes = DEFAULT_QUALITY_POLL_MIN
        try:
            await _quality_tick(deps)
            minutes = (await asyncio.to_thread(alert_config))["quality_poll_min"]
        except Exception as e:  # noqa: BLE001 — um erro no ciclo nunca derruba o loop
            logger.warning("whatsapp_cloud alert: erro no ciclo de qualidade: %s", e)
        # stop_event é threading.Event (não asyncio): dorme em fatias curtas para
        # sair rápido no shutdown. A leitura de config fica dentro do try acima:
        # indisponibilidade transitória do DB usa o default em vez de derrubar o
        # loop e consumir o limite de restarts do supervisor.
        for _ in range(int(minutes) * 60):
            if _stopped():
                break
            await asyncio.sleep(1)
