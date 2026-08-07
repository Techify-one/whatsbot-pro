"""Contact search/filter clause building (plano 23 Fase E2).

Owns the text-search side of ``contact_repo.list_contacts``:

- ``fold`` / ``match_snippet`` — accent/case-insensitive folding and the excerpt
  centered on a match (display keeps the original, accented text).
- ``build_q_clause`` — the search ``q`` as a SQL boolean expression (plano 62
  F5), so the WHERE does the matching and ``LIMIT/OFFSET`` paginates for real.
- ``build_content_matches_query`` — the per-page lookup that backs
  ``match_snippet``/``match_msg_id`` (only for the rows actually shown).
- ``contact_ids_matching_message`` — LEGACY message scan (pre-F5). No longer on
  the search path; kept because it is still exercised directly by
  ``tests/test_endpoints.py``.
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

from sqlalchemy import ColumnElement, Select, String
from sqlalchemy import and_, func, literal, or_, select, true

from db.repositories._mapping import _PREVIEW_EXCLUDED
from db.tables import contact_tags, contacts, conversations, messages, tags


def fold(s: str) -> str:
    """Casefold and strip accents so search matches regardless of diacritics.

    "Ó"/"ó"/"o" all fold to "o", so typing an unaccented letter still finds
    accented names (and vice-versa).

    Still used for the SNIPPET (``match_snippet``) and for deciding which page
    rows need the content lookup; the MATCHING itself moved to SQL in plano 62
    F5 (``f_unaccent(lower(x)) ILIKE …`` — see :func:`_folded_match`).

    NOTE (plano 62 F5): the two folds agree on every PT-BR case (accents,
    cedilla, upper/lower), but NOT in exotic ones — ``casefold()`` expands ``ß``
    → ``ss`` and NFKD splits ligatures (``ﬁ`` → ``fi``), which
    ``lower()``/``unaccent`` do not; conversely ``unaccent`` transliterates some
    non-decomposable letters (``Æ`` → ``AE``) that NFKD leaves alone. Parity is
    targeted at PT-BR text; the divergence is accepted (plano 62 §5 Riscos).
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

# Piso do ramo de CONTEÚDO no caminho SQL (plano 62 F5, fix da revisão de perf).
# O `pg_trgm` só extrai trigramas com >= 3 caracteres; com 2, o `ILIKE '%xx%'` sobre
# o índice GIN degenera para um FULL SCAN (devolve TODAS as mensagens não-excluídas —
# ~534k em produção — independente de quantas casam), ficando ~1,7× MAIS lento que a
# busca antiga. Então o ramo de conteúdo só entra com >= 3 chars; nome/telefone/tag/
# grupo continuam casando a partir de 1 char (não dependem do trigrama). Trade-off:
# buscar CONTEÚDO de mensagem por 2 chars deixa de funcionar (casava quase tudo e era
# ruído) — mudança de comportamento deliberada e documentada.
TRIGRAM_MIN_LEN = 3

# Roles NUNCA pesquisáveis por conteúdo (puramente internos). Este é o MESMO
# conjunto do predicado dos índices parciais ``idx_msg_ts_visible`` (0059) e
# ``idx_msg_content_trgm`` (0060): se divergir, o índice deixa de ser aplicável e
# a busca por conteúdo volta a varrer a tabela inteira. NÃO confundir com
# ``_PREVIEW_EXCLUDED`` (7 roles), que é o filtro do PREVIEW da lista.
SEARCH_EXCLUDED_ROLES = ("tool_call", "system_notice", "conversation_event", "system")


