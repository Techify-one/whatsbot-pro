"""Repository for conversation labels (etiquetas próprias da conversa, Onda 3).

Mirrors tag_repo, but for the conversation-scoped registry
(``conversation_labels``) and its N:N link to conversations
(``conversation_label_links``). Labels are SEPARATE from contact tags.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import conversation_labels as labels
from db.tables import conversation_label_links as links
from db.upsert import upsert_ignore


# ── Registry (global) ─────────────────────────────────────────────────────

def get_all() -> list[dict]:
    """All labels ordered by position then name (the picker/registry view)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(labels).order_by(labels.c.position, labels.c.name)
        ).mappings().all()
    return [dict(r) for r in rows]


def get(label_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(labels).where(labels.c.id == label_id)).mappings().first()
    return dict(row) if row else None


def get_by_name(name: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(labels).where(labels.c.name == name)).mappings().first()
    return dict(row) if row else None


def create(name: str, color: str = "#6b7280", position: int = 0) -> dict | None:
    """Create a label. Returns the row, or None if the name already exists."""
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(labels.c.id).where(labels.c.name == name)
        ).scalar_one_or_none()
        if existing is not None:
            return None
        result = conn.execute(sa_insert(labels).values(
            name=name, color=color, position=position, created_at=time.time()))
        new_id = result.inserted_primary_key[0]
    return get(new_id)


def update(label_id: int, *, name: str | None = None, color: str | None = None,
           position: int | None = None) -> dict | None:
    """Rename/recolor/reorder a label. Returns the row, or None if not found."""
    values: dict = {}
    if name:
        values["name"] = name
    if color:
        values["color"] = color
    if position is not None:
        values["position"] = position
    with get_engine().begin() as conn:
        exists = conn.execute(select(labels.c.id).where(labels.c.id == label_id)).first()
        if exists is None:
            return None
        if values:
            conn.execute(sa_update(labels).where(labels.c.id == label_id).values(**values))
    return get(label_id)


def delete(label_id: int) -> bool:
    """Delete a label and detach it from every conversation. False if not found."""
    with get_engine().begin() as conn:
        existing = conn.execute(select(labels.c.id).where(labels.c.id == label_id)).first()
        if existing is None:
            return False
        conn.execute(sa_delete(links).where(links.c.label_id == label_id))
        conn.execute(sa_delete(labels).where(labels.c.id == label_id))
    return True


# ── Per-conversation assignment ───────────────────────────────────────────

def get_for_conversation(conv_id: int) -> list[dict]:
    """Labels linked to a conversation (id, name, color, position)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(labels)
            .join(links, links.c.label_id == labels.c.id)
            .where(links.c.conversation_id == conv_id)
            .order_by(labels.c.position, labels.c.name)
        ).mappings().all()
    return [dict(r) for r in rows]


def get_names_for_conversation(conv_id: int) -> list[str]:
    return [r["name"] for r in get_for_conversation(conv_id)]


def set_for_conversation(conv_id: int, names: list[str]) -> list[dict]:
    """Replace a conversation's labels with the given names (snapshot).

    Names with no matching registry label are ignored (the editor creates the
    label in the registry first). Returns the resulting label rows.
    """
    with get_engine().begin() as conn:
        conn.execute(sa_delete(links).where(links.c.conversation_id == conv_id))
        for name in names:
            label_id = conn.execute(
                select(labels.c.id).where(labels.c.name == name)
            ).scalar_one_or_none()
            if label_id is not None:
                conn.execute(upsert_ignore(
                    links,
                    {"conversation_id": conv_id, "label_id": label_id},
                    conflict_cols=["conversation_id", "label_id"],
                ))
    return get_for_conversation(conv_id)
