"""UPSERT helper (Postgres ``INSERT ... ON CONFLICT``).

Plano 29 Eixo C: o app é Postgres-only, então o helper usa o ``insert`` do
dialect postgresql direto — o branch por dialeto (SQLite) morreu.

Usage:
    from db.upsert import upsert
    stmt = upsert(
        config,
        values={"key": "foo", "value": "bar"},
        conflict_cols=["key"],
        update_cols=["value"],
    )
    conn.execute(stmt)
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert


def upsert(
    table: Table,
    values: Mapping | Sequence[Mapping],
    conflict_cols: Sequence[str],
    update_cols: Iterable[str] | None = None,
):
    """Build an ``INSERT ... ON CONFLICT DO UPDATE`` statement.

    Args:
        table: SQLAlchemy ``Table`` object.
        values: One mapping for a single row, or a list of mappings for a batch.
        conflict_cols: Column names that form the conflict target.
        update_cols: Columns to overwrite on conflict. If ``None``, every column
            present in ``values`` except the conflict columns is updated.

    The returned statement is executable against the active engine.
    """
    stmt = insert(table).values(values)

    if update_cols is None:
        sample = values[0] if isinstance(values, (list, tuple)) else values
        update_cols = [c for c in sample.keys() if c not in conflict_cols]

    set_ = {c: getattr(stmt.excluded, c) for c in update_cols}
    return stmt.on_conflict_do_update(index_elements=list(conflict_cols), set_=set_)


def upsert_ignore(
    table: Table,
    values: Mapping | Sequence[Mapping],
    conflict_cols: Sequence[str],
):
    """``INSERT ... ON CONFLICT DO NOTHING`` — replaces ``INSERT OR IGNORE``."""
    stmt = insert(table).values(values)
    return stmt.on_conflict_do_nothing(index_elements=list(conflict_cols))
