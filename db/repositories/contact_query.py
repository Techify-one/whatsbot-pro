"""Heavy SELECT assembly + row shaping for ``contact_repo`` (plano 23 Fase E2).

Pulls the read-side query construction and the row→dict mapping out of the repo
so ``contact_repo`` is a thin orchestration facade. No observable behavior change:
the dicts produced here are byte-for-byte what the repo used to build inline.

Includes the N+1 tags fix for ``list_contacts``: :func:`tags_by_contact` batch-
loads every listed contact's tags in ONE ``contact_tags ⋈ tags`` query and groups
them in Python in row-iteration order, preserving the exact per-contact tag order
the old per-contact query produced.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from db.engine import get_engine
from db.repositories._mapping import coerce_json
from db.tables import (contact_tags, contacts, conversations, observations,
                       tags)


# ── Row shaping ────────────────────────────────────────────────────────────

def coerce_attrs(row) -> dict:
    """Return the custom_attributes dict for a row, tolerating missing/serialized."""
    try:
        val = row["custom_attributes"]
    except (KeyError, IndexError, TypeError):
        return {}
    parsed = coerce_json(val, {})
    return parsed if isinstance(parsed, dict) else {}


def row_to_dict(row) -> dict:
    """Convert a SQLAlchemy mapping row to a plain dict with Python types."""
    return {
        "id": row["id"],
        "phone": row["phone"],
        "name": row["name"],
        "email": row["email"],
        "profession": row["profession"],
        "company": row["company"],
        "address": row["address"],
        "ai_enabled": bool(row["ai_enabled"]),
        "is_group": bool(row["is_group"]),
        "group_name": row["group_name"],
        "is_archived": bool(row["is_archived"]),
        "archived_by_app": bool(row["archived_by_app"]) if row["archived_by_app"] is not None else False,
        "is_pinned": bool(row.get("is_pinned")) if hasattr(row, "get") else bool(row["is_pinned"]),
        "can_send": bool(row["can_send"]) if row["can_send"] is not None else True,
        "unread_count": row["unread_count"],
        "unread_ai_count": row["unread_ai_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        # Custom attributes (plano 05). Defensive: not every contacts query
        # selects the column. JSON column comes back as a dict (or str on SQLite).
        "custom_attributes": coerce_attrs(row),
    }


# ── Batched tags (N+1 fix) ──────────────────────────────────────────────────

def tags_by_contact(conn, contact_ids: list[int]) -> dict[int, list[str]]:
    """Tags for many contacts in ONE query, grouped per contact.

    Replaces the per-contact ``SELECT tags.name … WHERE contact_id = ?`` loop
    (one query per row). The old query had NO ``ORDER BY``, so each contact's tag
    list came back in the DB's natural ``contact_tags`` row order; we reproduce
    that exactly by iterating the joined result in row order and appending to each
    contact's list as tags are seen (no sort), so the resulting per-contact tag
    order is identical. Runs on the caller's ``conn``.
    """
    if not contact_ids:
        return {}
    rows = conn.execute(
        select(contact_tags.c.contact_id, tags.c.name)
        .select_from(contact_tags.join(tags, tags.c.id == contact_tags.c.tag_id))
        .where(contact_tags.c.contact_id.in_(contact_ids))
    ).all()
    grouped: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        grouped[r.contact_id].append(r.name)
    return grouped


# ── Inbox-scope visibility (contact-centric legacy read surface) ─────────────

def contact_has_visible_conversation(contact_id: int,
                                     inbox_ids: list[int] | None) -> bool:
    """Whether the contact has at least one conversation in a visible inbox.

    Inbox-membership scoping for the contact-centric (legacy) read surface
    (plano inboxes/canais §4.7): ``inbox_ids=None`` ⇒ no scoping (sees all);
    empty list ⇒ member of no inbox ⇒ nothing visible; otherwise the contact is
    visible só se tiver conversa numa das inboxes do usuário."""
    if inbox_ids is None:
        return True
    if not inbox_ids:
        return False
    with get_engine().connect() as conn:
        hit = conn.execute(
            select(conversations.c.id)
            .where(conversations.c.contact_id == contact_id,
                   conversations.c.inbox_id.in_(inbox_ids))
            .limit(1)
        ).first()
    return hit is not None


def contact_hidden_by_inbox_scope(contact_id: int,
                                  inbox_ids: list[int] | None) -> bool:
    """True if the contact detail must be hidden (404) for a scoped user.

    Hides only contacts that HAVE conversations but none in a visible inbox. A
    contact without any conversation (brand-new / start-new-conversation flow) is
    NOT hidden, so a scoped user can still open it to begin a thread on an inbox
    they belong to."""
    if inbox_ids is None:
        return False
    if contact_has_visible_conversation(contact_id, inbox_ids):
        return False
    with get_engine().connect() as conn:
        has_any = conn.execute(
            select(conversations.c.id)
            .where(conversations.c.contact_id == contact_id).limit(1)
        ).first()
    return has_any is not None


# ── Whole-contact reads ──────────────────────────────────────────────────────

def list_for_export(inbox_ids: list[int] | None = None) -> list[dict]:
    """Return non-group contacts with full info + tags, for CSV export.

    ``inbox_ids`` scopes by inbox membership (plano inboxes/canais §4.7), mirroring
    ``list_contacts``: ``None`` ⇒ no scoping; empty ⇒ nothing; a list ⇒ only contacts
    with a conversation in one of those inboxes. Includes both archived and active
    contacts (an export should be complete). Groups are skipped — they can't be
    re-imported by phone."""
    if inbox_ids is not None and not inbox_ids:
        return []
    stmt = select(contacts).where(contacts.c.is_group == 0)
    if inbox_ids is not None:
        stmt = stmt.where(contacts.c.id.in_(
            select(conversations.c.contact_id).where(
                conversations.c.inbox_id.in_(inbox_ids))
        ))
    stmt = stmt.order_by(contacts.c.name, contacts.c.phone)
    results = []
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
        for row in rows:
            tag_rows = conn.execute(
                select(tags.c.name)
                .join(contact_tags, contact_tags.c.tag_id == tags.c.id)
                .where(contact_tags.c.contact_id == row["id"])
                .order_by(tags.c.name)
            ).all()
            custom = row["custom_attributes"]
            results.append({
                "phone": row["phone"],
                "name": row["name"] or "",
                "email": row["email"] or "",
                "profession": row["profession"] or "",
                "company": row["company"] or "",
                "address": row["address"] or "",
                "ai_enabled": bool(row["ai_enabled"]),
                "tags": [t.name for t in tag_rows],
                "custom_attributes": custom if isinstance(custom, dict) else {},
            })
    return results


def get_full_contact(variants: list[str]) -> dict | None:
    """Get full contact data for API response (contact + info + observations).

    ``variants`` is the BR-phone variant list resolved by the repo facade.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.phone.in_(variants))
        ).mappings().first()
        if row is None:
            return None
        contact_id = row["id"]
        obs_rows = conn.execute(
            select(observations.c.text)
            .where(observations.c.contact_id == contact_id)
            .order_by(observations.c.created_at)
        ).all()
        tag_rows = conn.execute(
            select(tags.c.name)
            .join(contact_tags, contact_tags.c.tag_id == tags.c.id)
            .where(contact_tags.c.contact_id == contact_id)
        ).all()

    data = row_to_dict(row)
    data["info"] = {
        "name": row["name"],
        "email": row["email"],
        "profession": row["profession"],
        "company": row["company"],
        "address": row["address"],
        "observations": [r.text for r in obs_rows],
        # Custom attributes (plano 05): also surface them under `info` so the
        # contact panel and the resolve guard read a single, reliable source.
        "custom_attributes": data["custom_attributes"],
    }
    data["tags"] = [t.name for t in tag_rows]
    return data
