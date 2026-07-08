"""Enriched-conversation SELECT assembly for ``conversation_repo`` (plano 23 Fase E2).

Holds the heavy read-side query construction for the conversa-cêntrica list/detail
surfaces (columns + joins + last-message preview subqueries + per-conversation
unread), plus the row→dict finalizer. ``conversation_repo`` keeps thin facades
that compose these — public API unchanged, same rows/shapes as before.
"""

from __future__ import annotations

from sqlalchemy import func, select

from sqlalchemy import exists, literal

from db.repositories._mapping import _PREVIEW_EXCLUDED, media_preview
from db.tables import (channels, contacts, conversations, inboxes, mentions,
                       messages, unread_msg_ids)


def last_msg_subq(col, excluded=_PREVIEW_EXCLUDED):
    """Correlated scalar subquery: ``col`` of the latest visible msg of the conversation.

    ``excluded`` é o conjunto de roles painel-only pulados no preview. O default é o
    canônico ``_PREVIEW_EXCLUDED``; o call site pode passar um set reduzido (ex.: sem
    ``private_note``) para que uma nota privada notificada participe do preview/ordenação
    da sidebar — gate ``notify_private_messages``, plano notificação-privada."""
    return (
        select(col)
        .where(messages.c.conversation_id == conversations.c.id)
        .where(messages.c.role.notin_(excluded))
        .order_by(messages.c.ts.desc())
        .limit(1)
        .scalar_subquery()
    )


def enriched_columns(include_private_note: bool = False,
                     current_user_id: int | None = None) -> list:
    """Columns for a conversation-centric list: conv + contact + channel + preview
    + per-CONVERSA unread (plano 11 D1).

    Unread é DERIVADO de ``unread_msg_ids ⋈ messages.msg_id`` filtrando por
    ``conversation_id`` — assim o mesmo número em 2 canais tem badges independentes
    sem coluna denormalizada nova (os contadores por-contato seguem intactos).

    ``include_private_note`` (gate ``notify_private_messages``): quando ``True``, a nota
    privada deixa de ser pulada no preview — vira a "última mensagem" da sidebar
    (content/role/ts), então a conversa sobe ao topo e mostra o texto + cadeado. Quando
    ``False`` (default), comportamento byte-a-byte legado.

    ``current_user_id`` (colaboração estilo Chatwoot): quando informado, adiciona o flag
    POR-USUÁRIO ``has_user_mention`` = existe menção não-lida (``mentions``) daquele usuário
    nesta conversa → alimenta o badge "@" e a aba "Menções". ``None`` (ex.: contexto sem
    usuário) ⇒ o flag sai ``False`` (subquery constante-falsa barata).
    """
    excluded = (tuple(r for r in _PREVIEW_EXCLUDED if r != "private_note")
                if include_private_note else _PREVIEW_EXCLUDED)
    unread_subq = (
        select(func.count())
        .select_from(unread_msg_ids.join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id))
        .where(messages.c.conversation_id == conversations.c.id)
        .scalar_subquery()
    )
    if current_user_id is not None:
        user_mention_subq = (
            exists()
            .where(mentions.c.conversation_id == conversations.c.id)
            .where(mentions.c.mentioned_user_id == current_user_id)
            .where(mentions.c.read_at.is_(None))
        ).label("has_user_mention")
    else:
        user_mention_subq = literal(False).label("has_user_mention")
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
        last_msg_subq(messages.c.content, excluded).label("last_msg_content"),
        last_msg_subq(messages.c.role, excluded).label("last_msg_role"),
        last_msg_subq(messages.c.ts, excluded).label("last_msg_ts"),
        last_msg_subq(messages.c.media_type, excluded).label("last_msg_media_type"),
        last_msg_subq(messages.c.status, excluded).label("last_msg_status"),
        last_msg_subq(messages.c.msg_id, excluded).label("last_msg_id"),
        unread_subq.label("unread_count"),
        user_mention_subq,
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
    d["has_user_mention"] = bool(d.get("has_user_mention"))
    return d
