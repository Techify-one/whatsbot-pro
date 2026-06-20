"""Repository for the quick_replies table (plano 04 — respostas rápidas).

Global single list (P42, no scope), plain text (P47). Mirrors tag_repo's
SQLAlchemy-Core style. ``short_code`` is stored WITHOUT the leading slash and
is UNIQUE global (P41).
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from db.engine import get_engine
from db.tables import quick_replies


def list_all() -> list[dict]:
    """Return the whole global list, ordered by short_code.

    Same list feeds the management screen AND the composer autocomplete.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(quick_replies).order_by(quick_replies.c.short_code)
        ).mappings().all()
    return [dict(r) for r in rows]


def get_by_id(qr_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(quick_replies).where(quick_replies.c.id == qr_id)
        ).mappings().first()
    return dict(row) if row else None


def get_by_short_code(short_code: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(quick_replies).where(quick_replies.c.short_code == short_code)
        ).mappings().first()
    return dict(row) if row else None


def exists(short_code: str, exclude_id: int | None = None) -> bool:
    """Friendly uniqueness check before insert/update (P41)."""
    stmt = select(quick_replies.c.id).where(quick_replies.c.short_code == short_code)
    if exclude_id is not None:
        stmt = stmt.where(quick_replies.c.id != exclude_id)
    with get_engine().connect() as conn:
        return conn.execute(stmt).first() is not None


def create(*, short_code: str, content: str) -> dict | None:
    """Create a quick reply. Returns the row, or None on uniqueness violation."""
    now = time.time()
    try:
        with get_engine().begin() as conn:
            if conn.execute(
                select(quick_replies.c.id).where(quick_replies.c.short_code == short_code)
            ).first() is not None:
                return None
            result = conn.execute(sa_insert(quick_replies).values(
                short_code=short_code, content=content,
                created_at=now, updated_at=now,
            ))
            new_id = result.inserted_primary_key[0]
    except IntegrityError:
        return None
    return get_by_id(new_id)


def update(qr_id: int, *, short_code: str | None = None, content: str | None = None) -> dict | None:
    """Update short_code and/or content. Returns the row, or None if not found
    or the new short_code collides with another row."""
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(quick_replies).where(quick_replies.c.id == qr_id)
        ).mappings().first()
        if existing is None:
            return None
        values: dict = {}
        if short_code is not None and short_code != existing["short_code"]:
            collide = conn.execute(
                select(quick_replies.c.id)
                .where(quick_replies.c.short_code == short_code)
                .where(quick_replies.c.id != qr_id)
            ).first()
            if collide is not None:
                return None
            values["short_code"] = short_code
        if content is not None:
            values["content"] = content
        if values:
            values["updated_at"] = time.time()
            try:
                conn.execute(sa_update(quick_replies).where(quick_replies.c.id == qr_id).values(**values))
            except IntegrityError:
                return None
    return get_by_id(qr_id)


def delete(qr_id: int) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(quick_replies).where(quick_replies.c.id == qr_id))
    return (result.rowcount or 0) > 0
