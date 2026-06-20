"""Repository for inboxes (plano 01 / plano 06).

Minimal for now — the canal/inbox management UI will grow this. Exposed so the
agent factory can honour ``inboxes.default_agent_key`` (precedência de roteamento
conversa → inbox → default).
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import inboxes

_UPDATABLE = ("name", "channel_id", "agent_bot_enabled", "default_agent_key")


def get(inbox_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(inboxes).where(inboxes.c.id == inbox_id)).mappings().first()
    return dict(row) if row else None


def get_by_channel(channel_id: str) -> dict | None:
    """The inbox that owns ``channel_id`` (plano 11 Fase 1).

    Each channel maps to exactly one inbox (``inboxes.channel_id`` unique in
    practice; if duplicates exist we take the lowest id deterministically).
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(inboxes).where(inboxes.c.channel_id == channel_id)
            .order_by(inboxes.c.id)
        ).mappings().first()
    return dict(row) if row else None


def create(*, channel_id: str, name: str = "WhatsApp",
           channel_type: str = "whatsapp", agent_bot_enabled: int = 1,
           default_agent_key: str | None = None) -> dict:
    now = time.time()
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(inboxes).values(
            name=name, channel_type=channel_type, channel_id=channel_id,
            agent_bot_enabled=agent_bot_enabled, default_agent_key=default_agent_key,
            created_at=now, updated_at=now,
        ))
        inbox_id = result.inserted_primary_key[0]
    return get(inbox_id)


def get_or_create_for_channel(channel_id: str, *, name: str = "",
                              channel_type: str = "whatsapp") -> dict:
    """Idempotent: return the channel's inbox, creating it on first use.

    Used by the runtime to resolve the inbox for an inbound event's channel
    even on installs predating the inbox-per-channel migration (defensive).
    """
    existing = get_by_channel(channel_id)
    if existing:
        return existing
    return create(channel_id=channel_id, name=name or channel_id or "WhatsApp",
                  channel_type=channel_type)


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
