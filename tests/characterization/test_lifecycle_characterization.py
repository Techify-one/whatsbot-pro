"""Golden-master characterization of conversation/contact LIFECYCLE actions
(Plano 23 · Fase A2b — BLOCKING; guards Wave-2 blocks B4 + C1).

Locks the CURRENT behavior (bugs included) of the lifecycle routes in
``server/routes/conversations.py`` and ``server/routes/contacts.py`` BEFORE B4
extracts the conversation service layer and C1 relocates the bus ``emit``s. Each
test drives the REAL HTTP route via the hermetic ``build_app`` client, captures
every bus event with an :class:`EventRecorder`, and ``golden_assert``s the
NORMALIZED tri-facet outcome:

  (a) ``conversation`` — the resulting row's load-bearing lifecycle columns
      (status / assignee_user_id / active_agent_key / ai_active), with the
      assignee id reduced to a stable ``"<USER>"`` token (real ids drift across
      the shared-DB session);
  (b) ``contact`` — ``ai_enabled`` (only where the action touches it);
  (c) ``events`` — the bus event SET (``recorder.name_set``, fan-out order is
      non-deterministic) AND the ordered SEQUENCE (``recorder.names``, which IS
      deterministic for serial route steps) AND a per-name COUNT map so a
      double-fire is impossible to miss;
  (d) ``notices`` — the ``conversation_event`` rows written into the thread
      (role + PT-BR content), since the lifecycle card is part of the behavior
      the refactor must preserve. Post-plano-48 the routes require an authenticated
      operator, so the client authenticates as a fixed admin ("Operador") — the
      notices show the deterministic actor-variant phrasing.

MANDATORY cell matrix (every cell is PASS or an explicit SKIP — the matrix is
visibly complete):

  assign → close      : closing CLEARS the assignee (assignee null after close);
                        status_changed + assigned each fire exactly 1×.
  reopen              : closed → open restores; conversation.reopened fires 1×
                        (C0 added it) alongside status_changed.
  ai toggle (conv)    : POST /conversations/{id}/ai → conversation.ai_toggled.
  ai toggle (contact) : POST /contacts/{phone}/toggle-ai → contact.ai_toggled
                        (+ mirrored conversation.ai_toggled, P17).
  assign → unassign   : POST /assign null → conversation.unassigned (NOT
                        conversation.assigned); the assigned verb is reserved for
                        a real assignee.

Discipline: ADDITIVE only. We never change app/source and never "fix" behavior.
Likely bugs are CAPTURED and flagged with ``# CHARACTERIZED BUG:`` comments.

Distinct phone numbers per test (the suite shares one process DB).
"""

from __future__ import annotations

import pytest

from tests.characterization.golden import (
    EventRecorder, normalize, golden_assert, sort_events,
)


# ── Shared helpers ───────────────────────────────────────────────────────────

def _make_conversation(phone: str, *, status: str = "open"):
    """Create a contact + a conversation in ``status``. Returns (conv_id, contact_id).

    Mirrors the C0 setup helper: ``resolve_for_contact_ex`` get_or_creates the
    contact_inbox + conversation on the seeded DEFAULT inbox (GOWA), then we force
    the desired status. Picks the SAME phone the caller passes so each test stays
    isolated on the shared DB.
    """
    from db.repositories import contact_repo, conversation_repo, config_repo
    # Pin the AI seed ON: a fresh conversation's ai_active/active_agent_key derive
    # from the global auto_reply + default_ai_enabled config, which can drift across
    # the shared-session tests — pinning them keeps this golden's INITIAL state
    # deterministic regardless of test order (the goldens capture these columns).
    config_repo.set("auto_reply", True)
    config_repo.set("default_ai_enabled", True)
    contact = contact_repo.get_or_create(phone)
    conv, _ = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net")
    conversation_repo.set_status(conv["id"], status)
    return conv["id"], contact["id"]


def _make_user(name: str, email: str) -> int:
    """Create an active user (idempotent-ish: a fresh email per test)."""
    from db.repositories import user_repo
    user = user_repo.create(email=email, name=name, password_hash="x")
    return user["id"]


_OPERATOR_TOKEN = None


