"""Repository for inbox membership (which agents see/receive an inbox).

A non-admin user without ``conversation.read_all`` only sees conversations of
the inboxes they are a member of (table ``inbox_members``). Managed from the
channel editor — one inbox per channel.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select

from db.engine import get_engine
from db.tables import inbox_members, users


def member_ids(inbox_id: int) -> list[int]:
    """User ids that are members of the inbox (sorted)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(inbox_members.c.user_id)
            .where(inbox_members.c.inbox_id == inbox_id)
            .order_by(inbox_members.c.user_id)
        ).all()
    return [r[0] for r in rows]


def inbox_ids_for_user(user_id: int) -> list[int]:
    """Inbox ids the user is a member of (drives conversation visibility)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(inbox_members.c.inbox_id)
            .where(inbox_members.c.user_id == user_id)
        ).all()
    return [r[0] for r in rows]


def set_members(inbox_id: int, user_ids: list[int]) -> list[int]:
    """Replace the inbox's member set. Only existing, active users are kept."""
    now = time.time()
    wanted = list(dict.fromkeys(int(u) for u in user_ids))  # dedupe, preserve order
    with get_engine().begin() as conn:
        if wanted:
            valid = {r[0] for r in conn.execute(
                select(users.c.id).where(users.c.id.in_(wanted)))}
            wanted = [u for u in wanted if u in valid]
        conn.execute(sa_delete(inbox_members).where(inbox_members.c.inbox_id == inbox_id))
        for uid in wanted:
            conn.execute(insert(inbox_members).values(
                inbox_id=inbox_id, user_id=uid, created_at=now))
    return member_ids(inbox_id)
