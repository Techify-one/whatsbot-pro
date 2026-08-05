"""Contexto de avaliação — resolve cada campo do catálogo a partir dos repos do core.

Sem rede: só leitura de banco (`conversation_repo`, `contact_repo`, `message_repo`,
`conversation_label_repo`, `tag_repo`). Roda em thread (o dispatcher chama de
`asyncio.to_thread`) e no bus de eventos (que já entrega handlers sync em thread).

O dict devolvido tem duas famílias de chaves:

* os **ids do catálogo** (`chatwoot.status`, `modulo.horas_desde_ultimo_contato`, …) —
  é o que `rules.avaliar` consome
* três chaves reservadas com ``__`` (`__now`, `__tz_offset_hours`, `__last_client_ts`)
  usadas por `rules.proximo_instante`

`build_ctx` também devolve o "meta" (conversa/contato frescos) para o dispatcher e as
ações reusarem sem consultar de novo — o canal SEMPRE sai daqui (autoritativo), nunca do
snapshot gravado no controle (precedente `agendamento_retorno/logic.py:399`).

Ao final entram os campos contribuídos por OUTROS plugins (`contrib.py`, seam
`filter.retornos.contexto`). Eles NUNCA sobrescrevem uma chave do core, e um plugin
quebrado/ausente simplesmente não acrescenta nada.
"""

from __future__ import annotations

import logging
import time

from . import catalog, contrib, rules

logger = logging.getLogger("plugin.retornos")


def _tz_of(configuracao) -> float:
    try:
        return float((configuracao or {}).get("tz_offset_hours", -3.0) or 0.0)
    except (TypeError, ValueError):
        return -3.0


def load_target(*, conversation_id: int) -> tuple[dict | None, dict | None]:
    """``(conversa enriquecida, contato)`` frescos — ou ``(None, None)``."""
    from db.repositories import contact_repo, conversation_repo
    try:
        conv = conversation_repo.get_with_channel(int(conversation_id))
    except Exception:  # noqa: BLE001
        logger.debug("retornos: get_with_channel falhou (conv=%s)", conversation_id,
                     exc_info=True)
        return None, None
    if not conv:
        return None, None
    contact = None
    try:
        if conv.get("contact_id") is not None:
            contact = contact_repo.get(int(conv["contact_id"]))
    except Exception:  # noqa: BLE001
        logger.debug("retornos: contact_repo.get falhou", exc_info=True)
    return conv, contact


def build_ctx(*, conv: dict, contact: dict | None, configuracao: dict | None,
              controle: dict | None = None, now: float | None = None,
              extra: dict | None = None) -> dict:
    """Monta o contexto de avaliação de UMA conversa (sem rede).

    ``extra`` = valores dos campos de outros plugins já resolvidos pelo chamador. Quem
    roda na thread do event loop (as rotas) PRECISA passá-lo — lá o seam síncrono é
    inerte; ``None`` resolve pelo caminho síncrono (dispatcher/eventos, em thread).
    """
    from db.repositories import conversation_label_repo, message_repo, tag_repo

    agora = float(now if now is not None else time.time())
    tz = _tz_of(configuracao)
    conv_id = conv.get("id")

    try:
        etiquetas = conversation_label_repo.get_names_for_conversation(int(conv_id))
    except Exception:  # noqa: BLE001
        etiquetas = []
    try:
        tags = tag_repo.get_contact_tags(int(conv["contact_id"])) if conv.get("contact_id") else []
    except Exception:  # noqa: BLE001
        tags = []

    last_client = (controle or {}).get("last_client_ts")
    if last_client is None:
        try:
            last_client = message_repo.last_inbound_ts(conversation_id=int(conv_id))
        except Exception:  # noqa: BLE001
            last_client = None

    local = rules.local_dt(agora, tz)
    # weekday(): 0=segunda … 6=domingo → catálogo usa 0=domingo … 6=sábado (convenção JS,
    # que é a exibida na UI).
    dia_semana = (local.weekday() + 1) % 7

    ctx: dict = {
        # ── Atendimento ──
        "chatwoot.agente_id": _str_or_none(conv.get("assignee_user_id")),
        "chatwoot.status": conv.get("status"),
        "chatwoot.etiquetas": list(etiquetas or []),
        "contato.tags": list(tags or []),
        "chatwoot.criado_em": conv.get("created_at") or conv.get("opened_at"),
        "chatwoot.ultima_atividade": conv.get("last_activity_at"),
        "wb.canal": conv.get("channel_id") or "default",
        "wb.contact_type": (contact or {}).get("contact_type") or "outros",
        "wb.agente_ia": conv.get("active_agent_key"),
        "wb.ia_ativa": "sim" if _int(conv.get("ai_active")) == 1 else "nao",
        "wb.grupo": "sim" if _int((contact or {}).get("is_group")) == 1 else "nao",
        # ── Virtual (relógio na tz da configuração) ──
        "virtual.hora_atual": local.hour * 60 + local.minute,
        "virtual.data_atual": agora,
        "virtual.dia_semana": str(dia_semana),
        # ── Configuração ──
        "modulo.n_disparos_enviados": _int((controle or {}).get("disparos_enviados")),
        "modulo.hora_ultimo_contato": None,
        "modulo.minutos_desde_ultimo_contato": None,
        "modulo.horas_desde_ultimo_contato": None,
        "modulo.dias_desde_ultimo_contato": None,
        "modulo.dias_calendario_desde_contato": None,
        # ── Reservadas ──
        rules.CTX_NOW: agora,
        rules.CTX_TZ: tz,
        rules.CTX_LAST_CLIENT: float(last_client) if last_client else None,
    }

    if last_client:
        lc = float(last_client)
        local_lc = rules.local_dt(lc, tz)
        delta = max(0.0, agora - lc)
        ctx["modulo.hora_ultimo_contato"] = local_lc.hour * 60 + local_lc.minute
        ctx["modulo.minutos_desde_ultimo_contato"] = delta / 60.0
        ctx["modulo.horas_desde_ultimo_contato"] = delta / 3600.0
        ctx["modulo.dias_desde_ultimo_contato"] = delta / 86400.0
        ctx["modulo.dias_calendario_desde_contato"] = round(
            (rules.epoch_of_local_midnight(agora, tz)
             - rules.epoch_of_local_midnight(lc, tz)) / 86400.0)

    if extra is None:
        extra = contrib.contexto(conv=conv, contact=contact, configuracao=configuracao,
                                 controle=controle, now=agora)
    for chave, valor in (extra or {}).items():
        if str(chave).startswith("__") or catalog.campo_core(str(chave)) is not None:
            continue  # chave reservada / campo do core: o core sempre ganha
        ctx[str(chave)] = valor
    return ctx


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _str_or_none(v):
    return None if v is None else str(v)
