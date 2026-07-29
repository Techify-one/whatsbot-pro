"""Repository for contacts table (plano 23 Fase E2 — thin orchestration facade).

The public API (function names, signatures, return shapes) is unchanged. The
heavy lifting moved to focused modules; this file just orchestrates them:

- unread bookkeeping → ``db.repositories.unread_repo``
- observations CRUD → ``db.repositories.observation_repo``
- row shaping + whole-contact reads + inbox-scope → ``db.repositories.contact_query``
- list_contacts SELECT + text search → ``db.search.contact_search``
- BR phone variants → ``channels.br_phone``
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from channels.br_phone import br_phone_variants as _br_phone_variants
from db.engine import get_engine
from db.repositories import contact_query, observation_repo, unread_repo
from db.repositories._mapping import media_preview
from db.search import contact_search
from db.tables import contacts

# Re-exported so the (unchanged) module-level helper name keeps working for any
# in-repo reference; the canonical row shaper lives in contact_query now.
_row_to_dict = contact_query.row_to_dict
_coerce_attrs = contact_query.coerce_attrs


def get_or_create(phone: str, default_ai_enabled: bool = True,
                  contact_type: str = "outros") -> dict:
    """Get a contact by phone, creating it if it doesn't exist.

    ``contact_type`` (plano tipos-de-contato) is the channel-declared kind
    (``whatsapp``/``telegram``/``outros``); it is only applied on INSERT, so an
    already-existing contact keeps whatever type it was first created with.
    """
    variants = _br_phone_variants(phone)
    with get_engine().begin() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.phone.in_(variants))
        ).mappings().first()
        if row is not None:
            return _row_to_dict(row)
        now = time.time()
        result = conn.execute(sa_insert(contacts).values(
            phone=phone,
            ai_enabled=1 if default_ai_enabled else 0,
            contact_type=contact_type or "outros",
            created_at=now,
            updated_at=now,
        ))
        new_id = result.inserted_primary_key[0]
        # Re-read the inserted row so the INSERT path returns the SAME shape as the
        # existing-contact path (plano 23 E1 — fixes the divergent hand-built dict
        # that was missing is_pinned/custom_attributes and could drift from the
        # column defaults).
        new_row = conn.execute(
            select(contacts).where(contacts.c.id == new_id)
        ).mappings().first()
    return _row_to_dict(new_row)


def delete(contact_id: int) -> None:
    """Delete a contact and all related data (CASCADE handles child tables)."""
    with get_engine().begin() as conn:
        conn.execute(sa_delete(contacts).where(contacts.c.id == contact_id))


def set_archived(contact_id: int, archived: bool, by_app: bool = False) -> None:
    """Set the archived status of a contact."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            is_archived=1 if archived else 0,
            archived_by_app=1 if (archived and by_app) else 0,
            updated_at=time.time(),
        ))


def set_pinned(contact_id: int, pinned: bool) -> None:
    """Pin or unpin a conversation (pinned ones sort to the top of the list)."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(
            is_pinned=1 if pinned else 0,
            updated_at=time.time(),
        ))


def get_by_phone(phone: str) -> dict | None:
    """Get a contact by phone number. Checks BR phone variants."""
    variants = _br_phone_variants(phone)
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.phone.in_(variants))
        ).mappings().first()
    return _row_to_dict(row) if row else None


def get(contact_id: int) -> dict | None:
    """Get a contact by its primary key."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.id == contact_id)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def update(contact_id: int, **fields) -> None:
    """Update specific fields on a contact."""
    if not fields:
        return
    fields["updated_at"] = time.time()
    with get_engine().begin() as conn:
        conn.execute(sa_update(contacts).where(contacts.c.id == contact_id).values(**fields))


# ── Unread bookkeeping (delegated to unread_repo) ────────────────────────────

def increment_unread(contact_id: int, msg_id: str | None = None) -> None:
    """Increment unread_count and optionally track the msg_id."""
    return unread_repo.increment_unread(contact_id, msg_id)


def increment_unread_ai(contact_id: int) -> None:
    """Increment unread_ai_count."""
    return unread_repo.increment_unread_ai(contact_id)


def mark_as_read(contact_id: int) -> list[str]:
    """Reset unread counts and return the unread msg_ids (for read receipts)."""
    return unread_repo.mark_as_read(contact_id)


def unread_conversation_count(inbox_ids: list[int] | None = None) -> int:
    """Number of non-archived conversations that have unread messages — used for the
    browser-tab badge (e.g. "(3) WhatsBot"). Counts a conversation once regardless of
    how many messages are unread, mirroring the sidebar badge visibility."""
    return unread_repo.unread_conversation_count(inbox_ids)


