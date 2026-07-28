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
from sqlalchemy.exc import IntegrityError

from db.engine import get_engine
from db.tables import custom_attribute_definitions as cad

# Fields the client may set on create; attribute_key/type/applies_to are identity
# (settable on create, immutable on update).
_EDITABLE = ("display_name", "options", "required", "description",
             "regex_pattern", "regex_cue", "position", "filterable")


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
                       position: int = 0, filterable: int = 0, is_system: int = 0,
                       created_by=None) -> dict | None:
    """Create a definition. Returns the row, or None on ACTIVE (key, applies_to)
    collision.

    A *soft-deleted* row with the same (key, applies_to) is REVIVED in place (its
    ``deleted_at`` cleared and every field overwritten with the new values) instead
    of inserting a fresh row — a fresh INSERT would violate ``uq_attr_key_scope``,
    which is not delete-aware, and raise a 500. Reviving also re-validates any
    values still stored on entities under that key (P49 keeps them on delete).

    ``is_system`` marks a built-in (plano 19) — only the boot seeder sets it; the
    public create route always passes 0.
    """
    now = time.time()
    values = dict(
        attribute_key=attribute_key, display_name=display_name, type=type,
        applies_to=applies_to, options=options, required=required,
        description=description, regex_pattern=regex_pattern, regex_cue=regex_cue,
        position=position, filterable=filterable, is_system=is_system,
        created_by=created_by, created_at=now, deleted_at=None,
    )
    try:
        with get_engine().begin() as conn:
            existing = conn.execute(
                select(cad.c.id, cad.c.deleted_at).where(
                    cad.c.attribute_key == attribute_key,
                    cad.c.applies_to == applies_to,
                )
            ).mappings().first()
            if existing is not None:
                if existing["deleted_at"] is None:
                    return None  # active dup → friendly "já existe" upstream
                # Soft-deleted slot → revive it (avoids the uq_attr_key_scope 500).
                conn.execute(
                    sa_update(cad).where(cad.c.id == existing["id"]).values(**values)
                )
                new_id = existing["id"]
            else:
                result = conn.execute(sa_insert(cad).values(**values))
                new_id = result.inserted_primary_key[0]
    except IntegrityError:
        # Concurrent create raced us to the same (key, applies_to) → treat as dup.
        return None
    return get_definition(new_id)


def ensure_system_definition(*, attribute_key: str, display_name: str, type: str = "text",
                             applies_to: str = "contact", description: str = "",
                             position: int = 0, is_system: int = 1, **_) -> dict | None:
    """Idempotently seed a built-in attribute — plano 19.

    No-op when a definition with this ``(attribute_key, applies_to)`` already exists,
    whether ACTIVE or soft-deleted, so user edits and deletions are respected across
    boots (and the ``UniqueConstraint(attribute_key, applies_to)`` is never violated).
    Returns the created row, or ``None`` when it already existed.

    ``is_system`` defaults to 1 (locked: no rename/delete, "Sistema" badge — e.g. CPF).
    Pass ``is_system=0`` to seed a *default* attribute that ships pre-defined but stays
    fully editable AND deletable (e.g. Email/Profissão/Empresa/Endereço).
    """
    with get_engine().connect() as conn:
        existing = conn.execute(
            select(cad.c.id).where(cad.c.attribute_key == attribute_key,
                                   cad.c.applies_to == applies_to)
        ).first()
    if existing is not None:
        return None
    return create_definition(
        attribute_key=attribute_key, display_name=display_name, type=type,
        applies_to=applies_to, description=description, position=position,
        is_system=is_system)


def list_filterable(applies_to: str) -> list[dict]:
    """Active definitions marked filterable (plano 08 filter-schema consumes this)."""
    return [d for d in list_definitions(applies_to) if d.get("filterable")]


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