def contact_ids_matching_message(conn, folded_q: str, archived: bool) -> dict[int, dict]:
    """LEGACY (pre plano 62 F5) — no longer used by the search path.

    Superseded by :func:`build_q_clause` (matching moved into the WHERE) plus
    :func:`build_content_matches_query` (snippet only for the rows of the page).
    Kept because ``tests/test_endpoints.py`` still calls it directly to assert the
    ``MIN_SCAN_QUERY_LEN`` guard.

    Map of contact id -> ``{"snippet": str, "id": int}`` for contacts (within the
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
        .where(messages.c.role.notin_(SEARCH_EXCLUDED_ROLES))
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


# ── Busca `q` NO SQL (plano 62 F5) ───────────────────────────────────────────

def _escape_like(s: str) -> str:
    """Escape the LIKE metacharacters of ``s`` (backslash is Postgres' default
    LIKE escape). The Python ``in`` this replaces had no wildcards, so a ``%``
    or ``_`` typed by the operator must keep matching itself."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _folded(col) -> ColumnElement[str]:
    """``f_unaccent(lower(col))`` — the EXACT expression indexed by
    ``idx_contacts_name_trgm``/``idx_msg_content_trgm`` (migration 0060). Any
    deviation (extra cast, different nesting) makes the trigram index
    inapplicable, so keep this the single place that spells it."""
    return func.f_unaccent(func.lower(col), type_=String)


def _folded_pattern(q: str) -> ColumnElement[str]:
    """``'%' || f_unaccent(lower(:q)) || '%'`` — the pattern for ``q``, folded by
    the SAME expression as the column side (:func:`_folded`) so both halves
    normalize identically. ``f_unaccent``/``lower`` are IMMUTABLE, so Postgres
    constant-folds this at plan time and pg_trgm can still extract the trigrams.
    Bind param — never interpolated."""
    esc = literal(_escape_like(q), String)
    return literal("%", String).concat(func.f_unaccent(func.lower(esc), type_=String)).concat(
        literal("%", String))


def _folded_match(col, pattern: ColumnElement[str]) -> ColumnElement[bool]:
    """``f_unaccent(lower(col)) ILIKE pattern`` — the folded text comparison.

    ⚠️ ILIKE, not LIKE, and that is load-bearing. Both the production and the
    test clusters run with ``lc_collate = 'C'``, where ``lower()`` only lowers
    ASCII: ``lower('ORÇAMENTO')`` is ``'orÇamento'``, so
    ``f_unaccent(lower(…))`` yields ``'orCamento'`` — an ASCII capital left
    behind by ``unaccent`` AFTER ``lower()`` already ran. Folding the query the
    same way does not help (the residual capitals land in different positions on
    each side: column ``'orcamento'`` vs query ``'orCamento'``), so a plain
    ``LIKE`` silently misses every accented UPPERCASE search — caught by the F0
    characterization tests (``óLiViA``, ``ORÇAMENTO``).

    ILIKE closes the gap: whatever ``unaccent`` leaves is plain ASCII, and ASCII
    case-insensitivity works under the C collation. The expression on the column
    side stays byte-identical to the 0060 index, and ``gin_trgm_ops`` supports
    ``ILIKE`` (``~~*``) just like ``LIKE``.

    (The alternative — indexing ``lower(f_unaccent(col))``, which folds correctly
    in one pass — would need the 0060 indexes rebuilt; noted as follow-up.)
    """
    return _folded(col).ilike(pattern)


def message_content_predicate(pattern: ColumnElement[str]) -> ColumnElement[bool]:
    """The searchable-message predicate: non-empty content, role not internal,
    content matches ``pattern``. The role filter MUST mirror
    ``SEARCH_EXCLUDED_ROLES`` (= the ``idx_msg_content_trgm`` partial predicate)."""
    return and_(
        messages.c.content != "",
        messages.c.role.notin_(SEARCH_EXCLUDED_ROLES),
        _folded_match(messages.c.content, pattern),
    )


