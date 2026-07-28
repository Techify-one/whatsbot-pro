"""Characterization of ``contact_repo.list_contacts_page`` (Plano 62 · Fase F0).

Freezes the CURRENT observable behavior of the contact list/search — with and
without ``q`` — BEFORE the F1 (LATERAL rewrite of the ``lm``/``conv`` self-joins
+ ``msg_count`` removal) and F5 (SQL-side ``q`` matching) optimizations. F1/F5
must keep this suite green to prove equivalence.

Covered behavior (all against the CURRENT code — repo layer only, no HTTP):

  * no-``q`` ordering — ``sort="recency"`` (pinned first, then
    ``coalesce(last VISIBLE msg ts, updated_at) DESC``; ``_PREVIEW_EXCLUDED``
    roles never drive ``last_msg_*``) and ``sort="name"``
    (``coalesce(nullif(name,''), phone)`` — NOTE: a group with empty
    ``contacts.name`` sorts by PHONE even though the shaped row displays
    ``group_name``);
  * ``q`` matching — name accent/case-insensitive in BOTH directions, phone
    substring, group_name, tag accent-insensitive;
  * ``q`` by message CONTENT only — row gains ``match_snippet`` (original
    accented text) + ``match_msg_id`` (most recent matching msg); private_note
    IS searchable, tool_call is NOT;
  * 1-char ``q`` — name/phone still match but the content scan is NOT triggered
    (``MIN_SCAN_QUERY_LEN``);
  * pagination with ``q`` — Python-side slice of the filtered list
    (``total`` = filtered size, ``has_more = offset+len < total``);
  * ``archived=True`` scoping;
  * row shape — ``msg_count`` present as int >= 0 (value NOT asserted: F1
    pinned it to 0) and the ``conv_*`` columns;
  * ts TIE (2 visible msgs, same contact, identical ts) — the pre-F1
    ``MAX(ts)`` self-join DUPLICATED the contact row; the F1 LATERAL rewrite
    deduplicates, so the test now asserts exactly one row (the xfail marker
    was removed when F1 landed — "dedupe é correção, não regressão").

Discipline: ADDITIVE only — no app/source change; real bugs are captured and
flagged with ``# CHARACTERIZED BUG:`` comments.

Dataset: seeded once per module with a phone prefix unique to this file
(``77900062…`` — non-BR so ``br_phone_variants`` never collapses them), so it
never collides with the conftest seed or other suites on the shared process DB.
All membership/order assertions are RELATIVE (filtered to this file's phones),
robust to unrelated contacts existing in the session DB.
"""

from __future__ import annotations

import time

import pytest


# Phone prefix unique to this file (shared process DB — see module docstring).
PREFIX = "77900062"

P_JOSE = PREFIX + "01"       # "José Ávila", tag "Orçamento", has a conversation
P_OLIVIA = PREFIX + "02"     # "OLÍVIA prado", private_note NEWER than last visible
P_GROUP = PREFIX + "03"      # group, group_name "Grupo Xavantes", empty name
P_CONTENT = PREFIX + "04"    # "Cliente Silva" — matches only by msg content
P_NOTE = PREFIX + "05"       # "Contato Nota" — matches only via a private_note
P_TOOL = PREFIX + "06"       # "Contato Ferramenta" — token only in a tool_call
P_DIGIT = PREFIX + "18"      # "Contato Oito" — phone contains "8" (1-char q)
P_CONTENT8 = PREFIX + "09"   # "Contato Codigo" — "8" only in msg content
P_PINNED = PREFIX + "30"     # "Fixado Teste" — pinned, OLDEST message
P_ARCHIVED = PREFIX + "31"   # "Arquivado Teste" — archived
P_TIE = PREFIX + "32"        # "Empate Teste" — 2 visible msgs with identical ts


