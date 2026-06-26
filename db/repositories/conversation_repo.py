"""Repository for conversations — the atendimento thread (plano 01 Fase 1).

display_id is a human-friendly sequential id allocated atomically from
conversation_counters in the SAME transaction as the INSERT (P6).
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select, update, func, delete as sa_delete, case, false as sa_false

from db.engine import get_engine
from db.tables import (conversations, contacts, conversation_counters, inboxes,
                       channels, messages, unread_msg_ids, conversation_label_links)

logger = logging.getLogger(__name__)

_COUNTER = "conversation_display_id"
DEFAULT_INBOX_ID = 1  # inbox semeado na migration 0013


def _next_display_id(conn) -> int:
    """Atomically reserve the next display_id within `conn`'s transaction."""
    current = conn.execute(
        select(conversation_counters.c.next_value)
        .where(conversation_counters.c.name == _COUNTER)
    ).scalar()
    if current is None:
        current = 1
        conn.execute(conversation_counters.insert().values(
            name=_COUNTER, next_value=current + 1))
    else:
        conn.execute(update(conversation_counters)
                     .where(conversation_counters.c.name == _COUNTER)
                     .values(next_value=current + 1))
    return current


def _default_agent_key_for_inbox(inbox_id: int) -> str | None:
    """Agent a brand-new conversation is bound to so the panel shows "IA padrão"
    handling it from the start: the inbox's ``default_agent_key`` if configured,
    otherwise the global default AI agent. Best-effort — never blocks creation."""
    from db.repositories import inbox_repo, agent_repo
    try:
        inbox = inbox_repo.get(inbox_id)
        if inbox and inbox.get("default_agent_key"):
            return inbox["default_agent_key"]
    except Exception:
        logger.debug("conversation create: inbox default_agent_key lookup failed")
    return agent_repo.DEFAULT_AGENT_KEY


def _default_ai_enabled() -> bool:
    """Whether brand-new conversations start with the AI active (config-driven).

    Plano 17: the per-conversation ``ai_active`` seed comes from the global
    ``default_ai_enabled`` toggle (no longer from ``contacts.ai_enabled``).
    Best-effort — defaults to True so a config read failure never silences the AI.
    """
    from db.repositories import config_repo
    try:
        return bool(config_repo.get("default_ai_enabled", True))
    except Exception:
        return True


def create(*, inbox_id: int, contact_id: int, contact_inbox_id: int,
           opened_at: float | None = None, ai_active: int | None = None,
           is_archived: int = 0, active_agent_key: str | None = None) -> dict:
    now = time.time()
    opened = opened_at if opened_at is not None else now
    if ai_active is None:
        ai_active = 1 if _default_ai_enabled() else 0
    if active_agent_key is None:
        active_agent_key = _default_agent_key_for_inbox(inbox_id)
    with get_engine().begin() as conn:
        display_id = _next_display_id(conn)
        result = conn.execute(conversations.insert().values(
            display_id=display_id, inbox_id=inbox_id, contact_id=contact_id,
            contact_inbox_id=contact_inbox_id, status="open", is_archived=is_archived,
            ai_active=ai_active, active_agent_key=active_agent_key,
            opened_at=opened, last_activity_at=opened,
            custom_attributes={}, created_at=now, updated_at=now,
        ))
        conv_id = result.inserted_primary_key[0]
        row = conn.execute(
            select(conversations).where(conversations.c.id == conv_id)).mappings().first()
    return dict(row)


def get(conv_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations).where(conversations.c.id == conv_id)).mappings().first()
    return dict(row) if row else None