def set_mention(contact_id: int) -> None:
    """Raise the unread-mention flag (bot was @mentioned in a group). Shown as an
    "@" next to the unread badge until the operator opens the conversation."""
    return unread_repo.set_mention(contact_id)


def mark_as_unread(contact_id: int) -> None:
    """Mark a contact as unread by ensuring unread_count is at least 1.

    Only touches the in-app green badge; preserves an already-higher count.
    """
    return unread_repo.mark_as_unread(contact_id)


def mark_all_as_unread() -> int:
    """Mark every conversation as unread (green badge).

    Only rows currently at 0 are touched, so existing higher counts are kept.
    Returns the number of conversations newly marked.
    """
    return unread_repo.mark_all_as_unread()


def mark_all_as_read() -> int:
    """Reset unread counts for every conversation (clear all in-app badges).

    App-only: clears the tracked unread msg_ids too, but does not send WhatsApp
    read receipts. Returns the number of conversations that had unread badges.
    """
    return unread_repo.mark_all_as_read()


def mark_user_messages_as_read(contact_id: int) -> list[str]:
    """Reset only unread_count (user messages) and return msg_ids for read receipts."""
    return unread_repo.mark_user_messages_as_read(contact_id)


# ── Observations (delegated to observation_repo) ─────────────────────────────

def get_observations(contact_id: int) -> list[str]:
    """Return all observations for a contact."""
    return observation_repo.get_observations(contact_id)


def set_observations(contact_id: int, observations_list: list[str]) -> None:
    """Replace all observations for a contact."""
    return observation_repo.set_observations(contact_id, observations_list)


def add_observation(contact_id: int, text: str) -> None:
    """Append a single observation if it doesn't already exist."""
    return observation_repo.add_observation(contact_id, text)


# ── Inbox-scope visibility (delegated to contact_query) ──────────────────────

def contact_has_visible_conversation(contact_id: int,
                                     inbox_ids: list[int] | None) -> bool:
    """Whether the contact has at least one conversation in a visible inbox.

    Inbox-membership scoping for the contact-centric (legacy) read surface
    (plano inboxes/canais §4.7): ``inbox_ids=None`` ⇒ no scoping (sees all);
    empty list ⇒ member of no inbox ⇒ nothing visible; otherwise the contact is
    visible só se tiver conversa numa das inboxes do usuário."""
    return contact_query.contact_has_visible_conversation(contact_id, inbox_ids)


def contact_hidden_by_inbox_scope(contact_id: int,
                                  inbox_ids: list[int] | None) -> bool:
    """True if the contact detail must be hidden (404) for a scoped user.

    Hides only contacts that HAVE conversations but none in a visible inbox. A
    contact without any conversation (brand-new / start-new-conversation flow) is
    NOT hidden, so a scoped user can still open it to begin a thread on an inbox
    they belong to."""
    return contact_query.contact_hidden_by_inbox_scope(contact_id, inbox_ids)


def list_for_export(inbox_ids: list[int] | None = None) -> list[dict]:
    """Return non-group contacts with full info + tags, for CSV export.

    ``inbox_ids`` scopes by inbox membership (plano inboxes/canais §4.7), mirroring
    ``list_contacts``: ``None`` ⇒ no scoping; empty ⇒ nothing; a list ⇒ only contacts
    with a conversation in one of those inboxes. Includes both archived and active
    contacts (an export should be complete). Groups are skipped — they can't be
    re-imported by phone."""
    return contact_query.list_for_export(inbox_ids)


def iter_for_export(inbox_ids: list[int] | None = None, *, chunk: int = 500):
    """Gerador chunked de contatos p/ export CSV streaming (plano 50 F11). Sem N+1
    (tags em lote por chunk) e memória constante. Ver ``contact_query.iter_for_export``."""
    return contact_query.iter_for_export(inbox_ids, chunk=chunk)


