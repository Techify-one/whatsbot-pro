"""Enriched-conversation SELECT assembly for ``conversation_repo`` (plano 23 Fase E2).

Holds the heavy read-side query construction for the conversa-cêntrica list/detail
surfaces (columns + joins + last-message preview subqueries + per-conversation
unread), plus the row→dict finalizer. ``conversation_repo`` keeps thin facades
that compose these — public API unchanged, same rows/shapes as before.
"""

from __future__ import annotations

from sqlalchemy import func, select

from db.repositories._mapping import _PREVIEW_EXCLUDED, media_preview
from db.tables import (channels, contacts, conversations, inboxes, messages,
                       unread_msg_ids)


def last_msg_subq(col):
    """Correlated scalar subquery: ``col`` of the latest visible msg of the conversation."""
    return (
        select(col)
        .where(messages.c.conversation_id == conversations.c.id)
        .where(messages.c.role.notin_(_PREVIEW_EXCLUDED))
        .order_by(messages.c.ts.desc())
        .limit(1)
        .scalar_subquery()
    )


def enriched_columns() -> list:
    """Columns for a conversation-centric list: conv + contact + channel + preview
    + per-CONVERSA unread (plano 11 D1).

    Unread é DERIVADO de ``unread_msg_ids ⋈ messages.msg_id`` filtrando por
    ``conversation_id`` — assim o mesmo número em 2 canais tem badges independentes
    sem coluna denormalizada nova (os contadores por-contato seguem intactos).
    """
    unread_subq = (
        select(func.count())
        .select_from(unread_msg_ids.join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id))
        .where(messages.c.conversation_id == conversations.c.id)
        .scalar_subquery()
    )
    return [
        conversations,
        contacts.c.name.label("contact_name"),
        contacts.c.phone.label("contact_phone"),
        contacts.c.is_group.label("contact_is_group"),
        contacts.c.is_pinned.label("is_pinned"),
        contacts.c.has_unread_mention.label("has_unread_mention"),
        # Contact-level AI-unread count (plano 28): carried so a conversation_upsert
        # keeps the "IA respondeu" badge live (contact-level, like in buildRows).
        contacts.c.unread_ai_count.label("unread_ai_count"),
        inboxes.c.channel_id.label("channel_id"),
        channels.c.provider.label("channel_provider"),
        channels.c.display_name.label("channel_name"),
        last_msg_subq(messages.c.content).label("last_msg_content"),
        last_msg_subq(messages.c.role).label("last_msg_role"),
        last_msg_subq(messages.c.ts).label("last_msg_ts"),
        last_msg_subq(messages.c.media_type).label("last_msg_media_type"),
        last_msg_subq(messages.c.status).label("last_msg_status"),
        last_msg_subq(messages.c.msg_id).label("last_msg_id"),
        unread_subq.label("unread_count"),
    ]


def enriched_from():
    return (
        conversations
        .join(contacts, contacts.c.id == conversations.c.contact_id)
        .outerjoin(inboxes, inboxes.c.id == conversations.c.inbox_id)
        .outerjoin(channels, channels.c.id == inboxes.c.channel_id)
    )


def finalize_conv(row) -> dict:
    """Shape a raw enriched row into the dict the sidebar/list consumes."""
    d = dict(row)
    d["last_message"] = media_preview(d.get("last_msg_content"),
                                      d.get("last_msg_media_type"))
    d["last_message_role"] = d.get("last_msg_role") or ""
    d["last_message_ts"] = d.get("last_msg_ts") or 0
    d["last_message_status"] = d.get("last_msg_status") or ""
    d["last_message_msg_id"] = d.get("last_msg_id") or ""
    d["unread_count"] = int(d.get("unread_count") or 0)
    d["unread_ai_count"] = int(d.get("unread_ai_count") or 0)
    d["is_pinned"] = bool(d.get("is_pinned"))
    d["has_unread_mention"] = bool(d.get("has_unread_mention"))
    return d
