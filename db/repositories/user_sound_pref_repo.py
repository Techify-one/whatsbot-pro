"""Repository for user_sound_prefs — per-user notification-sound overrides (plano 63).

``prefs`` is a SPARSE override of the global ``config.sound_settings`` (only the
fields the user changed). ``user_id`` scopes it to a logged-in RBAC user; in open
mode (before the first admin) there's no identity, so the override is stored with
``user_id IS NULL`` and shared on that install. All queries filter by the
(possibly NULL) user_id so the two modes never leak into each other.

Molde: ``saved_filter_repo`` (NULL-safe ``_user_match``). The upsert uses
``INSERT ... ON CONFLICT (user_id)`` for real users; the NULL (open-mode) case is
handled manually because Postgres treats NULLs as distinct in a unique index.
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import user_sound_prefs as usp
from db.upsert import upsert as _upsert


def _user_match(user_id: int | None):
    """Scope predicate (NULL-safe equality for open mode)."""
    return usp.c.user_id.is_(None) if user_id is None else (usp.c.user_id == user_id)


def get(user_id: int | None) -> dict | None:
    """Return the sparse override dict for this user, or None if none saved."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(usp.c.prefs).where(_user_match(user_id)).order_by(usp.c.id)
        ).mappings().first()
    if not row:
        return None
    prefs = row["prefs"]
    return prefs if isinstance(prefs, dict) else None


def upsert(user_id: int | None, prefs: dict) -> dict:
    """Persist the sparse override for this user (create or overwrite). Returns ``prefs``."""
    now = time.time()
    if user_id is None:
        # Postgres treats NULLs as distinct in the unique index, so ON CONFLICT
        # won't dedupe the open-mode row — do a manual get-then-update/insert.
        with get_engine().begin() as conn:
            existing = conn.execute(
                select(usp.c.id).where(usp.c.user_id.is_(None)).order_by(usp.c.id)
            ).scalar_one_or_none()
            if existing is not None:
                conn.execute(
                    sa_update(usp).where(usp.c.id == existing)
                    .values(prefs=prefs, updated_at=now)
                )
            else:
                conn.execute(
                    usp.insert().values(user_id=None, prefs=prefs, updated_at=now)
                )
        return prefs
    with get_engine().begin() as conn:
        conn.execute(_upsert(
            usp,
            {"user_id": user_id, "prefs": prefs, "updated_at": now},
            conflict_cols=["user_id"],
            update_cols=["prefs", "updated_at"],
        ))
    return prefs