def get_open_for_contact(contact_id: int) -> dict | None:
    """Most recent open conversation for a contact (the active thread)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations)
            .where(conversations.c.contact_id == contact_id,
                   conversations.c.status == "open")
            .order_by(conversations.c.last_activity_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def get_latest_for_contact(contact_id: int) -> dict | None:
    """Most recent conversation for a contact, regardless of status."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations)
            .where(conversations.c.contact_id == contact_id)
            .order_by(conversations.c.last_activity_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def get_open_for_contact_inbox(contact_id: int, inbox_id: int) -> dict | None:
    """Most recent open conversation for a contact WITHIN one inbox (plano 11 F1).

    A conversa é por-canal: o mesmo contato/número tem threads separadas por inbox
    (uma inbox por canal). Resolução de saída/agente usa esta visão por-inbox.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations)
            .where(conversations.c.contact_id == contact_id,
                   conversations.c.inbox_id == inbox_id,
                   conversations.c.status == "open")
            .order_by(conversations.c.last_activity_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def get_latest_for_contact_inbox(contact_id: int, inbox_id: int) -> dict | None:
    """Most recent conversation for a contact WITHIN one inbox, any status."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations)
            .where(conversations.c.contact_id == contact_id,
                   conversations.c.inbox_id == inbox_id)
            .order_by(conversations.c.last_activity_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def resolve_for_contact_ex(contact_id: int, jid: str, *, reopen_if_closed: bool = False,
                           opened_at: float | None = None,
                           inbox_id: int = DEFAULT_INBOX_ID) -> tuple[dict, str | None]:
    """Like :func:`resolve_for_contact` but also reports the lifecycle transition.

    Returns ``(conv, event)`` where ``event`` is ``"created"`` (a brand-new
    conversation was opened), ``"reopened"`` (an existing closed conversation was
    reactivated by this inbound), or ``None`` (already-open conversation, no
    transition). Lets callers surface an automatic system notice (plano 12 §3)
    without re-querying the prior status.
    """
    from db.repositories import contact_inbox_repo
    ci = contact_inbox_repo.get_or_create(
        inbox_id=inbox_id, contact_id=contact_id, source_id=jid, source_jid=jid)
    conv = get_latest_for_contact_inbox(contact_id, inbox_id)
    if conv is None:
        return create(inbox_id=inbox_id, contact_id=contact_id,
                      contact_inbox_id=ci["id"], opened_at=opened_at), "created"
    if reopen_if_closed and conv["status"] == "closed":
        return set_status(conv["id"], "open"), "reopened"
    return conv, None


def resolve_for_contact(contact_id: int, jid: str, *, reopen_if_closed: bool = False,
                        opened_at: float | None = None,
                        inbox_id: int = DEFAULT_INBOX_ID) -> dict:
    """Return the active conversation for a contact IN a given inbox (plano 01 F2 / plano 11 F1).

    Idempotent: get_or_creates the contact_inbox on ``inbox_id`` (JID = source_id,
    espelhando o backfill da migration 0013) e a conversa. ``inbox_id`` é resolvido
    do canal de origem (uma inbox por canal); o default mantém o GOWA inalterado.
    Quando reopen_if_closed e a última conversa daquela inbox está closed, reabre —
    uma nova mensagem inbound reativa o atendimento, sem misturar canais.

    Thin wrapper sobre :func:`resolve_for_contact_ex` (descarta o flag de transição)
    — mantido para os call sites que só querem a conversa.
    """
    conv, _event = resolve_for_contact_ex(
        contact_id, jid, reopen_if_closed=reopen_if_closed,
        opened_at=opened_at, inbox_id=inbox_id)
    conv = dict(conv)
    conv["created"] = (_event == "created")
    return conv


# Roles excluded from the last-message preview (mirrors contact_repo.list_contacts).
_PREVIEW_EXCLUDED = ("transcription", "system_notice", "conversation_event", "system")


def _last_msg_subq(col):
    """Correlated scalar subquery: ``col`` of the latest visible msg of the conversation."""
    return (
        select(col)
        .where(messages.c.conversation_id == conversations.c.id)
        .where(messages.c.role.notin_(_PREVIEW_EXCLUDED))
        .order_by(messages.c.ts.desc())
        .limit(1)
        .scalar_subquery()
    )


def _enriched_columns() -> list:
    """Columns for a conversation-centric list: conv + contact + channel + preview
    + per-CONVERSA unread (plano 11 D1).

    Unread é DERIVADO de ``unread_msg_ids ⋈ messages.msg_id`` filtrando por
    ``conversation_id`` — assim o mesmo número em 2 canais tem badges independentes
    sem coluna denormalizada nova (os contadores por-contato seguem intactos).
    """
    unread_subq = (
        select(func.count())
        .select_from(unread_msg_ids.join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id))
        .where(messages.c.conversation_id == conversations.c.id)
        .scalar_subquery()
    )
    return [
        conversations,
        contacts.c.name.label("contact_name"),
        contacts.c.phone.label("contact_phone"),
        contacts.c.is_group.label("contact_is_group"),
        contacts.c.is_pinned.label("is_pinned"),
        contacts.c.has_unread_mention.label("has_unread_mention"),
        inboxes.c.channel_id.label("channel_id"),
        channels.c.provider.label("channel_provider"),
        channels.c.display_name.label("channel_name"),
        _last_msg_subq(messages.c.content).label("last_msg_content"),
        _last_msg_subq(messages.c.role).label("last_msg_role"),
        _last_msg_subq(messages.c.ts).label("last_msg_ts"),
        _last_msg_subq(messages.c.media_type).label("last_msg_media_type"),
        _last_msg_subq(messages.c.status).label("last_msg_status"),
        _last_msg_subq(messages.c.msg_id).label("last_msg_id"),
        unread_subq.label("unread_count"),
    ]


def _enriched_from():
    return (
        conversations
        .join(contacts, contacts.c.id == conversations.c.contact_id)
        .outerjoin(inboxes, inboxes.c.id == conversations.c.inbox_id)
        .outerjoin(channels, channels.c.id == inboxes.c.channel_id)
    )


def _finalize_conv(row) -> dict:
    """Shape a raw enriched row into the dict the sidebar/list consumes."""
    d = dict(row)
    mt = d.get("last_msg_media_type")
    content = d.get("last_msg_content")
    if content is None:
        preview = ""
    elif mt == "image":
        preview = (content or "")[:80] or "\U0001f4f7 Imagem"
    elif mt == "audio":
        preview = "\U0001f3a4 Áudio"
    else:
        preview = (content or "")[:80]
    d["last_message"] = preview
    d["last_message_role"] = d.get("last_msg_role") or ""
    d["last_message_ts"] = d.get("last_msg_ts") or 0
    d["last_message_status"] = d.get("last_msg_status") or ""
    d["last_message_msg_id"] = d.get("last_msg_id") or ""
    d["unread_count"] = int(d.get("unread_count") or 0)
    d["is_pinned"] = bool(d.get("is_pinned"))
    d["has_unread_mention"] = bool(d.get("has_unread_mention"))
    return d


def list_conversations(*, status: str | None = None, inbox_id: int | None = None,
                       assignee_user_id: int | None = None, is_archived: int | None = None,
                       inbox_ids: list[int] | None = None,
                       limit: int = 100, offset: int = 0) -> list[dict]:
    """List conversations with contact + channel info + last-message preview +
    per-conversation unread, pinned-first then newest. Feeds the conversa-cêntrica
    sidebar and the full-page conversation list (plano 11 D1).

    ``inbox_ids`` scopes visibility to a set of inboxes (inbox membership): ``None``
    means no scoping (sees all); an empty list means the user is a member of no
    inbox and sees nothing."""
    stmt = select(*_enriched_columns()).select_from(_enriched_from())
    if status is not None:
        stmt = stmt.where(conversations.c.status == status)
    if inbox_id is not None:
        stmt = stmt.where(conversations.c.inbox_id == inbox_id)
    if assignee_user_id is not None:
        stmt = stmt.where(conversations.c.assignee_user_id == assignee_user_id)
    if is_archived is not None:
        stmt = stmt.where(conversations.c.is_archived == is_archived)
    if inbox_ids is not None:
        stmt = stmt.where(conversations.c.inbox_id.in_(inbox_ids) if inbox_ids
                          else sa_false())
    stmt = (stmt.order_by(contacts.c.is_pinned.desc(),
                          conversations.c.last_activity_at.desc())
            .limit(limit).offset(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_finalize_conv(r) for r in rows]


def list_filtered(where, *, inbox_ids: list[int] | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
    """List conversations matching a pre-built (injection-safe) WHERE from db.filters.

    ``inbox_ids`` scopes by inbox membership (see :func:`list_conversations`)."""
    stmt = select(*_enriched_columns()).select_from(_enriched_from())
    if where is not None:
        stmt = stmt.where(where)
    if inbox_ids is not None:
        stmt = stmt.where(conversations.c.inbox_id.in_(inbox_ids) if inbox_ids
                          else sa_false())
    stmt = (stmt.order_by(contacts.c.is_pinned.desc(),
                          conversations.c.last_activity_at.desc())
            .limit(limit).offset(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_finalize_conv(r) for r in rows]


def get_with_channel(conv_id: int) -> dict | None:
    """One conversation enriched with contact + channel + preview + unread (plano 11).

    Used by the conversa-cêntrico chat endpoint to render the header (channel badge,
    contact name) without re-resolving by phone (which would fuse channels)."""
    stmt = (select(*_enriched_columns()).select_from(_enriched_from())
            .where(conversations.c.id == conv_id))
    with get_engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _finalize_conv(row) if row else None


def mark_conversation_read(conv_id: int) -> list[str]:
    """Clear unread for ONE conversation; return its unread msg_ids for read receipts.

    Deletes only the ``unread_msg_ids`` whose message belongs to this conversation
    and decrements the CONTACT's denormalized ``unread_count`` by that many (clamped
    at 0), so opening one channel's thread never clears another channel's badge
    (plano 11 D1). ``has_unread_mention`` (nível-contato no MVP) é preservado.
    """
    with get_engine().begin() as conn:
        conv = conn.execute(
            select(conversations.c.contact_id).where(conversations.c.id == conv_id)
        ).first()
        if conv is None:
            return []
        contact_id = conv.contact_id
        rows = conn.execute(
            select(unread_msg_ids.c.id, unread_msg_ids.c.msg_id)
            .select_from(unread_msg_ids.join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id))
            .where(unread_msg_ids.c.contact_id == contact_id)
            .where(messages.c.conversation_id == conv_id)
        ).all()
        row_ids = [r.id for r in rows]
        msg_ids = [r.msg_id for r in rows]
        n = len(row_ids)
        if n:
            conn.execute(sa_delete(unread_msg_ids).where(unread_msg_ids.c.id.in_(row_ids)))
            conn.execute(update(contacts).where(contacts.c.id == contact_id).values(
                unread_count=case((contacts.c.unread_count <= n, 0),
                                  else_=contacts.c.unread_count - n),
                updated_at=time.time(),
            ))
    return msg_ids


def unread_conversation_count() -> int:
    """Non-archived conversations with at least one unread message (browser-tab badge).

    Conversa-cêntrico: conta CONVERSAS (uma por canal), derivando unread de
    ``unread_msg_ids ⋈ messages.conversation_id`` — o mesmo número em 2 canais com
    não-lidas conta 2, não 1 (ao contrário do contador por-contato legado)."""
    unread_subq = (
        select(func.count())
        .select_from(unread_msg_ids.join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id))
        .where(messages.c.conversation_id == conversations.c.id)
        .scalar_subquery()
    )
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(conversations).where(
                conversations.c.is_archived == 0,
                unread_subq > 0,
            )
        ).scalar() or 0


def count(*, status: str | None = None) -> int:
    stmt = select(func.count()).select_from(conversations)
    if status is not None:
        stmt = stmt.where(conversations.c.status == status)
    with get_engine().connect() as conn:
        return conn.execute(stmt).scalar() or 0


def _update(conv_id: int, values: dict) -> dict | None:
    values = {**values, "updated_at": time.time()}
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(conversations.c.id == conv_id).values(**values))
    return get(conv_id)