@pytest.fixture(scope="module")
def dataset(_engine_ready) -> dict:
    """Seed this file's dataset once per process. Returns ids/ts used in asserts."""
    from db.repositories import (
        config_repo, contact_repo, conversation_repo, message_repo, tag_repo,
    )

    base = time.time() - 5000.0
    data: dict = {"base": base}

    def make(phone: str, name: str = "", **fields) -> int:
        c = contact_repo.get_or_create(phone)
        if name or fields:
            contact_repo.update(c["id"], name=name, **fields)
        data[phone] = c["id"]
        return c["id"]

    # P_JOSE — accented/mixed-case name + accented tag + a conversation.
    jose = make(P_JOSE, "José Ávila")
    message_repo.add(jose, "user", "Olá, preciso de ajuda", ts=base + 10)
    message_repo.add(jose, "assistant", "Claro, posso ajudar!", ts=base + 20)
    tag_repo.create("Orçamento", "#ff8800")
    tag_repo.add_contact_tag(jose, "Orçamento")

    # Pin the AI-seed config so the conversation's initial state is deterministic
    # regardless of what other suites left in the shared config table.
    config_repo.set("auto_reply", True)
    config_repo.set("default_ai_enabled", True)
    conv, _ = conversation_repo.resolve_for_contact_ex(
        jose, f"{P_JOSE}@s.whatsapp.net")
    data["conv_id"] = conv["id"]

    # P_OLIVIA — the private_note is NEWER than the last visible message; the
    # preview (last_msg_*) must ignore it (_PREVIEW_EXCLUDED).
    olivia = make(P_OLIVIA, "OLÍVIA prado")
    message_repo.add(olivia, "user", "Bom dia", ts=base + 30)
    message_repo.add(olivia, "private_note", "nota interna particular", ts=base + 100)

    # P_GROUP — group with group_name and EMPTY contacts.name.
    group = make(P_GROUP, "", is_group=1, group_name="Grupo Xavantes")
    message_repo.add(group, "user", "mensagem do grupo", ts=base + 40)

    # P_CONTENT — matches "manutencao"/"zumbaxi" ONLY by message content.
    content = make(P_CONTENT, "Cliente Silva")
    m_old = message_repo.add(
        content, "user", "A manutenção zumbaxi falhou ontem", ts=base + 50)
    m_new = message_repo.add(
        content, "assistant", "Vamos revisar a manutenção zumbaxi amanhã", ts=base + 60)
    data["content_msg_old_id"] = m_old["id"]
    data["content_msg_new_id"] = m_new["id"]

    # P_NOTE — the searchable token lives ONLY in a private_note.
    note = make(P_NOTE, "Contato Nota")
    message_repo.add(note, "user", "oi", ts=base + 70)
    pn = message_repo.add(
        note, "private_note", "registro confidencial plimplim", ts=base + 75)
    data["note_msg_id"] = pn["id"]

    # P_TOOL — the token lives ONLY in a tool_call (NOT searchable).
    tool = make(P_TOOL, "Contato Ferramenta")
    message_repo.add(tool, "user", "olá tudo bem", ts=base + 80)
    message_repo.add(tool, "tool_call", "resultado quixote interno", ts=base + 85)

    # P_DIGIT — phone contains "8"; P_CONTENT8 — "8" appears only in content.
    digit = make(P_DIGIT, "Contato Oito")
    message_repo.add(digit, "user", "sem token relevante", ts=base + 82)
    content8 = make(P_CONTENT8, "Contato Codigo")
    message_repo.add(content8, "user", "código 8 confirmado", ts=base + 84)

    # P_PINNED — oldest message but pinned → must sort FIRST in recency.
    pinned = make(P_PINNED, "Fixado Teste")
    message_repo.add(pinned, "user", "mensagem antiga", ts=base + 5)
    contact_repo.set_pinned(pinned, True)

    # P_ARCHIVED — archived scope.
    archived = make(P_ARCHIVED, "Arquivado Teste")
    message_repo.add(archived, "user", "histórico arquivado", ts=base + 90)
    contact_repo.set_archived(archived, True)

    # P_TIE — two VISIBLE messages with the exact same ts (self-join tie case).
    tie = make(P_TIE, "Empate Teste")
    message_repo.add(tie, "user", "empate um", ts=base + 95)
    message_repo.add(tie, "assistant", "empate dois", ts=base + 95)

    return data


# ── Helpers ──────────────────────────────────────────────────────────────────

def _page(q: str = "", archived: bool = False, **kw) -> dict:
    from db.repositories import contact_repo
    return contact_repo.list_contacts_page(q, archived, None, **kw)


def _phones(items: list[dict]) -> list[str]:
    return [c["phone"] for c in items]


def _row(items: list[dict], phone: str) -> dict:
    matches = [c for c in items if c["phone"] == phone]
    assert matches, f"contact {phone} not in result"
    return matches[0]


def _ordered_subset(items: list[dict], expected_phones: list[str]) -> list[str]:
    """The result's phone sequence restricted to ``expected_phones`` (relative
    order assertion — robust to unrelated contacts interleaving on the shared DB)."""
    want = set(expected_phones)
    return [p for p in _phones(items) if p in want]


# ── 1. no q, sort=recency ────────────────────────────────────────────────────

