"""Repository for conversations — the atendimento thread (plano 01 Fase 1).

display_id is a human-friendly sequential id allocated atomically from
conversation_counters in the SAME transaction as the INSERT (P6).
"""

from __future__ import annotations

import time

from sqlalchemy import select, update, func

from db.engine import get_engine
from db.tables import conversations, contacts, conversation_counters

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


def create(*, inbox_id: int, contact_id: int, contact_inbox_id: int,
           opened_at: float | None = None, ai_active: int = 1,
           is_archived: int = 0) -> dict:
    now = time.time()
    opened = opened_at if opened_at is not None else now
    with get_engine().begin() as conn:
        display_id = _next_display_id(conn)
        result = conn.execute(conversations.insert().values(
            display_id=display_id, inbox_id=inbox_id, contact_id=contact_id,
            contact_inbox_id=contact_inbox_id, status="open", is_archived=is_archived,
            ai_active=ai_active, opened_at=opened, last_activity_at=opened,
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


def resolve_for_contact(contact_id: int, jid: str, *, reopen_if_closed: bool = False,
                        opened_at: float | None = None) -> dict:
    """Return the active conversation for a contact, creating one if needed (plano 01 F2).

    Idempotent: get_or_creates the contact_inbox on the default inbox (JID = source_id,
    espelhando o backfill da migration 0013) e a conversa. Quando reopen_if_closed e a
    última conversa está closed, reabre — uma nova mensagem inbound reativa o atendimento.
    """
    from db.repositories import contact_inbox_repo
    ci = contact_inbox_repo.get_or_create(
        inbox_id=DEFAULT_INBOX_ID, contact_id=contact_id, source_id=jid, source_jid=jid)
    conv = get_latest_for_contact(contact_id)
    if conv is None:
        return create(inbox_id=DEFAULT_INBOX_ID, contact_id=contact_id,
                      contact_inbox_id=ci["id"], opened_at=opened_at)
    if reopen_if_closed and conv["status"] == "closed":
        conv = set_status(conv["id"], "open")
    return conv


def list_conversations(*, status: str | None = None, inbox_id: int | None = None,
                       assignee_user_id: int | None = None, is_archived: int | None = None,
                       limit: int = 100, offset: int = 0) -> list[dict]:
    """List conversations joined with basic contact info, newest activity first."""
    stmt = (
        select(
            conversations,
            contacts.c.name.label("contact_name"),
            contacts.c.phone.label("contact_phone"),
            contacts.c.is_group.label("contact_is_group"),
        )
        .select_from(conversations.join(contacts, contacts.c.id == conversations.c.contact_id))
    )
    if status is not None:
        stmt = stmt.where(conversations.c.status == status)
    if inbox_id is not None:
        stmt = stmt.where(conversations.c.inbox_id == inbox_id)
    if assignee_user_id is not None:
        stmt = stmt.where(conversations.c.assignee_user_id == assignee_user_id)
    if is_archived is not None:
        stmt = stmt.where(conversations.c.is_archived == is_archived)
    stmt = stmt.order_by(conversations.c.last_activity_at.desc()).limit(limit).offset(offset)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


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


def touch_activity(conv_id: int, ts: float | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(conversations.c.id == conv_id)
                     .values(last_activity_at=ts if ts is not None else time.time()))