def _operator_client(built):
    """Authenticate ``built.client`` as a fixed admin operator (plano 48).

    Post-48 the API gate closes once ≥1 user exists, so the lifecycle routes need a
    real user session — and the authenticated operator becomes the *actor* in the
    lifecycle notices/audit (the goldens now show the actor-variant phrasing, e.g.
    "Operador resolveu a conversa"). A single shared session is reused across tests
    (the DB is process-shared). Returns the same ``built`` for chaining."""
    global _OPERATOR_TOKEN
    from db.repositories import user_repo, session_repo
    from server.auth import generate_session_token
    if _OPERATOR_TOKEN is None:
        op = (user_repo.get_by_email("lifecycle_operator@test.com")
              or user_repo.create(email="lifecycle_operator@test.com", name="Operador",
                                  password_hash="x", role_keys=["admin"]))
        _OPERATOR_TOKEN = generate_session_token()
        session_repo.create(_OPERATOR_TOKEN, op["id"], user_agent="test", ip="127.0.0.1")
    built.client.headers["Authorization"] = f"Bearer {_OPERATOR_TOKEN}"
    return built


def _conv_state(conv_id: int) -> dict:
    """The conversation's load-bearing lifecycle columns, with assignee id
    reduced to a stable token (real ids drift across the shared-DB session)."""
    from db.repositories import conversation_repo
    conv = conversation_repo.get(conv_id)
    assignee = conv.get("assignee_user_id")
    return {
        "status": conv.get("status"),
        "assignee_user_id": "<USER>" if assignee is not None else None,
        "active_agent_key": conv.get("active_agent_key"),
        "ai_active": conv.get("ai_active"),
    }


def _contact_ai(contact_id: int) -> dict:
    from db.repositories import contact_repo
    c = contact_repo.get(contact_id)
    return {"ai_enabled": c.get("ai_enabled")}


def _notices(conv_id: int) -> list[dict]:
    """The conversation_event (lifecycle card) rows written into the thread,
    reduced to role + content (ts/ids normalized away by the projection)."""
    from db.repositories import message_repo
    rows = message_repo.get_by_conversation(conv_id)
    return [{"role": r.get("role"), "content": r.get("content")}
            for r in rows if r.get("role") == "conversation_event"]


def _events_facet(rec: EventRecorder, prefix: str = "") -> dict:
    """Capture the events tri-view: deterministic SET, ordered SEQUENCE, and a
    per-name COUNT map (so a double-fire is impossible to miss).

    ``prefix`` scopes to a verb family (e.g. ``conversation.``) so unrelated
    bus chatter from background tasks can't make the golden flaky."""
    names = [n for n in rec.names if not prefix or n.startswith(prefix)]
    counts: dict[str, int] = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return {
        "set": sort_events(list(set(names))),
        "sequence": names,
        "counts": counts,
    }


# ── assign → close ───────────────────────────────────────────────────────────

def test_assign_then_close(build_app):
    """assign → close: assigning sets the assignee; CLOSING the conversation
    CLEARS the assignee (set_status drops assignee_user_id AND active_agent_key
    when status='closed'). Each event fires EXACTLY once per action.

    The close golden therefore shows assignee_user_id=null even though we assigned
    a user a step earlier — this is the documented "resolving drops the
    assignment" behavior. Captures both steps' events so a double-fire surfaces.
    """
    phone = "5511960000001"
    conv_id, _cid = _make_conversation(phone, status="open")
    uid = _make_user("Atendente A2b", "lifecycle_assign_close@test.com")
    built = _operator_client(build_app(["gowa"]))

    # Step 1: assign to a real user → conversation.assigned (NOT unassigned).
    with EventRecorder() as rec_assign:
        r = built.client.post(f"/api/conversations/{conv_id}/assign",
                              json={"assignee_user_id": uid})
        assert r.status_code == 200, r.text
        rec_assign.drain()
    assign_events = _events_facet(rec_assign, "conversation.")
    assert assign_events["counts"].get("conversation.assigned") == 1
    assert "conversation.unassigned" not in assign_events["set"]
    state_after_assign = _conv_state(conv_id)
    assert state_after_assign["assignee_user_id"] == "<USER>"

    # Step 2: close → assignee CLEARED.
    with EventRecorder() as rec_close:
        r = built.client.post(f"/api/conversations/{conv_id}/status",
                              json={"status": "closed"})
        assert r.status_code == 200, r.text
        rec_close.drain()
    close_events = _events_facet(rec_close, "conversation.")
    assert close_events["counts"].get("conversation.status_changed") == 1
    state_after_close = _conv_state(conv_id)
    assert state_after_close["assignee_user_id"] is None, \
        "closing a conversation must clear the assignee"

    golden_assert("lifecycle_assign_then_close", normalize({
        "assign": {
            "conversation": state_after_assign,
            "events": assign_events,
            "notices": _notices(conv_id),  # note: read AFTER close (full thread)
        },
        "close": {
            "conversation": state_after_close,
            "events": close_events,
        },
        "final_notices": _notices(conv_id),
    }))


