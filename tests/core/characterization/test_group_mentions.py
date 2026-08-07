"""Golden-master characterization of ``agent/group_mentions.py`` mention I/O
(Plano 23 · Fase A2b — BLOCKING).

Locks the CURRENT behavior (bugs included) of the @mention round-trip BEFORE
Wave 2 touches the group-mentions service:

* ``resolve_incoming(group_jid, text)`` — wire ``@<digits>`` → human ``@<Name>``
  (panel/LLM see names, not numbers), using the cached participant roster
  (indexed by BOTH phone-digits and lid-digits).
* ``resolve_outgoing(group_jid, text)`` — operator/AI ``@Name`` → real inline
  ``@<phone>`` + the GOWA ``mentions`` list; raw ``@<digits>`` normalized to the
  canonical phone; and the mention-all keywords (``@todos`` / ``@geral`` /
  ``@all`` / ``@everyone`` / ``@todes``) → text token ``@all`` + EVERY member's
  phone in ``mentions`` (the bot excluded).

The service is a pure in-memory module keyed off module-level globals
(``_client``, ``_bot_phone``, ``_bot_name``, ``_members_cache``,
``_pushname_cache``, ``_store_cache``, ``_pushname_attempted``). We seed the
members cache directly (the behavior frozen by this characterization suite)
so ``get_members`` returns a fixed roster with NO HTTP — exactly the cut Wave 2
must preserve — and snapshot the NORMALIZED ``(text, mentions)`` via
``golden_assert``.

Determinism / isolation: every test runs inside ``_isolated_mentions`` which
snapshots and restores ALL module globals, so nothing leaks onto the shared
process (group_mentions is a singleton, and the parallel A2 webhook suite calls
``group_mentions.init`` at app build). Goldens are byte-stable: ``mentions`` is
normalized to a SORTED set (insertion order of the mention list is an
implementation detail — see the comment on ``_outgoing_value``), while the
visible ``text`` is asserted verbatim.

Discipline: ADDITIVE only. We do NOT change app/source and do NOT "fix"
behavior. Likely bugs are CAPTURED and flagged with ``# CHARACTERIZED BUG:``.

File + every golden are prefixed ``gm_`` to avoid collisions with parallel
sibling subsystems.
"""

from __future__ import annotations

import contextlib
import time

import pytest

from agent import group_mentions
from tests.golden import golden_assert, normalize


GROUP = "120363999990000@g.us"

# Fixed roster: two named phone-only members, one named lid-addressed member,
# one nameless phone-only member. Mirrors test_group_mentions.py's shape but
# adds a lid participant so the phone↔lid indexing is exercised.
ROSTER = [
    {"phone": "5511111111111", "lid": "",                "name": "Ana",   "is_admin": True},
    {"phone": "5522222222222", "lid": "",                "name": "Bruno", "is_admin": False},
    {"phone": "5533333333333", "lid": "199998887776665", "name": "Carla", "is_admin": False},
    {"phone": "5544444444444", "lid": "",                "name": "",      "is_admin": False},
]
ALL_PHONES = sorted(m["phone"] for m in ROSTER)


@contextlib.contextmanager
def _isolated_mentions():
    """Snapshot & restore EVERY group_mentions module global.

    group_mentions is a process singleton; the parallel A2 webhook suite wires a
    real client into it via ``group_mentions.init`` on app build. Saving and
    restoring guarantees this characterization neither leaks fake state outward
    nor depends on ordering against that suite.
    """
    saved = {
        "_client": group_mentions._client,
        "_bot_phone": group_mentions._bot_phone,
        "_bot_name": group_mentions._bot_name,
        "_members_cache": dict(group_mentions._members_cache),
        "_store_cache": group_mentions._store_cache,
        "_pushname_cache": dict(group_mentions._pushname_cache),
        "_pushname_attempted": set(group_mentions._pushname_attempted),
    }
    try:
        yield
    finally:
        group_mentions._client = saved["_client"]
        group_mentions._bot_phone = saved["_bot_phone"]
        group_mentions._bot_name = saved["_bot_name"]
        group_mentions._members_cache.clear()
        group_mentions._members_cache.update(saved["_members_cache"])
        group_mentions._store_cache = saved["_store_cache"]
        group_mentions._pushname_cache.clear()
        group_mentions._pushname_cache.update(saved["_pushname_cache"])
        group_mentions._pushname_attempted.clear()
        group_mentions._pushname_attempted.update(saved["_pushname_attempted"])


