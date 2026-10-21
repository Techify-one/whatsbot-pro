"""Repository for config key-value storage."""

from __future__ import annotations

import json

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from db.engine import get_engine
from db.tables import config
from db.upsert import upsert


def _decode(value):
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def get_all() -> dict:
    """Return all config key-value pairs as a dict (values JSON-decoded)."""
    with get_engine().connect() as conn:
        rows = conn.execute(select(config.c.key, config.c.value)).all()
    return {row.key: _decode(row.value) for row in rows}


def get(key: str, default=None):
    """Get a single config value by key."""
    with get_engine().connect() as conn:
        value = conn.execute(
            select(config.c.value).where(config.c.key == key)
        ).scalar_one_or_none()
    if value is None:
        return default
    return _decode(value)


def set(key: str, value) -> None:
    """Set a single config value (JSON-encoded)."""
    encoded = json.dumps(value, ensure_ascii=False)
    with get_engine().begin() as conn:
        conn.execute(upsert(
            config,
            {"key": key, "value": encoded},
            conflict_cols=["key"],
            update_cols=["value"],
        ))


def set_many(data: dict) -> None:
    """Set multiple config values at once."""
    if not data:
        return
    rows = [{"key": k, "value": json.dumps(v, ensure_ascii=False)} for k, v in data.items()]
    with get_engine().begin() as conn:
        for row in rows:
            conn.execute(upsert(
                config,
                row,
                conflict_cols=["key"],
                update_cols=["value"],
            ))


def advance_if_older(key: str, threshold: float, new_value: float) -> bool:
    """Atomically set a numeric ``key`` to ``new_value`` iff its current value is
    older than ``threshold`` (missing / non-numeric counts as 0). Returns whether
    THIS caller won the claim (plano 178).

    Built for "run this scheduled slot exactly once" across instances sharing one
    database: read, compare and write happen in ONE short transaction under a
    transaction-level advisory lock keyed on the config key, so two instances
    cannot both see the old value. ``pg_try_advisory_xact_lock`` (not the session
    flavour) on purpose: the lock dies with the transaction, which is the only kind
    that is safe behind PgBouncer in transaction mode. The loser does not wait: it
    gets ``False`` and the slot is already being served by the winner."""
    from sqlalchemy import func
    with get_engine().begin() as conn:
        if not conn.execute(select(func.pg_try_advisory_xact_lock(
                func.hashtext("config:" + key)))).scalar():
            return False
        raw = conn.execute(
            select(config.c.value).where(config.c.key == key)).scalar_one_or_none()
        try:
            current = float(_decode(raw)) if raw is not None else 0.0
        except (TypeError, ValueError):
            current = 0.0
        if current >= float(threshold):
            return False
        conn.execute(upsert(
            config, {"key": key, "value": json.dumps(float(new_value))},
            conflict_cols=["key"], update_cols=["value"]))
    return True

def delete_prefix(prefix: str) -> int:
    """Delete every config key starting with ``prefix``. Returns row count."""
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(config).where(config.c.key.like(prefix + "%")))
    return result.rowcount or 0