def set_status(conv_id: int, status: str) -> dict | None:
    values = {"status": status}
    if status == "closed":
        values["resolved_at"] = time.time()
        # Resolving a conversation also drops the assignment so it leaves any
        # agent's queue and lands back as "Não atribuída".
        values["assignee_user_id"] = None
        values["active_agent_key"] = None
    elif status == "open":
        values["resolved_at"] = None
    return _update(conv_id, values)


def set_archived(conv_id: int, is_archived: int) -> dict | None:
    return _update(conv_id, {"is_archived": is_archived})


def set_assignee(conv_id: int, assignee_user_id: int | None) -> dict | None:
    return _update(conv_id, {"assignee_user_id": assignee_user_id})


def set_ai_active(conv_id: int, ai_active: int) -> dict | None:
    """Pause/resume the AI for a specific conversation (gate nível conversa)."""
    return _update(conv_id, {"ai_active": ai_active})


def set_conversation_ai(conv_id: int, active: int,
                        assignee_user_id: int | None = None) -> dict | None:
    """Toggle the AI for a conversation AND (re)assign it (plano 17).

    OFF (``active=0``): pausing the AI hands the conversation to the human who
    turned it off (``assignee_user_id``) — they are taking over, so the row lands
    in THEIR queue instead of "Não atribuídas". When no operator identity is
    available (legacy/open mode, ``assignee_user_id=None``) it falls back to
    unassigned, mirroring ``set_status('closed')``. The AI agent is cleared either
    way.
    ON (``active=1``) re-attributes it to the inbox's default AI agent and clears
    any human assignee (the bot is taking over), so the row leaves the fila
    immediately without waiting for the next inbound message."""
    if active:
        conv = get(conv_id)
        if conv is None:
            return None
        agent_key = _default_agent_key_for_inbox(conv.get("inbox_id"))
        return _update(conv_id, {
            "ai_active": 1, "active_agent_key": agent_key, "assignee_user_id": None})
    return _update(conv_id, {
        "ai_active": 0, "active_agent_key": None, "assignee_user_id": assignee_user_id})