def _seed(members, *, bot_phone="", bot_name="", client=None):
    """Prime get_members() to return ``members`` without any HTTP.

    A non-None ``_client`` is required for get_members() to proceed (it short
    -circuits to ``[]`` when the client is unset). We default to a bare object()
    sentinel — the cached roster means no client method is ever called on the
    outgoing/incoming hot paths.
    """
    group_mentions._client = client if client is not None else object()
    group_mentions._bot_phone = group_mentions._digits(bot_phone or "")
    group_mentions._bot_name = (bot_name or "").strip()
    group_mentions._members_cache[GROUP] = (time.time(), [dict(m) for m in members])


def _outgoing_value(text, mentions):
    """Golden-stable projection of a resolve_outgoing() result.

    ``text`` is asserted VERBATIM (the visible message the operator/recipient
    sees is load-bearing). ``mentions`` is captured as a SORTED list: the GOWA
    /send/message ``mentions`` array is a notify-set, and the service's insertion
    order (mention-all phones first, then named matches, then raw-numeric) is an
    implementation detail Wave 2 may reasonably reshuffle without changing who
    gets tagged. ``mentions_count`` preserves de-dup evidence (a phone appearing
    once even when matched by both @all and @Name).
    """
    return normalize({
        "text": text,
        "mentions_sorted": sorted(mentions),
        "mentions_count": len(mentions),
        "has_literal_everyone": "@everyone" in mentions,
    })


# ── resolve_incoming: @<digits> → @<Name> ────────────────────────────────────

def test_incoming_number_to_name():
    """resolve_incoming replaces a raw ``@<phone-digits>`` with ``@<Name>``."""
    with _isolated_mentions():
        _seed(ROSTER)
        out = group_mentions.resolve_incoming(
            GROUP, "bom dia @5511111111111 e @5522222222222")
        golden_assert("gm_incoming_number_to_name", normalize({"text": out}))


def test_incoming_lid_digits_to_name():
    """resolve_incoming resolves a mention by the participant's LID digits too
    (the roster is indexed by BOTH phone-digits and lid-digits)."""
    with _isolated_mentions():
        _seed(ROSTER)
        # Carla is addressed by her lid digits here, not her phone.
        out = group_mentions.resolve_incoming(
            GROUP, "obrigado @199998887776665")
        golden_assert("gm_incoming_lid_to_name", normalize({"text": out}))


def test_incoming_unknown_and_nameless_untouched():
    """A raw mention that maps to no member (or to a NAMELESS member) is left
    verbatim — only members with a known name are rewritten.

    Nameless members are excluded from the lookup (build_lookup skips
    ``not m['name']``), so ``@5544444444444`` (the nameless member) and a digit
    string for nobody both pass through unchanged."""
    with _isolated_mentions():
        _seed(ROSTER)
        out = group_mentions.resolve_incoming(
            GROUP, "oi @5544444444444 e @5500000000000")
        golden_assert("gm_incoming_unknown_untouched", normalize({"text": out}))


def test_incoming_no_at_is_noop():
    """No ``@`` in the text → returned verbatim (fast path, no lookup build)."""
    with _isolated_mentions():
        _seed(ROSTER)
        out = group_mentions.resolve_incoming(GROUP, "mensagem sem mencao")
        golden_assert("gm_incoming_noop", normalize({"text": out}))


def test_incoming_short_number_not_matched():
    """``_NUM_RE`` requires 5+ digits; a short ``@123`` is never treated as a
    numeric mention (so it can't accidentally rewrite)."""
    with _isolated_mentions():
        _seed(ROSTER)
        out = group_mentions.resolve_incoming(GROUP, "ramal @123 favor ligar")
        golden_assert("gm_incoming_short_number", normalize({"text": out}))


# ── resolve_outgoing: @Name → inline @<phone> + mentions ─────────────────────

def test_outgoing_single_name():
    """resolve_outgoing rewrites ``@Bruno`` → inline ``@<phone>`` and lists that
    phone in ``mentions``."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(GROUP, "fala @Bruno")
        golden_assert("gm_outgoing_single_name", _outgoing_value(text, mentions))


def test_outgoing_multiple_names():
    """Two distinct ``@Name`` tokens → both rewritten inline, both in mentions
    (longest-name-first matching avoids a short name shadowing a longer one)."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(
            GROUP, "@Ana e @Carla podem revisar?")
        golden_assert("gm_outgoing_multiple_names", _outgoing_value(text, mentions))


