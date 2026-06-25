"""Repository for saved_conversation_filters — named inbox filter presets per user.

``user_id`` scopes a preset to a logged-in RBAC user. In legacy single-password
mode there's no user identity, so presets are stored with ``user_id IS NULL`` and
shared on that install. All queries filter by the (possibly NULL) user_id so the
two modes never leak presets into each other.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import saved_conversation_filters as sf


def _user_match(user_id: int | None):
    """Build the scope predicate (NULL-safe equality for legacy mode)."""
    return sf.c.user_id.is_(None) if user_id is None else (sf.c.user_id == user_id)


def list_for_user(user_id: int | None) -> list[dict]:
    """Return all presets for this user, ordered by position then id."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(sf)
            .where(_user_match(user_id))
            .order_by(sf.c.position, sf.c.id)
        ).mappings().all()
    return [dict(r) for r in rows]


def get(filter_id: int, user_id: int | None) -> dict | None:
    """Fetch one preset, scoped to the owner (None if not found / not owned)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(sf).where(sf.c.id == filter_id, _user_match(user_id))
        ).mappings().first()
    return dict(row) if row else None


def create(user_id: int | None, name: str, spec: dict) -> dict:
    """Insert a new preset at the end of the user's list. Returns the full row."""
    now = time.time()
    with get_engine().begin() as conn:
        max_pos = conn.execute(
            select(func.coalesce(func.max(sf.c.position), -1)).where(_user_match(user_id))
        ).scalar_one()
        result = conn.execute(
            sf.insert().values(
                user_id=user_id, name=name, spec=spec,
                position=int(max_pos) + 1, created_at=now, updated_at=now,
            )
        )
        new_id = result.inserted_primary_key[0]
        row = conn.execute(select(sf).where(sf.c.id == new_id)).mappings().first()
    return dict(row)


def update(filter_id: int, user_id: int | None, *, name: str | None = None,
           spec: dict | None = None) -> dict | None:
    """Patch name and/or spec of an owned preset. Returns the row or None."""
    values: dict = {"updated_at": time.time()}
    if name is not None:
        values["name"] = name
    if spec is not None:
        values["spec"] = spec
    with get_engine().begin() as conn:
        res = conn.execute(
            sa_update(sf).where(sf.c.id == filter_id, _user_match(user_id)).values(**values)
        )
        if res.rowcount == 0:
            return None
        row = conn.execute(select(sf).where(sf.c.id == filter_id)).mappings().first()
    return dict(row) if row else None


def delete(filter_id: int, user_id: int | None) -> bool:
    """Delete an owned preset. Returns True if a row was removed."""
    with get_engine().begin() as conn:
        res = conn.execute(
            sa_delete(sf).where(sf.c.id == filter_id, _user_match(user_id))
        )
    return res.rowcount > 0
