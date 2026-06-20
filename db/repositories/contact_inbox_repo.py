"""Repository for contact_inboxes — a contact's identity within an inbox (plano 01)."""

from __future__ import annotations

import time

from sqlalchemy import select

from db.engine import get_engine
from db.tables import contact_inboxes
from db.upsert import upsert


def get_or_create(*, inbox_id: int, contact_id: int, source_id: str,
                  source_jid: str | None = None, source_lid: str | None = None) -> dict:
    """Idempotent on (inbox_id, source_id) — the unique resolution key (JID)."""
    now = time.time()
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(contact_inboxes).where(
                contact_inboxes.c.inbox_id == inbox_id,
                contact_inboxes.c.source_id == source_id)
        ).mappings().first()
        if existing:
            return dict(existing)
        conn.execute(upsert(
            contact_inboxes,
            {"inbox_id": inbox_id, "contact_id": contact_id, "source_id": source_id,
             "source_jid": source_jid or source_id, "source_lid": source_lid,
             "created_at": now, "updated_at": now},
            conflict_cols=["inbox_id", "source_id"],
            update_cols=["updated_at"],
        ))
        row = conn.execute(
            select(contact_inboxes).where(
                contact_inboxes.c.inbox_id == inbox_id,
                contact_inboxes.c.source_id == source_id)
        ).mappings().first()
    return dict(row)


def get(contact_inbox_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contact_inboxes).where(contact_inboxes.c.id == contact_inbox_id)
        ).mappings().first()
    return dict(row) if row else None