def test_outgoing_name_case_insensitive():
    """Name matching is case-insensitive: ``@ana`` resolves to Ana's phone."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(GROUP, "valeu @ana")
        golden_assert("gm_outgoing_name_case_insensitive",
                      _outgoing_value(text, mentions))


def test_outgoing_lid_member_named():
    """A lid-addressed member with a known name still resolves by ``@Name`` to
    her canonical PHONE (mentions carry the phone, never the lid)."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(GROUP, "oi @Carla")
        golden_assert("gm_outgoing_lid_member_named",
                      _outgoing_value(text, mentions))


def test_outgoing_raw_number_normalized_from_phone():
    """A raw ``@<phone-digits>`` is normalized to the canonical phone and added
    to mentions (here the digits already ARE the canonical phone)."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(
            GROUP, "chama @5522222222222")
        golden_assert("gm_outgoing_raw_number_phone",
                      _outgoing_value(text, mentions))


def test_outgoing_raw_number_normalized_from_lid():
    """A raw ``@<lid-digits>`` is normalized to the member's canonical PHONE in
    BOTH the inline text and the mentions list (lid → phone collapse)."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(
            GROUP, "chama @199998887776665")
        golden_assert("gm_outgoing_raw_number_lid",
                      _outgoing_value(text, mentions))


def test_outgoing_unknown_name_untouched():
    """An ``@Name`` for nobody in the roster is left verbatim, no mention added."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(GROUP, "oi @Fulano")
        golden_assert("gm_outgoing_unknown_name", _outgoing_value(text, mentions))


def test_outgoing_no_at_is_noop():
    """No ``@`` → ``(text, [])`` verbatim."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(GROUP, "sem mencao aqui")
        golden_assert("gm_outgoing_noop", _outgoing_value(text, mentions))


# ── resolve_outgoing: @todos / @geral / @all / @everyone → @everyone semantics ─

def test_outgoing_todos_expands_to_everyone():
    """LOCKED: ``@todos`` → visible token ``@all`` + EVERY member's phone in
    mentions (so WhatsApp actually notifies everyone) — NOT a literal
    ``@everyone`` (which WhatsApp would notify nobody). The bot is not seeded
    here, so all four phones appear."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(GROUP, "@todos bom dia")
        assert text == "@all bom dia"
        assert set(mentions) == set(ALL_PHONES)
        assert "@everyone" not in mentions
        golden_assert("gm_outgoing_todos_everyone",
                      _outgoing_value(text, mentions))


@pytest.mark.parametrize("kw", ["@all", "@geral", "@everyone", "@todes"])
def test_outgoing_all_aliases_expand(kw):
    """Each mention-all alias expands the same way: token → ``@all``, mentions =
    every member's phone, no literal ``@everyone``."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(GROUP, f"oi {kw} tudo bem")
        assert "@all" in text and "@everyone" not in mentions
        assert set(mentions) == set(ALL_PHONES)
        golden_assert(f"gm_outgoing_alias_{kw.lstrip('@')}",
                      _outgoing_value(text, mentions))


def test_outgoing_todos_excludes_bot():
    """The bot's own phone is excluded from a mention-all expansion (no
    self-ping). Ana is the bot here → only the other three phones."""
    with _isolated_mentions():
        _seed(ROSTER, bot_phone="5511111111111", bot_name="WhatsBot")
        text, mentions = group_mentions.resolve_outgoing(GROUP, "@todos reuniao agora")
        assert "5511111111111" not in mentions
        golden_assert("gm_outgoing_todos_excludes_bot",
                      _outgoing_value(text, mentions))


