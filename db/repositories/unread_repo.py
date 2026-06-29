"""Unread-tracking operations (plano 23 Fase E2).

Extracted from ``contact_repo`` so the unread bookkeeping — the denormalized
``contacts.unread_count`` / ``unread_ai_count`` / ``has_unread_mention`` columns
and the ``unread_msg_ids`` side-table that backs read receipts — lives in one
place. ``contact_repo`` keeps thin facades delegating here, so the public API
(``contact_repo.increment_unread`` etc.) is unchanged.

Single source of truth for the browser-tab badge count
(:func:`unread_conversation_count`): the LIVE implementation is contact-centric
(counts non-archived contacts whose denormalized unread counters are positive),
matching the route at ``server/routes/contacts.py``. The conversation-centric
variant that used to live in ``conversation_repo`` had zero callers and was
removed in this phase — switching to it would break the legacy suite, which
increments unread with a phantom msg_id that the ``unread_msg_ids ⋈ messages``
join would not match.
"""

from __future__ import annotations

import time

from sqlalchemy import case
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import contacts, unread_msg_ids


def increment_unread(contact_id: int, msg_id: str | None = None) -> None:
    """Increment unread_count and optionally track the msg_id."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_count=contacts.c.unread_count + 1,
            updated_at=time.time(),
        ))
        if msg_id:
            conn.execute(sa_insert(unread_msg_ids).values(
                contact_id=contact_id, msg_id=msg_id,
            ))


def increment_unread_ai(contact_id: int) -> None:
    """Increment unread_ai_count."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_ai_count=contacts.c.unread_ai_count + 1,
            updated_at=time.time(),
        ))


def mark_as_read(contact_id: int) -> list[str]:
    """Reset unread counts and return the unread msg_ids (for read receipts)."""
    with get_engine().begin() as conn:
        rows = conn.execute(
            select(unread_msg_ids.c.msg_id).where(unread_msg_ids.c.contact_id == contact_id)
        ).all()
        msg_ids = [r.msg_id for r in rows]
        conn.execute(sa_delete(unread_msg_ids).where(unread_msg_ids.c.contact_id == contact_id))
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_count=0,
            unread_ai_count=0,
            has_unread_mention=0,
            updated_at=time.time(),
        ))
    return msg_ids


def unread_conversation_count() -> int:
    """Number of non-archived conversations that have unread messages — used for the
    browser-tab badge (e.g. "(3) WhatsBot"). Counts a conversation once regardless of
    how many messages are unread, mirroring the sidebar badge visibility.

    Contact-centric (denormalized counters) — the single source of truth. The
    conversation-centric join variant was removed (dead, and incompatible with the
    phantom-msg_id increment in the legacy suite)."""
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(contacts).where(
                (contacts.c.is_archived == 0)
                & ((contacts.c.unread_count > 0) | (contacts.c.unread_ai_count > 0))
            )
        ).scalar() or 0


def set_mention(contact_id: int) -> None:
    """Raise the unread-mention flag (bot was @mentioned in a group). Shown as an
    "@" next to the unread badge until the operator opens the conversation."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            has_unread_mention=1,
            updated_at=time.time(),
        ))


def mark_as_unread(contact_id: int) -> None:
    """Mark a contact as unread by ensuring unread_count is at least 1.

    Only touches the in-app green badge; preserves an already-higher count.
    """
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_count=case(
                (contacts.c.unread_count < 1, 1),
                else_=contacts.c.unread_count,
            ),
            updated_at=time.time(),
        ))


def mark_all_as_unread() -> int:
    """Mark every conversation as unread (green badge).

    Only rows currently at 0 are touched, so existing higher counts are kept.
    Returns the number of conversations newly marked.
    """
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_update(contacts).where(contacts.c.unread_count < 1).values(
                unread_count=1,
                updated_at=time.time(),
            )
        )
    return result.rowcount or 0


def mark_all_as_read() -> int:
    """Reset unread counts for every conversation (clear all in-app badges).

    App-only: clears the tracked unread msg_ids too, but does not send WhatsApp
    read receipts. Returns the number of conversations that had unread badges.
    """
    with get_engine().begin() as conn:
        conn.execute(sa_delete(unread_msg_ids))
        result = conn.execute(
            sa_update(contacts)
            .where((contacts.c.unread_count > 0) | (contacts.c.unread_ai_count > 0)
                   | (contacts.c.has_unread_mention > 0))
            .values(unread_count=0, unread_ai_count=0, has_unread_mention=0, updated_at=time.time())
        )
    return result.rowcount or 0


def mark_user_messages_as_read(contact_id: int) -> list[str]:
    """Reset only unread_count (user messages) and return msg_ids for read receipts."""
    with get_engine().begin() as conn:
        rows = conn.execute(
            select(unread_msg_ids.c.msg_id).where(unread_msg_ids.c.contact_id == contact_id)
        ).all()
        msg_ids = [r.msg_id for r in rows]
        conn.execute(sa_delete(unread_msg_ids).where(unread_msg_ids.c.contact_id == contact_id))
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            unread_count=0,
            updated_at=time.time(),
        ))
    return msg_ids
