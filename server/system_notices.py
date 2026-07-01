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


# ── Registry de grupos + tipos (gate de config global) ───────────────────────
# Estes três dicts são as STORES do registry. Eram literais; agora são populados
# por ``_seed_core_notices`` (mais abaixo) via ``register_notice_group`` /
# ``register_notice`` — o MESMO mecanismo que um plugin usa para adicionar avisos
# próprios. O conteúdo final é idêntico ao das tabelas antigas (as goldens travam
# o texto). Permanecem públicos: outros módulos leem ``FORMATTERS`` etc.
#
#   EVENT_GROUPS:   group -> {config_key, default, label}  (config_key default
#                   = ``system_notice_<group>``; todos default True)
#   EVENT_GROUP_OF: event_type -> group (decide qual toggle controla a geração)
#   FORMATTERS:     event_type -> fn(**ctx) -> str (texto PT-BR, com autor)
EVENT_GROUPS: dict[str, dict] = {}
EVENT_GROUP_OF: dict[str, str] = {}
FORMATTERS: dict[str, callable] = {}


# ── Formatters (PT-BR, com autor quando houver) ──────────────────────────────
# Cada formatter aceita **kwargs e ignora o que não usa, para o call site poder
# passar contexto extra sem quebrar. ``actor`` = nome do operador (ou None para
# ações automáticas / instalações sem identidade de usuário).

def _q(value) -> str:
    return str(value if value is not None else "")


def _with_actor(actor, with_actor: str, without_actor: str) -> str:
    """Shared "<actor> did X" / fallback selector (plano 23 Fase C3 — DRY).

    The 14 actor-aware formatters all repeat the same shape: a PT-BR string that
    leads with the operator's name when ``actor`` is truthy, and an impersonal
    fallback otherwise. This collapses that branch. The produced TEXT is the
    SAME strings the inline ``if actor: ... return ...`` returned (the lifecycle
    goldens lock it byte-for-byte), so callers build both variants and let this
    pick. ``with_actor`` already has the actor interpolated by the caller.
    """
    return with_actor if actor else without_actor


def _f_assigned(actor=None, target=None, **_) -> str:
    tgt = _q(target) or "alguém"
    return _with_actor(
        actor,
        f"🧑‍💼 {actor} atribuiu o atendimento para {tgt}.",
        f"🧑‍💼 Atendimento atribuído para {tgt}.",
    )


def _f_assigned_me(actor=None, **_) -> str:
    return _with_actor(
        actor,
        f"🧑‍💼 {actor} assumiu o atendimento.",
        "🧑‍💼 Atendimento assumido.",
    )


def _f_unassigned(actor=None, **_) -> str:
    return _with_actor(
        actor,
        f"🧑‍💼 {actor} removeu a atribuição do atendimento.",
        "🧑‍💼 Atribuição do atendimento removida.",
    )


def _f_tag_added(actor=None, tag=None, **_) -> str:
    return _with_actor(
        actor,
        f'🏷️ {actor} adicionou a tag "{_q(tag)}".',
        f'🏷️ Tag "{_q(tag)}" adicionada.',
    )


def _f_tag_removed(actor=None, tag=None, **_) -> str:
    return _with_actor(
        actor,
        f'🏷️ {actor} removeu a tag "{_q(tag)}".',
        f'🏷️ Tag "{_q(tag)}" removida.',
    )


def _f_conv_label_added(actor=None, label=None, **_) -> str:
    return _with_actor(
        actor,
        f'🏷️ {actor} adicionou a etiqueta "{_q(label)}" ao atendimento.',
        f'🏷️ Etiqueta "{_q(label)}" adicionada ao atendimento.',
    )


def _f_conv_label_removed(actor=None, label=None, **_) -> str:
    return _with_actor(
        actor,
        f'🏷️ {actor} removeu a etiqueta "{_q(label)}" do atendimento.',
        f'🏷️ Etiqueta "{_q(label)}" removida do atendimento.',
    )


def _f_status_closed(actor=None, **_) -> str:
    return _with_actor(
        actor,
        f"✅ {actor} resolveu o atendimento.",
        "✅ Atendimento resolvido.",
    )


def _f_status_open(actor=None, **_) -> str:
    return _with_actor(
        actor,
        f"🔄 {actor} reabriu o atendimento.",
        "🔄 Atendimento reaberto.",
    )


def _f_status_reopened_auto(**_) -> str:
    return "🔄 Atendimento reaberto automaticamente (cliente enviou mensagem)."


def _f_status_reopened_auto_agent(**_) -> str:
    return "🔄 Atendimento reaberto automaticamente (resposta enviada)."


