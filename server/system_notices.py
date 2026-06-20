"""Avisos de sistema no chat — eventos do ciclo de vida da conversa (plano 12).

Registra no FIO da conversa, como uma "mensagem de sistema" (igual ao card de
``tool_call``/``system_notice``), os eventos do atendimento: atribuição, tags,
status (abrir/fechar/reabrir/arquivar), IA, agente ativo, atributos — inclusive
as transições automáticas (cliente reabre conversa fechada, IA assume sozinha).

Arquitetura (ver plano 12 §1):

- ``EVENT_GROUPS``    — registry dos 4 grupos com a chave de config (gate global).
- ``EVENT_GROUP_OF``  — event_type -> grupo.
- ``FORMATTERS``      — event_type -> fn(**ctx) -> str (texto PT-BR, com autor).
- ``emit_conversation_notice`` — gate de config + formata + grava ``messages``
  (role ``conversation_event``, painel-only) + broadcast ``new_message``.

Painel-only: o aviso NUNCA é enviado ao WhatsApp. O gate é na GERAÇÃO (config
global): grupo desligado ⇒ no-op total (nada grava, nada emite). Extensível:
adicionar um tipo = nova entrada em ``FORMATTERS`` + ``EVENT_GROUP_OF``; grupo
novo = + chave em ``DEFAULT_CONFIG``/``allowed_keys``/``GET config`` + toggle UI.
"""

from __future__ import annotations

import logging

from db.repositories import config_repo, conversation_repo, message_repo
from plugins.context import broadcast

logger = logging.getLogger(__name__)

# Role especial da mensagem (D1). Renderiza como card centralizado e é excluído
# do contexto do LLM / preview da sidebar / contagem de não-lidas.
ROLE = "conversation_event"


# ── Registry de grupos (gate de config global) ───────────────────────────────
# group -> {config_key, default, label}. Todas as chaves default True.
EVENT_GROUPS: dict[str, dict] = {
    "assignment": {
        "config_key": "system_notice_assignment",
        "default": True,
        "label": "Atribuição",
    },
    "tags": {
        "config_key": "system_notice_tags",
        "default": True,
        "label": "Tags",
    },
    "status": {
        "config_key": "system_notice_status",
        "default": True,
        "label": "Status e arquivo",
    },
    "ai": {
        "config_key": "system_notice_ai",
        "default": True,
        "label": "IA e atributos",
    },
}

# event_type -> grupo (decide qual toggle controla a geração).
EVENT_GROUP_OF: dict[str, str] = {
    # assignment
    "assigned": "assignment",
    "assigned_me": "assignment",
    "unassigned": "assignment",
    # tags
    "tag_added": "tags",
    "tag_removed": "tags",
    # status & arquivo
    "status_closed": "status",
    "status_open": "status",
    "status_reopened_auto": "status",
    "archived": "status",
    "unarchived": "status",
    "created": "status",
    # ai & atributos
    "ai_on": "ai",
    "ai_off": "ai",
    "ai_takeover": "ai",
    "agent_changed": "ai",
    "attribute_set": "ai",
}


# ── Formatters (PT-BR, com autor quando houver) ──────────────────────────────
# Cada formatter aceita **kwargs e ignora o que não usa, para o call site poder
# passar contexto extra sem quebrar. ``actor`` = nome do operador (ou None para
# ações automáticas / instalações sem identidade de usuário).

def _q(value) -> str:
    return str(value if value is not None else "")


def _f_assigned(actor=None, target=None, **_) -> str:
    tgt = _q(target) or "alguém"
    if actor:
        return f"🧑‍💼 {actor} atribuiu a conversa para {tgt}."
    return f"🧑‍💼 Conversa atribuída para {tgt}."


def _f_assigned_me(actor=None, **_) -> str:
    if actor:
        return f"🧑‍💼 {actor} assumiu a conversa."
    return "🧑‍💼 Conversa assumida."


def _f_unassigned(actor=None, **_) -> str:
    if actor:
        return f"🧑‍💼 {actor} removeu a atribuição da conversa."
    return "🧑‍💼 Atribuição da conversa removida."


def _f_tag_added(actor=None, tag=None, **_) -> str:
    if actor:
        return f'🏷️ {actor} adicionou a tag "{_q(tag)}".'
    return f'🏷️ Tag "{_q(tag)}" adicionada.'


def _f_tag_removed(actor=None, tag=None, **_) -> str:
    if actor:
        return f'🏷️ {actor} removeu a tag "{_q(tag)}".'
    return f'🏷️ Tag "{_q(tag)}" removida.'


def _f_status_closed(actor=None, **_) -> str:
    if actor:
        return f"✅ {actor} resolveu a conversa."
    return "✅ Conversa resolvida."


def _f_status_open(actor=None, **_) -> str:
    if actor:
        return f"🔄 {actor} reabriu a conversa."
    return "🔄 Conversa reaberta."


def _f_status_reopened_auto(**_) -> str:
    return "🔄 Conversa reaberta automaticamente (cliente enviou mensagem)."


def _f_archived(actor=None, **_) -> str:
    if actor:
        return f"🗄️ {actor} arquivou a conversa."
    return "🗄️ Conversa arquivada."


def _f_unarchived(actor=None, **_) -> str:
    if actor:
        return f"🗄️ {actor} desarquivou a conversa."
    return "🗄️ Conversa desarquivada."


def _f_created(display_id=None, **_) -> str:
    if display_id:
        return f"💬 Conversa #{display_id} iniciada."
    return "💬 Conversa iniciada."


