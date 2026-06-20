"""Repository for inboxes (plano 01 / plano 06).

Minimal for now — the canal/inbox management UI will grow this. Exposed so the
agent factory can honour ``inboxes.default_agent_key`` (precedência de roteamento
conversa → inbox → default).
"""

from __future__ import annotations

from sqlalchemy import select

from db.engine import get_engine
from db.tables import inboxes


def get(inbox_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(inboxes).where(inboxes.c.id == inbox_id)).mappings().first()
    return dict(row) if row else None


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(inboxes).order_by(inboxes.c.id)).mappings().all()
    return [dict(r) for r in rows]
