"""Contact search/filter clause building (plano 23 Fase E2).

Owns the text-search side of ``contact_repo.list_contacts``:

- ``fold`` / ``match_snippet`` — accent/case-insensitive folding and the excerpt
  centered on a match (display keeps the original, accented text).
- ``contact_ids_matching_message`` — the "find a conversation by something said
  in it" message scan.
- ``build_list_contacts_query`` — the heavy ``list_contacts`` SELECT, now built
  with SQLAlchemy Core instead of raw ``text().format()`` interpolation of
  ``{preview_excluded}`` / ``{inbox_clause}``. Same rows, same order, dialect-
  agnostic (SQLite + Postgres) as before.

These are pure query/string builders — ``contact_repo`` keeps owning the
row→dict shaping and the post-fetch filtering loop, so the public API is
unchanged.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy import Select
from sqlalchemy import func, select

from db.repositories._mapping import _PREVIEW_EXCLUDED
from db.tables import contacts, conversations, messages


def fold(s: str) -> str:
    """Casefold and strip accents so search matches regardless of diacritics.

    "Ó"/"ó"/"o" all fold to "o", so typing an unaccented letter still finds
    accented names (and vice-versa).
    """
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).casefold()


def match_snippet(content: str, folded_q: str, radius: int = 40) -> str:
    """A short excerpt of ``content`` centered on the first match of ``folded_q``,
    with ellipses when trimmed. Matching is accent/case-insensitive (via ``fold``),
    but the snippet keeps the ORIGINAL text (accents intact) for display.
    """
    if not content:
        return ""
    # Per-character fold, tracking the original index each folded char came from,
    # so a match position in the folded string maps back to the original text.
    folded_chars: list[str] = []
    orig_idx: list[int] = []
    for i, ch in enumerate(content):
        for fc in fold(ch):
            folded_chars.append(fc)
            orig_idx.append(i)
    folded = "".join(folded_chars)
    pos = folded.find(folded_q)
    if pos < 0:
        return content[: radius * 2].strip()
    start = orig_idx[pos]
    end_f = min(pos + len(folded_q), len(orig_idx)) - 1
    end = orig_idx[end_f] + 1 if end_f >= 0 else start
    w_start = max(0, start - radius)
    w_end = min(len(content), end + radius)
    excerpt = content[w_start:w_end].strip()
    prefix = "…" if w_start > 0 else ""
    suffix = "…" if w_end < len(content) else ""
    return f"{prefix}{excerpt}{suffix}"


def contact_ids_matching_message(conn, folded_q: str, archived: bool) -> dict[int, dict]:
    """Map of contact id -> ``{"snippet": str, "id": int}`` for contacts (within the
    given archived scope) that have at least one message whose content matches
    ``folded_q`` (accent/case-insensitive, like names). The match comes from the most
    recent matching message; ``id`` is its DB primary key (so the UI can scroll to it).

    Covers normal messages, private notes and transcriptions; only the purely
    internal roles (tool calls, system notices) are skipped. Revoked messages are
    kept in the DB with their content, so they remain searchable too.

    Runs on the caller's ``conn`` (same connection used by ``list_contacts``).
    """
    if not folded_q:
        return {}

    # Most recent first, so the first match seen per contact is the freshest one.
    # Core equivalent of the prior raw SQL — same join/filter/order.
    stmt = (
        select(messages.c.id, messages.c.contact_id, messages.c.content)
        .select_from(messages.join(contacts, contacts.c.id == messages.c.contact_id))
        .where(contacts.c.is_archived == (1 if archived else 0))
        .where(messages.c.content != "")
        .where(messages.c.role.notin_(
            ("tool_call", "system_notice", "conversation_event", "system")))
        .order_by(messages.c.ts.desc())
    )
    matched: dict[int, dict] = {}
    for row in conn.execute(stmt).mappings():
        cid = row["contact_id"]
        if cid in matched:
            continue
        content = row["content"] or ""
        if folded_q in fold(content):
            matched[cid] = {"snippet": match_snippet(content, folded_q), "id": row["id"]}
    return matched


def build_list_contacts_query(*, archived: bool,
                              inbox_ids: list[int] | None) -> Select:
    """Build the ``list_contacts`` SELECT as a SQLAlchemy Core statement.

    Replaces the prior raw ``text().format(preview_excluded=…, inbox_clause=…)``:

    - ``lm`` — the latest VISIBLE message per contact (roles in ``_PREVIEW_EXCLUDED``
      excluded), via a ``MAX(ts)`` self-join. This was the ``{preview_excluded}``
      literal; now ``_PREVIEW_EXCLUDED`` flows through ``.notin_()`` as bind params.
    - ``conv`` — the ACTIVE conversation per contact (the highest id), via a
      ``MAX(id)`` self-join, surfacing status/assignee/agente/ai_active.
    - ``msg_count`` — correlated scalar count of all messages of the contact.
    - the inbox scope — was the optional ``{inbox_clause}`` EXISTS; now a Core
      ``.exists()`` correlated subquery only when ``inbox_ids`` is not None.

    Ordering and selected labels match the original exactly so the row→dict shaping
    in ``contact_repo`` is unchanged. ``inbox_ids`` is assumed non-empty when not
    None (the empty-list "sees nothing" shortcut is handled by the caller).
    """
    # lm: latest visible message per contact (MAX(ts) self-join, preview roles out)
    m1 = messages.alias("m1")
    visible = messages.c.role.notin_(_PREVIEW_EXCLUDED)
    m2 = (
        select(
            messages.c.contact_id.label("contact_id"),
            func.max(messages.c.ts).label("max_ts"),
        )
        .where(visible)
        .group_by(messages.c.contact_id)
        .subquery("m2")
    )
    lm = (
        select(
            m1.c.contact_id.label("contact_id"),
            m1.c.content.label("content"),
            m1.c.role.label("role"),
            m1.c.ts.label("ts"),
            m1.c.media_type.label("media_type"),
            m1.c.status.label("status"),
            m1.c.msg_id.label("msg_id"),
        )
        .select_from(
            m1.join(m2, (m1.c.contact_id == m2.c.contact_id) & (m1.c.ts == m2.c.max_ts))
        )
        .subquery("lm")
    )

    # conv: active conversation per contact (MAX(id) self-join)
    cv1 = conversations.alias("cv1")
    cv2 = (
        select(
            conversations.c.contact_id.label("contact_id"),
            func.max(conversations.c.id).label("max_id"),
        )
        .group_by(conversations.c.contact_id)
        .subquery("cv2")
    )
    conv = (
        select(
            cv1.c.contact_id.label("contact_id"),
            cv1.c.id.label("conv_id"),
            cv1.c.status.label("conv_status"),
            cv1.c.assignee_user_id.label("conv_assignee_user_id"),
            cv1.c.active_agent_key.label("conv_active_agent_key"),
            cv1.c.ai_active.label("conv_ai_active"),
        )
        .select_from(
            cv1.join(cv2, (cv1.c.contact_id == cv2.c.contact_id) & (cv1.c.id == cv2.c.max_id))
        )
        .subquery("conv")
    )

    msg_count = (
        select(func.count())
        .select_from(messages)
        .where(messages.c.contact_id == contacts.c.id)
        .scalar_subquery()
    )

    stmt = (
        select(
            contacts,
            lm.c.content.label("last_msg_content"),
            lm.c.role.label("last_msg_role"),
            lm.c.ts.label("last_msg_ts"),
            lm.c.media_type.label("last_msg_media_type"),
            lm.c.status.label("last_msg_status"),
            lm.c.msg_id.label("last_msg_id"),
            conv.c.conv_id.label("conv_id"),
            conv.c.conv_status.label("conv_status"),
            conv.c.conv_assignee_user_id.label("conv_assignee_user_id"),
            conv.c.conv_active_agent_key.label("conv_active_agent_key"),
            conv.c.conv_ai_active.label("conv_ai_active"),
            msg_count.label("msg_count"),
        )
        .select_from(
            contacts
            .outerjoin(lm, lm.c.contact_id == contacts.c.id)
            .outerjoin(conv, conv.c.contact_id == contacts.c.id)
        )
        .where(contacts.c.is_archived == (1 if archived else 0))
    )

    if inbox_ids is not None:
        scope = (
            select(conversations.c.id)
            .where(conversations.c.contact_id == contacts.c.id)
            .where(conversations.c.inbox_id.in_(inbox_ids))
        ).exists()
        stmt = stmt.where(scope)

    stmt = stmt.order_by(
        contacts.c.is_pinned.desc(),
        func.coalesce(lm.c.ts, contacts.c.updated_at).desc(),
    )
    return stmt


def build_count_contacts_query(*, archived: bool,
                               inbox_ids: list[int] | None) -> Select:
    """COUNT dos contatos que :func:`build_list_contacts_query` listaria (plano 50 F5).

    Espelha só o ``WHERE`` (archived + escopo de inbox) — sem os joins de preview/
    conversa/msg_count — para dar o ``total`` da paginação barato (uma varredura de
    índice em ``contacts``, não a query pesada). Vale apenas para o caminho SEM busca
    textual (com ``q`` o total é o tamanho da lista já filtrada em Python)."""
    stmt = (
        select(func.count())
        .select_from(contacts)
        .where(contacts.c.is_archived == (1 if archived else 0))
    )
    if inbox_ids is not None:
        scope = (
            select(conversations.c.id)
            .where(conversations.c.contact_id == contacts.c.id)
            .where(conversations.c.inbox_id.in_(inbox_ids))
        ).exists()
        stmt = stmt.where(scope)
    return stmt
