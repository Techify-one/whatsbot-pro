"""Repository for inboxes (plano 01 / plano 06).

Minimal for now — the canal/inbox management UI will grow this. Exposed so the
agent factory can honour ``inboxes.default_agent_key`` (precedência de roteamento
conversa → inbox → default).
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import inboxes

_UPDATABLE = ("name", "channel_id", "agent_bot_enabled", "default_agent_key")


def get(inbox_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(inboxes).where(inboxes.c.id == inbox_id)).mappings().first()
    return dict(row) if row else None


def update(inbox_id: int, **fields) -> dict | None:
    values = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not values:
        return get(inbox_id)
    values["updated_at"] = time.time()
    with get_engine().begin() as conn:
        conn.execute(sa_update(inboxes).where(inboxes.c.id == inbox_id).values(**values))
    return get(inbox_id)


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(inboxes).order_by(inboxes.c.id)).mappings().all()
    return [dict(r) for r in rows]
