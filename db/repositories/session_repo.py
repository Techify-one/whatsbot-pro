"""Repository for opaque server-side user sessions (plano 03 Fase 3)."""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select, update

from db.engine import get_engine
from db.tables import user_sessions

SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def create(token: str, user_id: int, *, user_agent: str = "", ip: str = "",
           ttl: float = SESSION_TTL_SECONDS) -> dict:
    now = time.time()
    with get_engine().begin() as conn:
        conn.execute(insert(user_sessions).values(
            id=token, user_id=user_id, created_at=now, expires_at=now + ttl,
            last_seen_at=now, user_agent=user_agent[:255], ip=ip[:64],
        ))
    return {"id": token, "user_id": user_id, "expires_at": now + ttl}


def get_valid(token: str) -> dict | None:
    """Return the session if it exists and is not expired, else None."""
    if not token:
        return None
    now = time.time()
    with get_engine().connect() as conn:
        row = conn.execute(
            select(user_sessions).where(
                user_sessions.c.id == token, user_sessions.c.expires_at > now)
        ).mappings().first()
    return dict(row) if row else None


def touch(token: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(user_sessions).where(user_sessions.c.id == token)
                     .values(last_seen_at=time.time()))


def delete(token: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(sa_delete(user_sessions).where(user_sessions.c.id == token))


def delete_for_user(user_id: int) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(user_sessions).where(user_sessions.c.user_id == user_id))
    return result.rowcount or 0


def purge_expired() -> int:
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_delete(user_sessions).where(user_sessions.c.expires_at <= time.time()))
    return result.rowcount or 0