def _shape_contact_row(row, tags_list: list) -> dict:
    """Row (heavy SELECT) → dict de contato para a sidebar/lista. Shape byte-idêntico
    ao antigo loop inline (plano 50 F5 extraiu para reuso entre list/page)."""
    contact_id = row["id"]
    # plano 87: a legenda do cliente entra na preview no lugar do content cru
    # (que, numa mídia descrita pela IA, é "[Descrição da imagem]: …"). Linha
    # vinda de query antiga sem a coluna ⇒ ``None`` ⇒ fallback legado.
    last_content = media_preview(row["last_msg_content"], row["last_msg_media_type"],
                                 row.get("last_msg_media_caption"))
    is_group = bool(row["is_group"])
    group_name = row["group_name"] or ""
    name = group_name if is_group else (row["name"] or "")
    attrs = _coerce_attrs(row)
    return {
        "id": contact_id,
        "phone": row["phone"],
        "name": name,
        # Email is now a (default) custom attribute — source it from the JSON,
        # falling back to the legacy column for not-yet-migrated rows. Kept at
        # top-level so the contacts list can still show it under the name.
        "email": (attrs.get("email") if isinstance(attrs, dict) else None) or (row["email"] or ""),
        "last_message": last_content,
        "last_message_role": row["last_msg_role"] or "",
        "last_message_ts": row["last_msg_ts"] or 0,
        "last_message_status": row["last_msg_status"] or "",
        "last_message_msg_id": row["last_msg_id"] or "",
        "msg_count": row["msg_count"] or 0,
        "unread_count": row["unread_count"],
        "unread_ai_count": row["unread_ai_count"],
        "has_unread_mention": bool(row["has_unread_mention"]),
        # Tipo do contato (plano tipos-de-contato) — alimenta o filtro por
        # tipo na sidebar (client-side) e a marca no painel do contato.
        "contact_type": row["contact_type"] or "outros",
        "ai_enabled": bool(row["ai_enabled"]),
        "is_group": is_group,
        "group_name": group_name,
        "is_archived": bool(row["is_archived"]),
        "archived_by_app": bool(row["archived_by_app"]) if row["archived_by_app"] is not None else False,
        "is_pinned": bool(row["is_pinned"]) if row["is_pinned"] is not None else False,
        "can_send": bool(row["can_send"]) if row["can_send"] is not None else True,
        # Contact-scoped custom attributes (plano 05) — exposed in the list so
        # the client-side contact/conversation filter can match on them
        # (incl. the seeded defaults Email/Profissão/Empresa/Endereço). The
        # column is already SELECTed (full contacts table); coerce_attrs
        # tolerates the SQLite str-serialized form.
        "custom_attributes": attrs,
        "tags": tags_list,
        "updated_at": row["updated_at"],
        # Active conversation (plano 10 FF2) — drives the status/assignment
        # tabs and the assignee shown on each row. NULL for contacts that
        # never got a conversation (treated as open/unassigned by the UI).
        "conversation_id": row["conv_id"],
        "conv_status": row["conv_status"] or "open",
        "assignee_user_id": row["conv_assignee_user_id"],
        "active_agent_key": row["conv_active_agent_key"],
        "conv_ai_active": (
            bool(row["conv_ai_active"]) if row["conv_ai_active"] is not None else True),
    }


def _matched_by_contact_fields(c: dict, folded_q: str) -> bool:
    """A row already matched ``q`` by name/phone/group_name/tag?

    Mirrors the first branch of the pre-F5 Python filter EXACTLY (same fields,
    same folding), because it decides the same thing it used to: whether the row
    gets the content-match decoration. In the old code the decoration was the
    ``elif`` — only a row that matched by NOTHING but message content carried a
    ``match_snippet``. Runs over the rows of ONE page now (≤ ``limit``)."""
    return (folded_q in contact_search.fold(c["name"])
            or folded_q in c["phone"]
            or folded_q in contact_search.fold(c.get("group_name", ""))
            or any(folded_q in contact_search.fold(t) for t in c.get("tags", [])))


def _decorate_content_matches(conn, results: list, q: str, include_messages: bool) -> None:
    """Attach ``match_snippet``/``match_msg_id`` to the page rows that matched only
    by message content (plano 62 F5 — in place of the old 5000-message scan).

    Only the rows of the current page are considered, and only those that did NOT
    match by name/phone/group/tag, so this is one indexed query over ≤ ``limit``
    contact ids. The snippet itself is still built in Python (``match_snippet``
    keeps the ORIGINAL accented text for display)."""
    if not results or not include_messages:
        return
    folded_q = contact_search.fold(q)
    if len(folded_q) < contact_search.MIN_SCAN_QUERY_LEN:
        return  # 1-char search never touches message content (legacy guard)
    pending = [c for c in results if not _matched_by_contact_fields(c, folded_q)]
    if not pending:
        return
    rows = conn.execute(
        contact_search.build_content_matches_query(q, [c["id"] for c in pending])
    ).mappings().all()
    by_contact = {r["contact_id"]: r for r in rows}
    for c in pending:
        hit = by_contact.get(c["id"])
        if hit is None:
            continue
        # Show the matching excerpt so the operator sees why this conversation
        # came up, and the message id so opening it can scroll to that message.
        c["match_snippet"] = contact_search.match_snippet(hit["content"] or "", folded_q)
        c["match_msg_id"] = hit["id"]


