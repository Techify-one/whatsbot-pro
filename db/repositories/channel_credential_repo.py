"""Repository for ``channel_credentials`` (plano 02 Fase 0).

Stores per-channel secrets in PLAIN TEXT (P15 — no encryption in the MVP). The
masking for the API boundary is applied in ``server/routes/channels.py``, never
here. Revisit (Fernet/pgcrypto) before serious production use.
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from db.engine import get_engine
from db.tables import channel_credentials as cc
from db.upsert import upsert


def get(channel_id: str, key: str) -> str | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(cc.c.value).where(cc.c.channel_id == channel_id, cc.c.key == key)
        ).first()
    return row[0] if row else None


def get_all(channel_id: str) -> dict[str, str]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(cc.c.key, cc.c.value).where(cc.c.channel_id == channel_id)
        ).all()
    return {k: v for k, v in rows}


def set(channel_id: str, key: str, value: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(upsert(
            cc,
            {"channel_id": channel_id, "key": key, "value": value},
            conflict_cols=["channel_id", "key"],
            update_cols=["value"],
        ))


def delete_all(channel_id: str) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(cc).where(cc.c.channel_id == channel_id))
    return result.rowcount or 0
