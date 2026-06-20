"""Repository for ``channels`` (plano 02 Fase 0).

Core access layer for channel rows. Providers read/write via the
``ChannelRegistry`` (P24), not by importing this directly.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import channels

_STATUS_FIELDS = ("connected", "logged_in", "own_phone", "last_error",
                  "enabled", "display_name", "config", "gowa_device_id",
                  "gowa_isolation", "provider")


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(channels).order_by(channels.c.id)).mappings().all()
    return [dict(r) for r in rows]


def get(channel_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(channels).where(channels.c.id == channel_id)
        ).mappings().first()
    return dict(row) if row else None


def create(*, id: str, provider: str, display_name: str = "", enabled: int = 1,
           gowa_device_id: str | None = None, gowa_isolation: str = "shared",
           config: str | None = None) -> dict:
    now = time.time()
    with get_engine().begin() as conn:
        conn.execute(sa_insert(channels).values(
            id=id, provider=provider, display_name=display_name, enabled=enabled,
            gowa_device_id=gowa_device_id, gowa_isolation=gowa_isolation,
            config=config, connected=0, logged_in=0, created_at=now, updated_at=now,
        ))
    return get(id)


def update(channel_id: str, **fields) -> dict | None:
    return set_status(channel_id, **fields)


def set_status(channel_id: str, **fields) -> dict | None:
    values = {k: v for k, v in fields.items() if k in _STATUS_FIELDS}
    if not values:
        return get(channel_id)
    values["updated_at"] = time.time()
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(channels).where(channels.c.id == channel_id).values(**values)
        )
    return get(channel_id)


def delete(channel_id: str) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(channels).where(channels.c.id == channel_id))
    return (result.rowcount or 0) > 0
