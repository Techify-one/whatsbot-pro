"""Repository for inboxes (plano 01 / plano 06).

Minimal for now — the canal/inbox management UI will grow this. Exposed so the
agent factory can honour ``inboxes.default_agent_key`` (precedência de roteamento
conversa → inbox → default).
"""

from __future__ import annotations

import time

from sqlalchemy import func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import channels, inboxes

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
                              channel_type: str = "whatsapp") -> dict | None:
    """Idempotent: return the channel's inbox, creating it on first use.

    Used by the runtime to resolve the inbox for an inbound event's channel
    even on installs predating the inbox-per-channel migration (defensive).

    Returns ``None`` when ``channel_id`` has no channel row (plano exclui-default):
    now that the default channel is deletable, a stale ``"default"`` fallback must
    NOT fabricate an orphan inbox pointing at a channel that no longer exists.
    """
    existing = get_by_channel(channel_id)
    if existing:
        return existing
    from db.repositories import channel_repo
    if channel_repo.get(channel_id) is None:
        return None
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


def delete(inbox_id: int) -> bool:
    """Hard-delete an inbox. FK ON DELETE CASCADE on ``conversations``,
    ``contact_inboxes`` and ``inbox_members`` carries the dependent rows.

    Only used on a genuine purge of a channel (plano inboxes/canais §4.3/§4.4) —
    the default channel-removal path is a soft-delete that keeps the inbox."""
    with get_engine().begin() as conn:
        res = conn.execute(sa_delete(inboxes).where(inboxes.c.id == inbox_id))
    return (res.rowcount or 0) > 0


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(inboxes).order_by(inboxes.c.id)).mappings().all()
    return [dict(r) for r in rows]


def list_with_channel(include_archived: bool = False) -> list[dict]:
    """Inboxes that still have a live channel, named by the channel's current
    ``display_name`` (plano inboxes/canais §4.6).

    Powers the UI lists (Usuários, Inboxes): the INNER JOIN drops órfãs (canal
    inexistente) and ``COALESCE`` surfaces the channel's atual nome em vez do
    ``inbox.name`` (snapshot velho). Archived channels are excluded by default."""
    stmt = (
        select(
            inboxes.c.id,
            func.coalesce(channels.c.display_name, inboxes.c.name).label("name"),
            inboxes.c.channel_id,
            inboxes.c.channel_type,
            inboxes.c.agent_bot_enabled,
            inboxes.c.default_agent_key,
            channels.c.provider.label("provider"),
            channels.c.enabled.label("channel_enabled"),
        )
        .select_from(inboxes.join(channels, channels.c.id == inboxes.c.channel_id))
    )
    if not include_archived:
        stmt = stmt.where(channels.c.archived == 0)
    stmt = stmt.order_by(channels.c.display_name, inboxes.c.id)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]