def _f_archived(actor=None, **_) -> str:
    return _with_actor(
        actor,
        f"🗄️ {actor} arquivou o atendimento.",
        "🗄️ Atendimento arquivado.",
    )


def _f_unarchived(actor=None, **_) -> str:
    return _with_actor(
        actor,
        f"🗄️ {actor} desarquivou o atendimento.",
        "🗄️ Atendimento desarquivado.",
    )


def _f_created(display_id=None, **_) -> str:
    if display_id:
        return f"💬 Atendimento #{display_id} iniciado."
    return "💬 Atendimento iniciado."


def _f_ai_on(actor=None, **_) -> str:
    return _with_actor(
        actor,
        f"🤖 {actor} reativou a IA.",
        "🤖 IA reativada.",
    )


def _f_ai_off(actor=None, **_) -> str:
    return _with_actor(
        actor,
        f"🤖 {actor} pausou a IA.",
        "🤖 IA pausada.",
    )


def _f_ai_takeover(**_) -> str:
    return "🤖 A IA assumiu o atendimento."


def _f_agent_changed(actor=None, agent=None, **_) -> str:
    name = _q(agent) or "padrão"
    return _with_actor(
        actor,
        f'🤖 {actor} mudou o agente ativo para "{name}".',
        f'🤖 Agente ativo alterado para "{name}".',
    )


def _f_attribute_set(actor=None, attribute=None, value=None, count=None, **_) -> str:
    # Agregação simples (plano 12 §6): vários atributos numa requisição viram 1 card.
    if count and count > 1:
        return _with_actor(
            actor,
            f"📋 {actor} atualizou {count} atributos do atendimento.",
            f"📋 {count} atributos do atendimento atualizados.",
        )
    return _with_actor(
        actor,
        f'📋 {actor} definiu "{_q(attribute)}" como "{_q(value)}".',
        f'📋 "{_q(attribute)}" definido como "{_q(value)}".',
    )


# ── Registry de avisos (plano 23 Fase C3 — extensível por plugin) ─────────────
#
# ``register_notice_group`` / ``register_notice`` são o ÚNICO ponto de mutação de
# ``EVENT_GROUPS`` / ``EVENT_GROUP_OF`` / ``FORMATTERS``. O core dogfooda os dois:
# ``_seed_core_notices`` abaixo registra os 5 grupos e os 19 tipos por aqui (em
# vez de literais de dict), e um plugin pode adicionar avisos próprios via
# ``plugins.context.register_notice``/``register_notice_group`` SEM dar patch nos
# dicts do core. O texto produzido permanece byte-idêntico (as goldens de
# lifecycle travam) — isto é refactor de plumbing, não de comportamento.

def register_notice_group(
    key: str, label: str, *, config_key: str | None = None, default: bool = True,
) -> None:
    """Registra um GRUPO de avisos (gate global de config).

    ``key`` é o identificador interno do grupo (ex: ``"assignment"``); ``label`` o
    rótulo PT-BR exibido na UI. ``config_key`` é a chave de config que liga/desliga
    o grupo — quando omitida, é auto-derivada como ``system_notice_<key>`` (mata o
    espelhamento manual da chave). ``default`` é o valor quando a chave está ausente
    no banco. Idempotente: registrar o mesmo grupo de novo sobrescreve.
    """
    EVENT_GROUPS[key] = {
        "config_key": config_key or f"system_notice_{key}",
        "default": bool(default),
        "label": label,
    }


def register_notice(event_type: str, group: str, formatter) -> None:
    """Registra um TIPO de aviso: liga ``event_type`` ao ``group`` + ``formatter``.

    ``formatter`` é ``fn(**ctx) -> str`` (texto PT-BR; string vazia ⇒ no-op). O
    ``group`` deve já ter sido registrado via :func:`register_notice_group`. Um
    plugin chama isto (via ``plugins.context.register_notice``) para adicionar
    avisos próprios sem tocar no core. Idempotente.
    """
    if group not in EVENT_GROUPS:
        logger.warning(
            "[SystemNotice] register_notice(%s): grupo %r desconhecido — "
            "registre o grupo primeiro via register_notice_group()",
            event_type, group,
        )
    EVENT_GROUP_OF[event_type] = group
    FORMATTERS[event_type] = formatter