def list_contacts(q: str = "", archived: bool = False,
                  inbox_ids: list[int] | None = None, *,
                  include_messages: bool = True, filter_where=None) -> list[dict]:
    """List contacts with last message preview, tags, and unread counts.

    ``inbox_ids`` scopes by inbox membership (plano inboxes/canais §4.7): ``None``
    ⇒ no scoping; empty ⇒ nothing; a list ⇒ only contacts with a conversation in
    one of those inboxes. Mirrors ``conversation_repo.list_conversations`` so the
    contact-centric sidebar não vaza contatos de caixas que o usuário não acessa.

    Retorna a lista COMPLETA (sem teto) — caminho legado para callers internos
    (varredura de avatares) e para o endpoint sem paginação. A versão paginada é
    :func:`list_contacts_page` (plano 50 F5).

    ``include_messages=False`` (plano 62 F5) tira a busca por conteúdo de mensagem
    da cláusula ``q`` — para telas que não renderizam o trecho casado.

    ``filter_where`` (plano 69 F5b): WHERE avançado já compilado (``db.filters``
    ``build_contact_where``) — ``None`` = sem filtro (retrocompatível)."""
    return list_contacts_page(q, archived, inbox_ids, limit=None, offset=0,
                              include_messages=include_messages,
                              filter_where=filter_where)["items"]


def list_contacts_page(q: str = "", archived: bool = False,
                       inbox_ids: list[int] | None = None, *,
                       limit: int | None = None, offset: int = 0,
                       sort: str = "recency",
                       include_messages: bool = True, filter_where=None) -> dict:
    """Página de contatos (plano 50 F5) → ``{items, total, has_more}``.

    ``limit=None`` ⇒ tudo (``total=len``, ``has_more=False``). ``limit`` set ⇒
    ``LIMIT/OFFSET`` no SQL + ``total`` por COUNT, COM ou SEM ``q``: desde o plano
    62 F5 a busca é uma cláusula no ``WHERE`` (:func:`contact_search.build_q_clause`)
    em vez de um filtro Python pós-SELECT, então o custo é por PÁGINA e não pelo
    universo. ``match_snippet``/``match_msg_id`` são resolvidos depois, só para as
    linhas da página.

    ``include_messages=False`` remove o ramo de conteúdo de mensagem da busca
    (mais barato; a tela que não mostra o trecho casado não precisa dele).

    ``filter_where`` (plano 69 F5b): um ``ColumnElement`` já compilado por
    ``db.filters.build_contact_where`` (tag/contact_type/cattr:contact:*). Como só
    referencia ``contacts.c.*`` + subqueries (nunca os LATERALs), entra no ``WHERE``
    da lista E do COUNT — então o ``total``/``has_more`` refletem o filtro, igual ao
    ``q_clause``. ``None`` = sem filtro (caminho byte-idêntico ao antigo)."""
    if inbox_ids is not None and not inbox_ids:
        return {"items": [], "total": 0, "has_more": False}  # sem inbox → nada

    q_clause = contact_search.build_q_clause(q, include_messages=include_messages)

    with get_engine().connect() as conn:
        stmt = contact_search.build_list_contacts_query(
            archived=archived, inbox_ids=inbox_ids, sort=sort)
        if q_clause is not None:
            stmt = stmt.where(q_clause)
        if filter_where is not None:
            stmt = stmt.where(filter_where)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        rows = conn.execute(stmt).mappings().all()
        # N+1 fix: batch-load das tags de toda a página em UMA query.
        tags_map = contact_query.tags_by_contact(conn, [r["id"] for r in rows])
        results = [_shape_contact_row(r, tags_map.get(r["id"], [])) for r in rows]
        if q_clause is not None:
            _decorate_content_matches(conn, results, q, include_messages)
        if limit is None:
            return {"items": results, "total": len(results), "has_more": False}
        count_stmt = contact_search.build_count_contacts_query(
            archived=archived, inbox_ids=inbox_ids)
        if q_clause is not None:
            count_stmt = count_stmt.where(q_clause)
        if filter_where is not None:
            count_stmt = count_stmt.where(filter_where)
        total = conn.execute(count_stmt).scalar() or 0
        return {"items": results, "total": total,
                "has_more": (offset + len(results)) < total}


def get_full_contact(phone: str) -> dict | None:
    """Get full contact data for API response (contact + info + observations)."""
    variants = _br_phone_variants(phone)
    return contact_query.get_full_contact(variants)


def list_avatar_targets() -> list[dict]:
    """Minimal contact listing for the background avatar sweep (plano 62 F7).

    Returns ``id``/``phone``/``is_group``/``is_archived`` of ALL contacts
    (archived included) in a single plain SELECT — no joins, no previews, no
    tags. Replaces the two heavy :func:`list_contacts` calls (full enriched
    query, active + archived) the sweep used to make every pass."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(contacts.c.id, contacts.c.phone,
                   contacts.c.is_group, contacts.c.is_archived)
        ).mappings().all()
    return [dict(r) for r in rows]