def test_recency_order_pinned_first_and_preview_excluded_roles(dataset):
    """Pinned first, then coalesce(last VISIBLE msg ts, updated_at) DESC; the
    last_msg_* columns ignore _PREVIEW_EXCLUDED roles (private_note/tool_call…)."""
    items = _page()["items"]

    # P_TIE excluded from the order assertion: it has its own dedicated test
    # (test_ts_tie_contact_appears_once) and would just lengthen the sequence.
    expected = [
        P_PINNED,      # pinned → first despite the OLDEST ts (base+5)
        P_CONTENT8,    # base+84
        P_DIGIT,       # base+82
        P_TOOL,        # base+80 (tool_call at +85 is NOT visible)
        P_NOTE,        # base+70 (private_note at +75 is NOT visible)
        P_CONTENT,     # base+60
        P_GROUP,       # base+40
        P_OLIVIA,      # base+30 (private_note at +100 is NOT visible)
        P_JOSE,        # base+20
    ]
    assert _ordered_subset(items, expected) == expected

    # last_msg_* must come from the last VISIBLE message, not the newer
    # private_note/tool_call rows.
    olivia = _row(items, P_OLIVIA)
    assert olivia["last_message"] == "Bom dia"
    assert olivia["last_message_role"] == "user"
    assert olivia["last_message_ts"] == pytest.approx(dataset["base"] + 30)

    tool = _row(items, P_TOOL)
    assert tool["last_message"] == "olá tudo bem"
    assert tool["last_message_ts"] == pytest.approx(dataset["base"] + 80)


# ── 2. no q, sort=name ───────────────────────────────────────────────────────

def test_name_sort_alphabetical_with_phone_fallback(dataset):
    """sort="name" orders by coalesce(nullif(contacts.name,''), phone).

    CHARACTERIZED: the group (empty ``contacts.name``) sorts by its PHONE (digits
    before letters) even though the shaped row displays ``group_name`` — the SQL
    ORDER BY never looks at ``group_name``."""
    items = _page(sort="name")["items"]

    expected = [
        P_GROUP,     # empty name → phone "7790006203" (digits sort first)
        P_CONTENT,   # Cliente Silva
        P_CONTENT8,  # Contato Codigo
        P_TOOL,      # Contato Ferramenta
        P_NOTE,      # Contato Nota
        P_DIGIT,     # Contato Oito
        P_PINNED,    # Fixado Teste (pinning does NOT affect name sort)
        P_JOSE,      # José Ávila
        P_OLIVIA,    # OLÍVIA prado
    ]
    assert _ordered_subset(items, expected) == expected

    # The group row still DISPLAYS the group_name.
    assert _row(items, P_GROUP)["name"] == "Grupo Xavantes"


# ── 3. q by name — accent/case-insensitive, both directions ──────────────────

def test_q_name_accent_and_case_insensitive_both_ways(dataset):
    # Unaccented lowercase query finds the accented name…
    items = _page("jose")["items"]
    row = _row(items, P_JOSE)
    # Matched by NAME → no content-match decoration.
    assert "match_snippet" not in row
    assert "match_msg_id" not in row

    # …and an accented mixed-case query finds the (differently-cased) name.
    items = _page("óLiViA")["items"]
    assert P_OLIVIA in _phones(items)

    # accent in the query, none in the stored half: "avila" finds "Ávila".
    items = _page("avila")["items"]
    assert P_JOSE in _phones(items)

    # group_name is searchable too.
    items = _page("xavantes")["items"]
    assert P_GROUP in _phones(items)


# ── 4. q by phone substring ──────────────────────────────────────────────────

def test_q_phone_substring(dataset):
    items = _page("006201")["items"]
    assert P_JOSE in _phones(items)
    assert P_OLIVIA not in _phones(items)


# ── 5. q by tag — accent-insensitive ─────────────────────────────────────────

def test_q_tag_accent_insensitive(dataset):
    items = _page("orcamento")["items"]
    assert P_JOSE in _phones(items)

    items = _page("ORÇAMENTO")["items"]
    assert P_JOSE in _phones(items)


# ── 6. q by message content → match_snippet/match_msg_id ─────────────────────

def test_q_message_content_snippet_and_msg_id(dataset):
    """Content-only match decorates the row with match_snippet (ORIGINAL accented
    text) + match_msg_id (the MOST RECENT matching message's DB id)."""
    items = _page("manutencao")["items"]
    row = _row(items, P_CONTENT)
    assert "manutenção" in row["match_snippet"]          # accents preserved
    assert row["match_msg_id"] == dataset["content_msg_new_id"]  # newest match