def _seed_core_notices() -> None:
    """Registra os grupos + tipos do core pelo MESMO mecanismo dos plugins."""
    # Grupos (config_key auto-derivado = system_notice_<key>, exceto onde diverge).
    register_notice_group("assignment", "Atribuição")
    register_notice_group("tags", "Tags")
    register_notice_group("conv_labels", "Etiquetas do atendimento")
    register_notice_group("status", "Status e arquivo")
    register_notice_group("ai", "IA e atributos")
    # Tipos -> (grupo, formatter).
    register_notice("assigned", "assignment", _f_assigned)
    register_notice("assigned_me", "assignment", _f_assigned_me)
    register_notice("unassigned", "assignment", _f_unassigned)
    register_notice("tag_added", "tags", _f_tag_added)
    register_notice("tag_removed", "tags", _f_tag_removed)
    register_notice("conv_label_added", "conv_labels", _f_conv_label_added)
    register_notice("conv_label_removed", "conv_labels", _f_conv_label_removed)
    register_notice("status_closed", "status", _f_status_closed)
    register_notice("status_open", "status", _f_status_open)
    register_notice("status_reopened_auto", "status", _f_status_reopened_auto)
    register_notice("status_reopened_auto_agent", "status", _f_status_reopened_auto_agent)
    register_notice("archived", "status", _f_archived)
    register_notice("unarchived", "status", _f_unarchived)
    register_notice("created", "status", _f_created)
    register_notice("ai_on", "ai", _f_ai_on)
    register_notice("ai_off", "ai", _f_ai_off)
    register_notice("ai_takeover", "ai", _f_ai_takeover)
    register_notice("agent_changed", "ai", _f_agent_changed)
    register_notice("attribute_set", "ai", _f_attribute_set)


_seed_core_notices()


def _check_config_key_consistency() -> None:
    """Warn if a CORE notice group's config key isn't declared in CONFIG_KEYS.

    A notice group auto-declares its key as ``system_notice_<group>``. The backend
    single-source for that key (seed/GET/PUT) is ``config.settings.CONFIG_KEYS``.
    This guard makes the two impossible to silently drift: if a core group is added
    here without a matching CONFIG_KEYS entry, the key would never seed/expose and
    the toggle would be inert — flag it loudly at import. Plugin groups are exempt
    (they manage their own ``plugin.<id>.*`` settings, not core CONFIG_KEYS)."""
    try:
        from config.settings import system_notice_config_keys
        declared = system_notice_config_keys()
        core_keys = {spec["config_key"] for spec in EVENT_GROUPS.values()
                     if spec["config_key"].startswith("system_notice_")}
        missing = core_keys - declared
        if missing:
            logger.warning(
                "[SystemNotice] grupos sem chave em CONFIG_KEYS (toggle inerte): %s",
                sorted(missing),
            )
    except Exception:
        logger.debug("[SystemNotice] config-key consistency check skipped", exc_info=True)


_check_config_key_consistency()


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


def resolve_conversation_for_contact(contact_id: int,
                                     inbox_id: int | None = None) -> dict | None:
    """Resolve the contact's conversation to anchor a notice on (open → latest).

    Prefers the contact's OPEN conversation; falls back to the most recent one
    (events like "fechada" leave the conversation closed). Returns ``None`` when
    the contact has no conversation. Best-effort — never raises.

    ``inbox_id`` restringe a resolução àquela caixa (plano 11): num contato com
    conversas em vários canais, o caminho contact-scoped fundiria os canais e
    ancoraria o aviso na thread errada. Quando informado, usa as variantes
    ``*_for_contact_inbox``; ausente mantém o comportamento contact-scoped legado."""
    try:
        if inbox_id is not None:
            conv = conversation_repo.get_open_for_contact_inbox(contact_id, inbox_id)
            if conv is None:
                conv = conversation_repo.get_latest_for_contact_inbox(contact_id, inbox_id)
            return conv
        conv = conversation_repo.get_open_for_contact(contact_id)
        if conv is None:
            conv = conversation_repo.get_latest_for_contact(contact_id)
        return conv
    except Exception:
        logger.exception("[SystemNotice] resolve_conversation_for_contact falhou (%s)",
                         contact_id)
        return None


def emit_for_contact(*, event_type: str, contact_id: int, phone: str | None = None,
                     inbox_id: int | None = None, **ctx) -> dict | None:
    """Resolve the contact's conversation (open → latest) and emit a notice on it.

    Convenience over :func:`emit_conversation_notice` for call sites that only
    have a contact: it resolves the anchor conversation, emits the notice, and
    returns the resolved conversation (so the caller can chain further per-conv
    actions). Returns ``None`` when no conversation exists (nothing emitted).
    Never raises — a failed notice never breaks the action. ``inbox_id`` restringe
    a resolução à caixa daquele canal (multicanal — ver
    :func:`resolve_conversation_for_contact`)."""
    conv = resolve_conversation_for_contact(contact_id, inbox_id)
    if conv is None:
        return None
    emit_conversation_notice(
        event_type=event_type, conversation_id=conv.get("id"),
        contact_id=contact_id, phone=phone, **ctx)
    return conv


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