def test_outgoing_all_plus_name_dedups():
    """``@all`` + a redundant ``@Ana`` → Ana appears exactly ONCE in mentions
    (the named pass skips a phone already added by the all-expansion), and her
    name token is still rewritten inline to her phone."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(GROUP, "@all e @Ana confiram")
        assert mentions.count("5511111111111") == 1
        assert set(mentions) == set(ALL_PHONES)
        assert "@all" in text and "@5511111111111" in text
        golden_assert("gm_outgoing_all_plus_name", _outgoing_value(text, mentions))


def test_outgoing_all_no_members_literal_fallback():
    """LOCKED fallback: when the roster is EMPTY (GOWA fetch failed / nobody
    resolvable), ``@todos`` can't be expanded to phones, so the service emits the
    LITERAL ``@everyone`` in mentions rather than regressing to no mention at
    all. Text still becomes ``@all``."""
    with _isolated_mentions():
        _seed([], bot_phone="")
        text, mentions = group_mentions.resolve_outgoing(GROUP, "@todos urgente")
        assert mentions == ["@everyone"]
        assert text == "@all urgente"
        golden_assert("gm_outgoing_all_empty_roster",
                      _outgoing_value(text, mentions))


def test_outgoing_all_only_bot_member_literal_fallback():
    """LOCKED edge: roster has ONLY the bot. Excluding the bot leaves zero
    expandable phones → the same literal ``@everyone`` fallback fires.

    # CHARACTERIZED BEHAVIOR: a single-member (bot-only) group sends the literal
    # ``@everyone`` even though the roster WAS fetched successfully — the
    # ``everyone`` list is empty after excluding the bot, indistinguishable from a
    # failed fetch. Captured, not judged."""
    with _isolated_mentions():
        _seed([{"phone": "5511111111111", "lid": "", "name": "Bot", "is_admin": True}],
              bot_phone="5511111111111", bot_name="WhatsBot")
        text, mentions = group_mentions.resolve_outgoing(GROUP, "@todos oi")
        assert mentions == ["@everyone"]
        golden_assert("gm_outgoing_all_bot_only",
                      _outgoing_value(text, mentions))


def test_outgoing_all_keyword_needs_word_boundary():
    """``_ALL_RE`` requires a leading non-word/@ boundary and a trailing ``\\b``:
    ``@todos`` embedded mid-token (e.g. ``email@todos.com``) is NOT treated as a
    mention-all. Characterizes the regex boundary so Wave 2 can't silently widen
    it."""
    with _isolated_mentions():
        _seed(ROSTER)
        text, mentions = group_mentions.resolve_outgoing(
            GROUP, "mande pra email@todos.com depois")
        # email@todos.com: the '@' is preceded by a word char → _ALL_RE's
        # (?<![\w@]) lookbehind fails, so NO expansion. Nothing matched a name
        # either, so mentions stays empty and text is verbatim.
        assert mentions == []
        assert text == "mande pra email@todos.com depois"
        golden_assert("gm_outgoing_all_word_boundary",
                      _outgoing_value(text, mentions))


# ── name-resolution fallback chain (deterministically stubbable layers) ──────
#
# _resolve_name order: saved contact (DB) > pushName cache > device store. The
# DB-saved layer requires the engine and is covered by its own engine-backed
# test below; the pushName + device-store layers are pure-memory / fake-client
# and characterized here. We feed get_members a roster with EMPTY names so the
# resolution actually runs, then assert which layer named whom.


class _StoreClient:
    """Minimal fake exposing only ``get_wa_contacts`` (the device address book),
    the single client method ``_store_map`` calls. Deterministic, no network."""

    def __init__(self, contacts):
        self._contacts = contacts

    def get_wa_contacts(self):
        return self._contacts


def test_resolution_pushname_layer():
    """Fallback layer 2: a pushName recorded via ``record_pushname`` names a
    participant that has no saved contact and isn't in the device store."""
    with _isolated_mentions():
        group_mentions._client = _StoreClient([])           # empty device store
        group_mentions._pushname_cache.clear()
        group_mentions._pushname_attempted.clear()
        group_mentions._store_cache = None
        # Record a pushName for a phone (digits-keyed).
        group_mentions.record_pushname(["5577777777777@s.whatsapp.net"], "Pedro")
        name = group_mentions._resolve_name("5577777777777", "")
        golden_assert("gm_resolution_pushname", normalize({"resolved": name}))


def test_resolution_pushname_by_lid():
    """Fallback layer 2 via the LID key: a pushName keyed by lid digits resolves
    when only the lid is known for the participant."""
    with _isolated_mentions():
        group_mentions._client = _StoreClient([])
        group_mentions._pushname_cache.clear()
        group_mentions._pushname_attempted.clear()
        group_mentions._store_cache = None
        group_mentions.record_pushname(["288887776665554@lid"], "Lucia")
        # phone unknown (""), lid known → resolves via the lid pushName key.
        name = group_mentions._resolve_name("", "288887776665554")
        golden_assert("gm_resolution_pushname_lid", normalize({"resolved": name}))


