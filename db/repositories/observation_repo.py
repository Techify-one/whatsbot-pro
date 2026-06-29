"""Observation (per-contact free-text note) CRUD (plano 23 Fase E2).

Extracted from ``contact_repo`` so the ``observations`` table access lives in its
own module. ``contact_repo`` keeps thin facades (``get_observations``,
``set_observations``, ``add_observation``) delegating here — public API unchanged.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select

from db.engine import get_engine
from db.tables import observations


def get_observations(contact_id: int) -> list[str]:
    """Return all observations for a contact."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(observations.c.text)
            .where(observations.c.contact_id == contact_id)
            .order_by(observations.c.created_at)
        ).all()
    return [r.text for r in rows]


def set_observations(contact_id: int, observations_list: list[str]) -> None:
    """Replace all observations for a contact."""
    now = time.time()
    cleaned = [t for t in observations_list if t.strip()]
    with get_engine().begin() as conn:
        conn.execute(sa_delete(observations).where(observations.c.contact_id == contact_id))
        if cleaned:
            conn.execute(sa_insert(observations), [
                {"contact_id": contact_id, "text": t, "created_at": now} for t in cleaned
            ])


def add_observation(contact_id: int, text: str) -> None:
    """Append a single observation if it doesn't already exist."""
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(observations.c.id).where(
                (observations.c.contact_id == contact_id) & (observations.c.text == text)
            )
        ).first()
        if existing:
            return
        conn.execute(sa_insert(observations).values(
            contact_id=contact_id, text=text, created_at=time.time()
        ))