def test_q_private_note_is_searchable(dataset):
    items = _page("plimplim")["items"]
    row = _row(items, P_NOTE)
    assert "plimplim" in row["match_snippet"]
    assert row["match_msg_id"] == dataset["note_msg_id"]


def test_q_tool_call_is_not_searchable(dataset):
    items = _page("quixote")["items"]
    assert P_TOOL not in _phones(items)


# ── 7. 1-char q — no content scan ────────────────────────────────────────────

def test_q_single_char_matches_phone_but_skips_content_scan(dataset):
    """len(q) < MIN_SCAN_QUERY_LEN: name/phone/tag still match, but the message
    content scan is NOT triggered — a contact that would only match by content
    does NOT appear."""
    items = _page("8")["items"]
    assert P_DIGIT in _phones(items)          # phone "…18" contains "8"
    assert P_CONTENT8 not in _phones(items)   # "8" only in msg content → no scan


# ── 8. pagination with q ─────────────────────────────────────────────────────

def test_pagination_with_q_slices_filtered_list(dataset):
    """With ``q`` the page is a Python-side slice of the FULL filtered list:
    ``total`` = filtered size, ``items`` = ``full[offset:offset+limit]``,
    ``has_more = (offset + len(items)) < total``."""
    full = _page(PREFIX)  # phone-prefix q → exactly this file's active contacts
    full_items = full["items"]
    total = full["total"]
    assert total == len(full_items)
    assert full["has_more"] is False
    assert total >= 3  # sanity: prefix matched the dataset
    assert all(PREFIX in c["phone"] for c in full_items)

    page1 = _page(PREFIX, limit=3, offset=0)
    assert _phones(page1["items"]) == _phones(full_items[:3])
    assert page1["total"] == total
    assert page1["has_more"] is (3 < total)

    page2 = _page(PREFIX, limit=3, offset=3)
    assert _phones(page2["items"]) == _phones(full_items[3:6])
    assert page2["total"] == total
    assert page2["has_more"] is ((3 + len(page2["items"])) < total)

    tail = _page(PREFIX, limit=5, offset=total - 1)
    assert len(tail["items"]) == 1
    assert tail["has_more"] is False

    beyond = _page(PREFIX, limit=5, offset=total + 5)
    assert beyond["items"] == []
    assert beyond["has_more"] is False


# ── 9. archived scoping ──────────────────────────────────────────────────────

def test_archived_scope(dataset):
    active = _phones(_page(archived=False)["items"])
    assert P_ARCHIVED not in active
    assert P_JOSE in active

    archived = _phones(_page(archived=True)["items"])
    assert P_ARCHIVED in archived
    for phone in (P_JOSE, P_OLIVIA, P_GROUP, P_CONTENT, P_PINNED):
        assert phone not in archived


# ── 10. row shape ────────────────────────────────────────────────────────────

def test_row_shape_msg_count_and_conv_fields(dataset):
    items = _page()["items"]

    jose = _row(items, P_JOSE)
    # msg_count: present and a non-negative int. Value NOT asserted — F1
    # stopped computing it (fixed 0 for shape compat, plano 62 F1).
    assert isinstance(jose["msg_count"], int)
    assert jose["msg_count"] >= 0
    # conv_* columns from the active conversation.
    assert jose["conversation_id"] == dataset["conv_id"]
    assert jose["conv_status"] == "open"
    assert jose["assignee_user_id"] is None
    assert "active_agent_key" in jose
    assert isinstance(jose["conv_ai_active"], bool)

    # A contact WITHOUT any conversation: NULL-shaped defaults.
    olivia = _row(items, P_OLIVIA)
    assert olivia["conversation_id"] is None
    assert olivia["conv_status"] == "open"
    assert olivia["assignee_user_id"] is None
    assert olivia["active_agent_key"] is None
    assert olivia["conv_ai_active"] is True

    # Pinned/archived flags surface as booleans.
    assert _row(items, P_PINNED)["is_pinned"] is True
    assert jose["is_pinned"] is False


# ── 11. ts tie — deduplicated by the F1 LATERAL rewrite ──────────────────────

def test_ts_tie_contact_appears_once(dataset):
    """Two VISIBLE messages of the SAME contact with an identical ts.

    The pre-F1 ``lm`` MAX(ts) self-join matched BOTH rows at the max ts, so the
    outer join yielded the contact TWICE (characterized as xfail in F0). The F1
    LATERAL rewrite (``ORDER BY ts DESC LIMIT 1``) returns exactly one row —
    dedupe is a fix, not a regression (plano 62 F0 item 2 / F1)."""
    counts = _phones(_page()["items"]).count(P_TIE)
    assert counts == 1
