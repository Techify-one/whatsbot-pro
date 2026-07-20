"""Contact search/filter clause building (plano 23 Fase E2).

Owns the text-search side of ``contact_repo.list_contacts``:

- ``fold`` / ``match_snippet`` — accent/case-insensitive folding and the excerpt
  centered on a match (display keeps the original, accented text).
- ``contact_ids_matching_message`` — the "find a conversation by something said
  in it" message scan.
- ``build_list_contacts_query`` — the heavy ``list_contacts`` SELECT, built
  with SQLAlchemy Core. Since plano 62 F1 the per-contact "latest row" lookups
  (``lm``/``conv``) are LEFT JOIN LATERAL ``ORDER BY … LIMIT 1`` probes instead
  of ``MAX()`` self-join subqueries: the self-join form made the Postgres
  planner misestimate the join (Nested Loop + Materialize over the full
  ``messages`` aggregate — ~70× slower in production). Postgres-only, like the
  rest of the app (plano 29).

These are pure query/string builders — ``contact_repo`` keeps owning the
row→dict shaping and the post-fetch filtering loop, so the public API is
unchanged.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy import Select
from sqlalchemy import func, literal, select, true

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


# Teto de trabalho da busca por conteúdo (plano 50 F6): varre no máximo as N mensagens
# MAIS RECENTES em vez da tabela `messages` inteira. Bound o custo por busca a algo
# constante independente do total de mensagens. Trade-off: um match que só existe em
# mensagens muito antigas (além das N recentes) pode escapar — aceitável p/ o caso de
# uso "achar uma conversa por algo dito recentemente". Caminho futuro = FTS/tsvector
# (plano P2). Nomes/telefone/tags NÃO usam este scan (casam sempre).
MESSAGE_SCAN_CAP = 5000

# Comprimento mínimo da query para ACIONAR o scan de conteúdo. Uma busca de 1 caractere
# casaria quase tudo e forçaria a varredura inteira sem valor — nome/telefone/tag ainda
# funcionam para 1 char (não dependem do scan).
MIN_SCAN_QUERY_LEN = 2


def contact_ids_matching_message(conn, folded_q: str, archived: bool) -> dict[int, dict]:
    """Map of contact id -> ``{"snippet": str, "id": int}`` for contacts (within the
    given archived scope) that have at least one message whose content matches
    ``folded_q`` (accent/case-insensitive, like names). The match comes from the most
    recent matching message; ``id`` is its DB primary key (so the UI can scroll to it).

    Covers normal messages, private notes and transcriptions; only the purely
    internal roles (tool calls, system notices) are skipped. Revoked messages are
    kept in the DB with their content, so they remain searchable too.

    Runs on the caller's ``conn`` (same connection used by ``list_contacts``).

    Plano 50 F6: varre só as ``MESSAGE_SCAN_CAP`` mensagens mais recentes (teto de
    custo) e exige ``len(folded_q) >= MIN_SCAN_QUERY_LEN`` (busca de 1 char não dispara
    o scan — nome/telefone/tag já cobrem esse caso).
    """
    if not folded_q or len(folded_q) < MIN_SCAN_QUERY_LEN:
        return {}

    # Most recent first, so the first match seen per contact is the freshest one.
    # `.limit(MESSAGE_SCAN_CAP)` bound the scan às N recentes (F6).
    stmt = (
        select(messages.c.id, messages.c.contact_id, messages.c.content)
        .select_from(messages.join(contacts, contacts.c.id == messages.c.contact_id))
        .where(contacts.c.is_archived == (1 if archived else 0))
        .where(messages.c.content != "")
        .where(messages.c.role.notin_(
            ("tool_call", "system_notice", "conversation_event", "system")))
        .order_by(messages.c.ts.desc())
        .limit(MESSAGE_SCAN_CAP)
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
                              inbox_ids: list[int] | None,
                              sort: str = "recency") -> Select:
    """Build the ``list_contacts`` SELECT as a SQLAlchemy Core statement.

    Plano 62 F1 — the per-contact "latest row" lookups are LEFT JOIN LATERAL
    (correlated ``ORDER BY … LIMIT 1``, one index-backed probe per contact)
    instead of the previous ``MAX()`` self-join subqueries. The self-join form
    made the Postgres planner misestimate the join cardinality (Nested Loop +
    Materialize over the whole ``messages`` aggregate — ~70× slower measured in
    production). The LATERAL form also DEDUPLICATES ties: two messages of the
    same contact with an identical ``ts`` yield ONE contact row, not two.

    - ``lm`` — the latest VISIBLE message per contact (roles in
      ``_PREVIEW_EXCLUDED`` excluded): ``LATERAL (SELECT … WHERE contact_id =
      contacts.id AND role NOT IN (…) ORDER BY ts DESC LIMIT 1)``.
    - ``conv`` — the ACTIVE conversation per contact (the highest id):
      ``LATERAL (SELECT … WHERE contact_id = contacts.id ORDER BY id DESC
      LIMIT 1)``.
    - ``msg_count`` — literal ``0``. The correlated per-contact COUNT was dead
      weight (zero consumers front/back — plano 62 F1); the key is kept only to
      preserve the public row shape.
    - the inbox scope — a Core ``.exists()`` correlated subquery, only when
      ``inbox_ids`` is not None.

    Ordering and selected labels match the original exactly so the row→dict shaping
    in ``contact_repo`` is unchanged. ``inbox_ids`` is assumed non-empty when not
    None (the empty-list "sees nothing" shortcut is handled by the caller).
    """
    # lm: latest visible message per contact (LEFT JOIN LATERAL … LIMIT 1,
    # preview roles out — plano 62 F1).
    visible = messages.c.role.notin_(_PREVIEW_EXCLUDED)
    lm = (
        select(
            messages.c.content.label("content"),
            messages.c.role.label("role"),
            messages.c.ts.label("ts"),
            messages.c.media_type.label("media_type"),
            messages.c.status.label("status"),
            messages.c.msg_id.label("msg_id"),
        )
        .where(messages.c.contact_id == contacts.c.id)
        .where(visible)
        # id DESC tie-breaker: two visible messages with an identical ts would
        # otherwise leave the picked row planner-dependent (plano 62 F1 review).
        .order_by(messages.c.ts.desc(), messages.c.id.desc())
        .limit(1)
        .correlate(contacts)
        .lateral("lm")
    )

    # conv: active conversation per contact (LEFT JOIN LATERAL … LIMIT 1 on the
    # highest id — plano 62 F1).
    conv = (
        select(
            conversations.c.id.label("conv_id"),
            conversations.c.status.label("conv_status"),
            conversations.c.assignee_user_id.label("conv_assignee_user_id"),
            conversations.c.active_agent_key.label("conv_active_agent_key"),
            conversations.c.ai_active.label("conv_ai_active"),
        )
        .where(conversations.c.contact_id == contacts.c.id)
        .order_by(conversations.c.id.desc())
        .limit(1)
        .correlate(contacts)
        .lateral("conv")
    )

    # Dead column kept as a fixed 0 for row-shape compat (plano 62 F1 — the
    # correlated COUNT had zero consumers and cost a full per-contact scan).
    msg_count = literal(0)

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
            .outerjoin(lm, true())
            .outerjoin(conv, true())
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

    if sort == "name":
        # Tela /contacts (full-page): ordem alfabética por nome, caindo no telefone.
        # (plano 50 F7 — a sidebar segue por recência via o default abaixo.)
        stmt = stmt.order_by(
            func.coalesce(func.nullif(contacts.c.name, ""), contacts.c.phone),
            contacts.c.phone,
        )
    else:
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