def build_q_clause(q: str, *, include_messages: bool = True) -> ColumnElement[bool] | None:
    """The search ``q`` as a SQL boolean expression over ``contacts`` (plano 62 F5).

    Replaces the post-SELECT Python filter (``contact_repo._apply_q_filter``), so
    the search paginates in the database instead of shaping the whole universe
    and slicing in memory. Returns ``None`` for an empty ``q`` (caller skips the
    WHERE). Matches the same surface the Python filter did, OR-ed:

    - ``contacts.name`` and ``contacts.group_name`` (accent/case-insensitive).
      The Python filter tested the SHAPED ``name``, which for a group is already
      ``group_name`` — so it effectively ignored a group's raw ``contacts.name``.
      Testing both raw columns is a deliberate (tiny) superset: a group whose
      stored ``name`` matches now surfaces too.
    - ``contacts.phone`` — substring of the RAW phone against the FOLDED ``q``,
      exactly like the Python filter did. Digits are unchanged by folding, so
      this is behavior-preserving; folding just keeps the two halves consistent.
    - any of the contact's tags (``contact_tags ⋈ tags``).
    - if ``include_messages``: any searchable message of the contact
      (``SEARCH_EXCLUDED_ROLES`` skipped, empty content skipped). Gated by
      ``TRIGRAM_MIN_LEN`` (=3) on the FOLDED ``q`` — a 1-2 char search still
      matches name/phone/tag/group but never content, because pg_trgm needs 3
      chars to prune and a 2-char content ``ILIKE`` would full-scan the whole
      table (plano 62 F5 perf fix). Unlike the legacy scan there is NO
      ``MESSAGE_SCAN_CAP``: the trigram index makes the whole history searchable
      (it used to reach back ~5 days in production).

    The tag/message branches are UNCORRELATED ``IN (SELECT …)`` rather than
    correlated ``EXISTS``: inside an ``OR`` Postgres cannot turn a correlated
    ``EXISTS`` into a semi-join, so it would re-run the subplan once per contact
    row. The uncorrelated form is evaluated ONCE (hashed), which is what lets
    ``idx_msg_content_trgm`` be scanned a single time.
    """
    if not q:
        return None
    folded = fold(q)
    pattern = _folded_pattern(q)

    tag_match = select(contact_tags.c.contact_id).select_from(
        contact_tags.join(tags, tags.c.id == contact_tags.c.tag_id)
    ).where(_folded_match(tags.c.name, pattern))

    clauses = [
        _folded_match(contacts.c.name, pattern),
        _folded_match(contacts.c.group_name, pattern),
        # Folded q vs raw phone — see docstring (digits survive folding).
        contacts.c.phone.like(literal("%" + _escape_like(folded) + "%", String)),
        contacts.c.id.in_(tag_match),
    ]

    if include_messages and len(folded) >= TRIGRAM_MIN_LEN:
        content_match = (
            select(messages.c.contact_id)
            .where(message_content_predicate(pattern))
        )
        clauses.append(contacts.c.id.in_(content_match))

    return or_(*clauses)


def build_content_matches_query(q: str, contact_ids: list[int]) -> Select:
    """Most recent searchable message matching ``q``, one row per contact id.

    Backs ``match_snippet``/``match_msg_id`` for the rows of the CURRENT page
    only (≤ ``limit`` contacts), instead of the legacy scan over the 5000 newest
    messages of the whole database. ``DISTINCT ON (contact_id)`` + ``ORDER BY
    contact_id, ts DESC, id DESC`` picks the freshest match deterministically
    (the legacy scan relied on ``ts DESC`` row order, leaving ties to the
    planner)."""
    return (
        select(messages.c.contact_id, messages.c.id, messages.c.content)
        .where(messages.c.contact_id.in_(contact_ids))
        .where(message_content_predicate(_folded_pattern(q)))
        .order_by(messages.c.contact_id, messages.c.ts.desc(), messages.c.id.desc())
        .distinct(messages.c.contact_id)
    )


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
            messages.c.media_caption.label("media_caption"),
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
            # plano 87: legenda do cliente para a preview (ver `media_preview`).
            lm.c.media_caption.label("last_msg_media_caption"),
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
    índice em ``contacts``, não a query pesada).

    Plano 62 F5: vale também para o caminho COM busca — o caller adiciona a mesma
    cláusula de :func:`build_q_clause` no ``WHERE`` (ela só referencia ``contacts``
    + subqueries, nunca os LATERALs), então o ``total`` continua sendo o universo
    filtrado, agora contado no banco em vez de ``len()`` da lista em memória."""
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