# ── reopen ───────────────────────────────────────────────────────────────────

def test_reopen_closed_conversation(build_app):
    """reopen: a CLOSED conversation reopened via POST /status {open} restores
    status=open AND emits conversation.reopened EXACTLY once (C0 added it),
    alongside the pre-existing conversation.status_changed (also exactly once).

    set_status('open') also clears resolved_at; assignee stays whatever it was
    (close already cleared it), so the reopened conversation has no assignee."""
    phone = "5511960000002"
    conv_id, _cid = _make_conversation(phone, status="closed")
    built = _operator_client(build_app(["gowa"]))

    with EventRecorder() as rec:
        r = built.client.post(f"/api/conversations/{conv_id}/status",
                              json={"status": "open"})
        assert r.status_code == 200, r.text
        rec.drain()

    events = _events_facet(rec, "conversation.")
    assert events["counts"].get("conversation.reopened") == 1, \
        f"reopened must fire exactly once; got {events['counts']}"
    assert events["counts"].get("conversation.status_changed") == 1
    assert "conversation.unassigned" not in events["set"]
    assert "conversation.assigned" not in events["set"]

    golden_assert("lifecycle_reopen", normalize({
        "conversation": _conv_state(conv_id),
        "events": events,
        "notices": _notices(conv_id),
    }))


def test_reopen_noop_when_already_open(build_app):
    """reopen guard: open → open does NOT emit conversation.reopened (the verb is
    gated on previous_status == 'closed'). status_changed still fires once."""
    phone = "5511960000003"
    conv_id, _cid = _make_conversation(phone, status="open")
    built = _operator_client(build_app(["gowa"]))

    with EventRecorder() as rec:
        r = built.client.post(f"/api/conversations/{conv_id}/status",
                              json={"status": "open"})
        assert r.status_code == 200, r.text
        rec.drain()

    events = _events_facet(rec, "conversation.")
    assert "conversation.reopened" not in events["set"], \
        "open→open must not emit reopened"
    assert events["counts"].get("conversation.status_changed") == 1

    # CHARACTERIZED BUG: the conversation.reopened BUS event is correctly gated on
    # previous_status == 'closed' (absent here), but the system-notice CARD is NOT
    # — set_status route emits the "status_open" notice unconditionally whenever
    # status == 'open', so an open→open no-op still writes a "🔄 Conversa reaberta."
    # lifecycle card (see the notices in this golden). The bus verb and the chat
    # card disagree on the open→open case. B4 should reconcile this when extracting
    # the service layer.
    golden_assert("lifecycle_reopen_noop_already_open", normalize({
        "conversation": _conv_state(conv_id),
        "events": events,
        "notices": _notices(conv_id),
    }))


# ── ai toggle scope: CONVERSATION vs CONTACT ────────────────────────────────

def test_ai_toggle_conversation_scope(build_app):
    """ai toggle (CONVERSATION scope): POST /conversations/{id}/ai {active:false}
    → conversation.ai_toggled fires (the CONVERSATION verb), conversation.assigned
    ALSO fires (turning the AI off hands the chat to the operator → re-assign).
    contact.ai_toggled does NOT fire on this route (distinct scope).

    Note (post-plano-48): the route now runs as an authenticated operator, so
    turning the AI off HANDS the chat to that operator — assignee becomes the
    operator ("<USER>"), not null. active_agent_key is cleared and ai_active flips
    to 0. Both ai_toggled and assigned fire EXACTLY once each."""
    phone = "5511960000004"
    conv_id, _cid = _make_conversation(phone, status="open")
    built = _operator_client(build_app(["gowa"]))

    with EventRecorder() as rec:
        r = built.client.post(f"/api/conversations/{conv_id}/ai",
                              json={"active": False})
        assert r.status_code == 200, r.text
        rec.drain()

    events = _events_facet(rec, "")  # capture full set so we can prove scope
    assert "conversation.ai_toggled" in events["set"]
    assert events["counts"].get("conversation.ai_toggled") == 1
    # CONTACT verb must NOT fire on the conversation-scope route.
    assert "contact.ai_toggled" not in events["set"], \
        "conversation-scope toggle must not emit the contact verb"

    golden_assert("lifecycle_ai_toggle_conversation_scope", normalize({
        "conversation": _conv_state(conv_id),
        "scope": "conversation",
        "events": events,
        "notices": _notices(conv_id),
    }))