def test_resolution_device_store_layer():
    """Fallback layer 3: the device address book (``get_wa_contacts``) names a
    participant when there's no saved contact and no captured pushName."""
    with _isolated_mentions():
        group_mentions._client = _StoreClient([
            {"jid": "5588888888888@s.whatsapp.net", "name": "Marina"},
            {"jid": "5599999999999@s.whatsapp.net", "name": ""},   # blank → skipped
        ])
        group_mentions._pushname_cache.clear()
        group_mentions._pushname_attempted.clear()
        group_mentions._store_cache = None
        named = group_mentions._resolve_name("5588888888888", "")
        blank = group_mentions._resolve_name("5599999999999", "")
        golden_assert("gm_resolution_device_store",
                      normalize({"named": named, "blank_name": blank}))


def test_resolution_pushname_beats_store():
    """Precedence: a captured pushName wins over the device store for the same
    participant (pushName is checked BEFORE the store in ``_resolve_name``)."""
    with _isolated_mentions():
        group_mentions._client = _StoreClient([
            {"jid": "5566666666666@s.whatsapp.net", "name": "NomeDaAgenda"},
        ])
        group_mentions._pushname_cache.clear()
        group_mentions._pushname_attempted.clear()
        group_mentions._store_cache = None
        group_mentions.record_pushname(["5566666666666@s.whatsapp.net"], "NomePush")
        name = group_mentions._resolve_name("5566666666666", "")
        golden_assert("gm_resolution_pushname_beats_store",
                      normalize({"resolved": name}))


def test_resolution_none_known_returns_empty():
    """All layers miss → ``_resolve_name`` returns ``""`` (caller falls back to
    the bare number / a /user/info lookup elsewhere)."""
    with _isolated_mentions():
        group_mentions._client = _StoreClient([])
        group_mentions._pushname_cache.clear()
        group_mentions._pushname_attempted.clear()
        group_mentions._store_cache = None
        name = group_mentions._resolve_name("5500000001111", "")
        golden_assert("gm_resolution_none", normalize({"resolved": name}))


def test_resolution_saved_contact_layer(_engine_ready):
    """Fallback layer 1 (top precedence): a SAVED contact name beats pushName +
    device store. Requires the engine (``contact_repo.get_by_phone`` hits the
    DB), so this cell depends on ``_engine_ready`` instead of being skipped.

    A distinct phone keeps it off the shared seed data."""
    from db.repositories import contact_repo

    phone = "5512345006789"
    c = contact_repo.get_or_create(phone)
    contact_repo.update(c["id"], name="~SalvoNoBanco")   # leading ~ is stripped
    with _isolated_mentions():
        group_mentions._client = _StoreClient([
            {"jid": f"{phone}@s.whatsapp.net", "name": "NomeAgenda"},
        ])
        group_mentions._pushname_cache.clear()
        group_mentions._pushname_attempted.clear()
        group_mentions._store_cache = None
        group_mentions.record_pushname([f"{phone}@s.whatsapp.net"], "NomePush")
        name = group_mentions._resolve_name(phone, "")
        golden_assert("gm_resolution_saved_contact", normalize({"resolved": name}))


# ── deterministically-unreachable cell (kept visible as a SKIP) ──────────────

def test_resolution_user_info_fetch_unreachable():
    """The LAST resort of the name chain is a blocking ``/user/info`` HTTP call
    (``_fetch_pushname`` → ``client._get_user_info``) reached only by
    ``_resolve_missing_names`` / ``_display_name`` — NOT by resolve_incoming /
    resolve_outgoing (the subsystem under characterization), and only for
    business accounts. Driving it deterministically would require stubbing the
    GOWA HTTP client's private ``_get_user_info`` AND asserting an HTTP-shaped
    side effect outside this subsystem's mention I/O surface. Out of scope for
    the mention round-trip golden; explicitly skipped so the matrix is visibly
    complete."""
    pytest.skip("_fetch_pushname /user/info path is outside resolve_incoming/"
                "resolve_outgoing; covered by autocomplete tests elsewhere")