def set_custom_attributes(conv_id: int, attrs: dict) -> dict | None:
    """Replace the conversation's custom_attributes JSON (reatribui o dict inteiro)."""
    return _update(conv_id, {"custom_attributes": dict(attrs or {})})


def set_agent(conv_id: int, agent_key: str | None) -> dict | None:
    """Bind a specific AI agent to this conversation (plano 06)."""
    return _update(conv_id, {"active_agent_key": agent_key})


def assign_agent(conv_id: int, *, assignee_user_id: int | None,
                 active_agent_key: str | None, ai_active: int | None = None) -> dict | None:
    """Unified assignment (plano 10): route a conversation to a HUMAN (set
    ``assignee_user_id``, clear the AI agent) or to an AI agent (set
    ``active_agent_key``, clear the human). One atomic write so the panel/bus get
    a single event. ``ai_active=None`` leaves the AI gate untouched."""
    values = {
        "assignee_user_id": assignee_user_id,
        "active_agent_key": active_agent_key,
    }
    if ai_active is not None:
        values["ai_active"] = ai_active
    return _update(conv_id, values)


def ensure_ai_agent(contact_id: int, agent_key: str) -> dict | None:
    """Attribute the contact's active conversation to the AI agent that is
    answering, so the inbox shows its assignee chip (e.g. "IA padrão") sempre que
    a IA responde — mesmo em conversas reabertas após resolução (que limpa o
    ``active_agent_key``) ou criadas antes do agente-padrão existir.

    No-op when a human has taken the chat over (``assignee_user_id`` set), when it
    is already bound to this same agent, or when the conversation is not open.
    Returns the updated conv only when it actually changed, so callers can
    broadcast a single assignment event."""
    conv = get_latest_for_contact(contact_id)
    if conv is None:
        return None
    if conv.get("status") != "open":
        return None
    if not conv.get("ai_active"):
        return None  # AI paused on this conversation (plano 17) — never re-attribute
    if conv.get("assignee_user_id") is not None:
        return None  # a person owns this chat — don't steal it for the AI
    if conv.get("active_agent_key") == agent_key:
        return None  # already attributed to this agent
    return _update(conv["id"], {"active_agent_key": agent_key})