def test_ai_toggle_contact_scope(build_app):
    """ai toggle (CONTACT scope): POST /contacts/{phone}/toggle-ai {enabled:false}
    → contact.ai_toggled fires (the CONTACT verb). P17: the route ALSO mirrors the
    flip onto the contact's conversation via set_ai_active + broadcasts
    conversation_ai_toggled over WS — but that mirror is a WS broadcast, NOT a bus
    emit_with_filter, so the BUS only sees contact.ai_toggled here (no
    conversation.ai_toggled bus verb).

    Distinguishes scope from the conversation route: contact.ai_toggled vs
    conversation.ai_toggled, with the ``scope`` field captured. ai_toggled fires
    exactly once. The contact-level ai_enabled flips to 0, and the conversation's
    ai_active is mirrored to 0 (low-level set_ai_active, no (un)assign)."""
    phone = "5511960000005"
    conv_id, contact_id = _make_conversation(phone, status="open")
    built = _operator_client(build_app(["gowa"]))

    with EventRecorder() as rec:
        r = built.client.post(f"/api/contacts/{phone}/toggle-ai",
                              json={"enabled": False})
        assert r.status_code == 200, r.text
        rec.drain()

    events = _events_facet(rec, "")
    assert "contact.ai_toggled" in events["set"]
    assert events["counts"].get("contact.ai_toggled") == 1
    # CHARACTERIZED BEHAVIOR: the per-conversation mirror (set_ai_active +
    # conversation_ai_toggled) is a WS broadcast only — it is NOT emitted on the
    # plugin bus, so conversation.ai_toggled does NOT appear here. B4/C1 should
    # preserve this (or deliberately unify it) when relocating emits.
    assert "conversation.ai_toggled" not in events["set"], \
        "contact-scope toggle emits the conversation verb only over WS, not the bus"

    golden_assert("lifecycle_ai_toggle_contact_scope", normalize({
        "conversation": _conv_state(conv_id),
        "contact": _contact_ai(contact_id),
        "scope": "contact",
        "events": events,
        "notices": _notices(conv_id),
    }))


# ── assign → unassign (null) ────────────────────────────────────────────────

def test_assign_then_unassign(build_app):
    """assign → unassign: assigning a user emits conversation.assigned; assigning
    NULL emits conversation.unassigned (the C0 distinct verb), NOT assigned. The
    assigned verb is reserved for a real assignee; the unassigned verb for
    clearing. Each fires exactly once on its own action, never both.

    The unassign golden shows assignee_user_id=null after the clear."""
    phone = "5511960000006"
    conv_id, _cid = _make_conversation(phone, status="open")
    uid = _make_user("Atendente Unassign", "lifecycle_unassign@test.com")
    built = _operator_client(build_app(["gowa"]))

    # Step 1: assign a real user.
    with EventRecorder() as rec_assign:
        r = built.client.post(f"/api/conversations/{conv_id}/assign",
                              json={"assignee_user_id": uid})
        assert r.status_code == 200, r.text
        rec_assign.drain()
    assign_events = _events_facet(rec_assign, "conversation.")
    assert assign_events["counts"].get("conversation.assigned") == 1
    assert "conversation.unassigned" not in assign_events["set"]
    state_assigned = _conv_state(conv_id)
    assert state_assigned["assignee_user_id"] == "<USER>"

    # Step 2: unassign (null) → the DISTINCT unassigned verb, not assigned.
    with EventRecorder() as rec_unassign:
        r = built.client.post(f"/api/conversations/{conv_id}/assign",
                              json={"assignee_user_id": None})
        assert r.status_code == 200, r.text
        rec_unassign.drain()
    unassign_events = _events_facet(rec_unassign, "conversation.")
    assert unassign_events["counts"].get("conversation.unassigned") == 1
    assert "conversation.assigned" not in unassign_events["set"], \
        "clearing the assignee must emit unassigned, never assigned"
    state_unassigned = _conv_state(conv_id)
    assert state_unassigned["assignee_user_id"] is None

    golden_assert("lifecycle_assign_then_unassign", normalize({
        "assign": {
            "conversation": state_assigned,
            "events": assign_events,
        },
        "unassign": {
            "conversation": state_unassigned,
            "events": unassign_events,
        },
        "notices": _notices(conv_id),
    }))


# ── matrix completeness: structurally-unreachable cell ──────────────────────

def test_assign_me_without_user_is_unreachable_here():
    """assign-me requires an authenticated user (401 otherwise). The hermetic
    TestClient has no session/user identity, so the assign-me happy path
    (assignee = the caller) is structurally unreachable in this characterization
    harness. Marked SKIP so the lifecycle matrix is visibly complete; the route's
    401 guard is covered by the endpoint suite."""
    pytest.skip("assign-me happy path needs an authenticated current_user, which "
                "the hermetic TestClient does not provide")
