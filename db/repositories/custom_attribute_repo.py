"""Repository for custom attribute definitions + values (plano 05).

Definitions live in ``custom_attribute_definitions`` (soft-deleted via deleted_at,
P49). Values live in a per-entity native-JSON column (``<entity>.custom_attributes``).

Mutation-tracking rule: JSON/JSONB do not track in-place dict mutation. set_values
ALWAYS reassigns the whole dict in the UPDATE — never ``obj["k"] = v``.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import custom_attribute_definitions as cad

# Fields the client may set on create; attribute_key/type/applies_to are identity
# (settable on create, immutable on update).
_EDITABLE = ("display_name", "options", "required", "description",
             "regex_pattern", "regex_cue", "position")


# ── Definitions ──────────────────────────────────────────────────────────

def list_definitions(applies_to: str | None = None, include_deleted: bool = False) -> list[dict]:
    stmt = select(cad)
    if not include_deleted:
        stmt = stmt.where(cad.c.deleted_at.is_(None))
    if applies_to:
        stmt = stmt.where(cad.c.applies_to == applies_to)
    stmt = stmt.order_by(cad.c.position, cad.c.id)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def get_definition(def_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(cad).where(cad.c.id == def_id)).mappings().first()
    return dict(row) if row else None


def get_definitions_map(applies_to: str) -> dict[str, dict]:
    """key -> active definition (used for validation on value writes)."""
    return {d["attribute_key"]: d for d in list_definitions(applies_to)}


def definition_exists(attribute_key: str, applies_to: str, exclude_id: int | None = None) -> bool:
    """Active definition with this (key, scope)? Used for friendly uniqueness."""
    stmt = select(cad.c.id).where(
        cad.c.attribute_key == attribute_key,
        cad.c.applies_to == applies_to,
        cad.c.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(cad.c.id != exclude_id)
    with get_engine().connect() as conn:
        return conn.execute(stmt).first() is not None


def create_definition(*, attribute_key: str, display_name: str, type: str = "text",
                       applies_to: str = "contact", options=None, required: int = 0,
                       description: str = "", regex_pattern=None, regex_cue=None,
                       position: int = 0, created_by=None) -> dict | None:
    """Create a definition. Returns the row, or None on (key, applies_to) collision."""
    if definition_exists(attribute_key, applies_to):
        return None
    now = time.time()
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(cad).values(
            attribute_key=attribute_key, display_name=display_name, type=type,
            applies_to=applies_to, options=options, required=required,
            description=description, regex_pattern=regex_pattern, regex_cue=regex_cue,
            position=position, created_by=created_by, created_at=now, deleted_at=None,
        ))
        new_id = result.inserted_primary_key[0]
    return get_definition(new_id)


def update_definition(def_id: int, **fields) -> dict | None:
    """Update editable fields only (NOT attribute_key/type/applies_to). Returns row or None."""
    values = {k: v for k, v in fields.items() if k in _EDITABLE and v is not None}
    if not values:
        return get_definition(def_id)
    with get_engine().begin() as conn:
        exists = conn.execute(
            select(cad.c.id).where(cad.c.id == def_id, cad.c.deleted_at.is_(None))
        ).first()
        if exists is None:
            return None
        conn.execute(sa_update(cad).where(cad.c.id == def_id).values(**values))
    return get_definition(def_id)


def delete_definition(def_id: int) -> bool:
    """Soft-delete (P49): set deleted_at; values stay in the entity JSON."""
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_update(cad)
            .where(cad.c.id == def_id, cad.c.deleted_at.is_(None))
            .values(deleted_at=time.time())
        )
    return (result.rowcount or 0) > 0


def purge_orphan_values(entity_table, applies_to: str) -> int:
    """Remove from each entity's JSON the keys with no active definition (P49).

    Admin batch op. Returns the number of rows touched.
    """
    active = set(get_definitions_map(applies_to).keys())
    touched = 0
    with get_engine().begin() as conn:
        rows = conn.execute(
            select(entity_table.c.id, entity_table.c.custom_attributes)
        ).mappings().all()
        for r in rows:
            attrs = r["custom_attributes"] or {}
            if not isinstance(attrs, dict):
                continue
            cleaned = {k: v for k, v in attrs.items() if k in active}
            if len(cleaned) != len(attrs):
                conn.execute(
                    sa_update(entity_table)
                    .where(entity_table.c.id == r["id"])
                    .values(custom_attributes=cleaned)
                )
                touched += 1
    return touched


# ── Values (generic per entity table) ────────────────────────────────────

def get_values(entity_table, entity_id: int) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(entity_table.c.custom_attributes).where(entity_table.c.id == entity_id)
        ).first()
    if not row or not row[0]:
        return {}
    return row[0] if isinstance(row[0], dict) else {}


def set_values(entity_table, entity_id: int, partial: dict) -> dict:
    """Merge ``partial`` into the entity's custom_attributes and persist.

    Reassigns the WHOLE dict (mutation-tracking rule). A value of None removes
    that key. Returns the merged dict.
    """
    with get_engine().begin() as conn:
        row = conn.execute(
            select(entity_table.c.custom_attributes).where(entity_table.c.id == entity_id)
        ).first()
        current = (row[0] if row and isinstance(row[0], dict) else {}) or {}
        merged = dict(current)
        for k, v in partial.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        conn.execute(
            sa_update(entity_table).where(entity_table.c.id == entity_id)
            .values(custom_attributes=merged)
        )
    return merged