def touch_activity(conv_id: int, ts: float | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(conversations.c.id == conv_id)
                     .values(last_activity_at=ts if ts is not None else time.time()))


def delete(conv_id: int) -> int | None:
    """Hard-delete a conversation and its messages (plano 16).

    Returns the deleted conversation's ``contact_id`` (truthy) or ``None`` if it
    did not exist. Steps, all in ONE transaction:

    1. Clear unread for this conversation FIRST (espelha :func:`mark_conversation_read`):
       ``unread_msg_ids`` has no ``conversation_id`` and ``contacts.unread_count`` is
       denormalized, so deleting the messages before clearing would orphan unread rows
       and inflate the badge.
    2. Delete ``messages WHERE conversation_id`` EXPLICITLY — the column has no real FK
       in SQLite (migration 0013 added it without one), so the declarative CASCADE does
       not fire. (In Postgres the FK may cascade; the explicit delete is then idempotent.)
    3. Delete ``conversation_label_links`` (defensive — FK may be missing in legacy SQLite).
    4. Delete the ``conversations`` row.
    """
    with get_engine().begin() as conn:
        row = conn.execute(
            select(conversations.c.contact_id).where(conversations.c.id == conv_id)
        ).first()
        if row is None:
            return None
        contact_id = row.contact_id
        unread_rows = conn.execute(
            select(unread_msg_ids.c.id)
            .select_from(unread_msg_ids.join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id))
            .where(unread_msg_ids.c.contact_id == contact_id)
            .where(messages.c.conversation_id == conv_id)
        ).all()
        n = len(unread_rows)
        if n:
            conn.execute(sa_delete(unread_msg_ids)
                         .where(unread_msg_ids.c.id.in_([r.id for r in unread_rows])))
            conn.execute(update(contacts).where(contacts.c.id == contact_id).values(
                unread_count=case((contacts.c.unread_count <= n, 0),
                                  else_=contacts.c.unread_count - n),
                updated_at=time.time(),
            ))
        conn.execute(sa_delete(messages).where(messages.c.conversation_id == conv_id))
        conn.execute(sa_delete(conversation_label_links)
                     .where(conversation_label_links.c.conversation_id == conv_id))
        conn.execute(sa_delete(conversations).where(conversations.c.id == conv_id))
    return contact_id