def _f_ai_on(actor=None, **_) -> str:
    if actor:
        return f"🤖 {actor} reativou a IA."
    return "🤖 IA reativada."


def _f_ai_off(actor=None, **_) -> str:
    if actor:
        return f"🤖 {actor} pausou a IA."
    return "🤖 IA pausada."


def _f_ai_takeover(**_) -> str:
    return "🤖 A IA assumiu o atendimento."


def _f_agent_changed(actor=None, agent=None, **_) -> str:
    name = _q(agent) or "padrão"
    if actor:
        return f'🤖 {actor} mudou o agente ativo para "{name}".'
    return f'🤖 Agente ativo alterado para "{name}".'


def _f_attribute_set(actor=None, attribute=None, value=None, count=None, **_) -> str:
    # Agregação simples (plano 12 §6): vários atributos numa requisição viram 1 card.
    if count and count > 1:
        if actor:
            return f"📋 {actor} atualizou {count} atributos da conversa."
        return f"📋 {count} atributos da conversa atualizados."
    if actor:
        return f'📋 {actor} definiu "{_q(attribute)}" como "{_q(value)}".'
    return f'📋 "{_q(attribute)}" definido como "{_q(value)}".'


FORMATTERS: dict[str, callable] = {
    "assigned": _f_assigned,
    "assigned_me": _f_assigned_me,
    "unassigned": _f_unassigned,
    "tag_added": _f_tag_added,
    "tag_removed": _f_tag_removed,
    "status_closed": _f_status_closed,
    "status_open": _f_status_open,
    "status_reopened_auto": _f_status_reopened_auto,
    "archived": _f_archived,
    "unarchived": _f_unarchived,
    "created": _f_created,
    "ai_on": _f_ai_on,
    "ai_off": _f_ai_off,
    "ai_takeover": _f_ai_takeover,
    "agent_changed": _f_agent_changed,
    "attribute_set": _f_attribute_set,
}


# ── Gate + resolução de alvo + emissão ───────────────────────────────────────

def _group_enabled(group: str) -> bool:
    """Config global: o grupo está habilitado? (default do registry se ausente)."""
    spec = EVENT_GROUPS.get(group)
    if spec is None:
        return False
    return bool(config_repo.get(spec["config_key"], spec["default"]))


def _resolve_target(conversation_id: int) -> tuple[int | None, str | None]:
    """(contact_id, phone) da conversa — para call sites que só têm o conv_id."""
    try:
        conv = conversation_repo.get_with_channel(conversation_id)
        if conv:
            return conv.get("contact_id"), conv.get("contact_phone")
    except Exception:
        logger.exception("[SystemNotice] _resolve_target falhou para conv %s", conversation_id)
    return None, None


def has_event(conversation_id: int, event_type: str) -> bool:
    """True se a conversa já tem um aviso ``event_type`` (dedupe de ``ai_takeover``)."""
    try:
        content = FORMATTERS[event_type]()
    except Exception:
        return False
    try:
        from sqlalchemy import select, func
        from db.engine import get_engine
        from db.tables import messages
        with get_engine().connect() as conn:
            n = conn.execute(
                select(func.count())
                .select_from(messages)
                .where(messages.c.conversation_id == conversation_id)
                .where(messages.c.role == ROLE)
                .where(messages.c.content == content)
            ).scalar()
        return bool(n)
    except Exception:
        logger.exception("[SystemNotice] has_event falhou (%s/%s)", conversation_id, event_type)
        return False


def emit_conversation_notice(*, event_type: str, conversation_id: int | None,
                             contact_id: int | None = None, phone: str | None = None,
                             **ctx) -> None:
    """Grava um aviso de sistema no fio da conversa e o emite ao vivo.

    1. gate: grupo do evento habilitado? (config global) — senão no-op total.
    2. ``content = FORMATTERS[event_type](**ctx)``; vazio ⇒ no-op.
    3. ``message_repo.add(contact_id, ROLE, content, conversation_id=...)`` (D4:
       conversation_id EXPLÍCITO — eventos como "fechada" deixam a conversa closed
       e ``get_open_for_contact`` não a acharia).
    4. broadcast ``new_message`` (keyed por phone) — render no card centralizado.

    Defensivo: qualquer exceção é logada e engolida — um aviso que falha NUNCA
    quebra a ação principal (plano 12 §0.1 / §6).
    """
    try:
        if conversation_id is None:
            return
        group = EVENT_GROUP_OF.get(event_type)
        if group is None:
            logger.warning("[SystemNotice] tipo de evento desconhecido: %s", event_type)
            return
        if not _group_enabled(group):
            return  # gate na geração — nada grava, nada emite
        formatter = FORMATTERS.get(event_type)
        if formatter is None:
            return
        content = formatter(**ctx)
        if not content:
            return
        if contact_id is None or phone is None:
            r_cid, r_phone = _resolve_target(conversation_id)
            contact_id = contact_id if contact_id is not None else r_cid
            phone = phone if phone is not None else r_phone
        if contact_id is None:
            logger.warning("[SystemNotice] sem contato p/ conv %s (%s)",
                           conversation_id, event_type)
            return
        msg = message_repo.add(contact_id, ROLE, content, conversation_id=conversation_id)
        broadcast("new_message", {
            "phone": phone,
            "message": {
                "role": ROLE,
                "content": content,
                "ts": msg["ts"],
                "conversation_id": conversation_id,
            },
        })
    except Exception:
        logger.exception("[SystemNotice] falha ao emitir aviso %s (conv %s)",
                         event_type, conversation_id)
