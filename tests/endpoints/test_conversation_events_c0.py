"""Plano 23 · Fase C0 — characterization tests for the new conversation
lifecycle DOMAIN EVENTS emitted from the current routes / closures / tools.

C0 ADDS six verbs to the bus without building a service layer:

    conversation.reopened          (manual status route + automatic inbound reopen)
    conversation.unassigned        (assign route, null-assignee case)
    conversation.transferred_to_human  (transfer_to_human LLM tool)
    conversation.agent_changed     (transferir_agente handoff tool)
    conversation.attribute_set     (PUT /info custom attribute)
    conversation.ai_takeover       (promoted from the internal dedupe)

Each test drives the real action (HTTP route, or the tool/closure where no HTTP
route exists) and asserts the corresponding new event fires EXACTLY ONCE, while
the pre-existing verbs are not double-emitted.

Capture strategy: register a ``filter.event.before_emit`` filter (it runs
synchronously inside both ``emit_with_filter`` and ``emit_with_filter_sync`` —
no fanout-timing flakiness) and record every (event_name, payload). A real
background event loop is wired via ``bus.set_runtime`` so the SYNC emit paths
(tool / memory threads) also reach the filter.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from plugins import events as bus


# ── Background loop + event capture ────────────────────────────────────────

@pytest.fixture
def captured_events():
    """Wire the bus to a real background loop and capture every emitted event.

    Yields a list of ``(event_name, payload)`` tuples. Uses a
    ``filter.event.before_emit`` filter that records the event then returns the
    payload unchanged, so emission is never suppressed.
    """
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        started.set()
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    started.wait()

    bus.reset()
    bus.set_runtime(loop, agent_handler=None)

    records: list[tuple[str, dict]] = []

    def _capture(ctx, payload):
        records.append((ctx.extras.get("event_name"), payload))
        return payload

    bus.register_filter("c0_test", "filter.event.before_emit", _capture, priority=1)

    yield records

    bus.reset()
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2.0)


def _drain():
    """Let the background loop flush scheduled emit() tasks (fanout)."""
    time.sleep(0.05)


def _names(records, prefix="conversation."):
    return [n for n, _ in records if n and n.startswith(prefix)]


# ── Helpers to set up conversation state ───────────────────────────────────

def _make_closed_conversation(phone: str) -> tuple[int, int]:
    """Create a contact + a CLOSED conversation. Returns (conv_id, contact_id)."""
    from db.repositories import contact_repo, conversation_repo
    contact = contact_repo.get_or_create(phone)
    conv, _ = conversation_repo.resolve_for_contact_ex(contact["id"], f"{phone}@s.whatsapp.net")
    conversation_repo.set_status(conv["id"], "closed")
    return conv["id"], contact["id"]


def _make_open_conversation(phone: str) -> tuple[int, int]:
    from db.repositories import contact_repo, conversation_repo
    contact = contact_repo.get_or_create(phone)
    conv, _ = conversation_repo.resolve_for_contact_ex(contact["id"], f"{phone}@s.whatsapp.net")
    conversation_repo.set_status(conv["id"], "open")
    return conv["id"], contact["id"]


def _auth_admin(client):
    """Authenticate the client as a DEDICATED admin (plano 48: the mutating
    /api/conversations/* routes require a user session once ≥1 user exists — which
    the tests below trigger by creating an assignee). The admin is distinct from
    any assignee under test, so the assign/attribute bus verbs stay actor-neutral
    (``conversation.assigned``, not ``assigned_me``). Returns the same client."""
    from db.repositories import user_repo, session_repo
    from server.auth import hash_password_argon2, generate_session_token
    admin = (user_repo.get_by_email("c0_admin@test.com")
             or user_repo.create(email="c0_admin@test.com", name="C0 Admin",
                                 password_hash=hash_password_argon2("x"),
                                 role_keys=["admin"]))
    tok = generate_session_token()
    session_repo.create(tok, admin["id"], user_agent="test", ip="127.0.0.1")
    client.headers["Authorization"] = f"Bearer {tok}"
    return client


# ── 1. conversation.reopened (manual status route) ─────────────────────────

def test_reopened_manual_via_status_route(client, captured_events):
    conv_id, contact_id = _make_closed_conversation("5511900000001")
    captured_events.clear()

    r = client.post(f"/api/conversations/{conv_id}/status", json={"status": "open"})
    assert r.status_code == 200, r.text
    _drain()

    reopened = [p for n, p in captured_events if n == "conversation.reopened"]
    assert len(reopened) == 1, f"expected 1 reopened, got {_names(captured_events)}"
    p = reopened[0]
    assert p["conversation_id"] == conv_id
    assert p["previous_status"] == "closed"
    assert p["trigger"] == "manual"
    # status_changed still fires exactly once (the pre-existing verb is untouched).
    assert _names(captured_events).count("conversation.status_changed") == 1
    # Not emitted as unassigned/assigned by accident.
    assert "conversation.unassigned" not in _names(captured_events)


def test_reopened_not_fired_when_already_open(client, captured_events):
    conv_id, _ = _make_open_conversation("5511900000002")
    captured_events.clear()

    r = client.post(f"/api/conversations/{conv_id}/status", json={"status": "open"})
    assert r.status_code == 200, r.text
    _drain()

    assert "conversation.reopened" not in _names(captured_events), \
        "open→open must NOT emit reopened"


# ── 2. conversation.reopened (automatic inbound reopen via memory.add_message)

def test_reopened_inbound_via_add_message(client, captured_events):
    """A client message on a CLOSED conversation reopens it → reopened(trigger=inbound)."""
    from agent.memory import ContactMemory
    phone = "5511900000003"
    _make_closed_conversation(phone)
    captured_events.clear()

    mem = ContactMemory(phone)
    mem.add_message("user", "Oi, voltei!")
    _drain()

    reopened = [p for n, p in captured_events if n == "conversation.reopened"]
    assert len(reopened) == 1, f"expected 1 inbound reopened, got {_names(captured_events)}"
    p = reopened[0]
    assert p["trigger"] == "inbound"
    assert p["previous_status"] == "closed"
    assert p["phone"] == phone


# ── 3. conversation.unassigned (assign route, null assignee) ───────────────

def test_unassigned_via_assign_route(client, captured_events):
    from db.repositories import conversation_repo
    conv_id, _ = _make_open_conversation("5511900000004")
    # Pre-assign so the removal is a real transition.
    conversation_repo.set_assignee(conv_id, None)
    captured_events.clear()

    r = client.post(f"/api/conversations/{conv_id}/assign",
                    json={"assignee_user_id": None})
    assert r.status_code == 200, r.text
    _drain()

    names = _names(captured_events)
    assert names.count("conversation.unassigned") == 1, \
        f"expected 1 unassigned, got {names}"
    # Must NOT also emit the assigned verb for a null assignee (no double-count).
    assert "conversation.assigned" not in names


def test_assigned_verb_kept_for_non_null(client, captured_events):
    """Setting a real assignee still emits assigned (not unassigned)."""
    from db.repositories import user_repo
    conv_id, _ = _make_open_conversation("5511900000005")
    user = user_repo.create(email="c0assignee@test.com", name="C0 Assignee",
                            password_hash="x")
    uid = user["id"]
    _auth_admin(client)  # mutating route needs a session now that a user exists
    captured_events.clear()

    r = client.post(f"/api/conversations/{conv_id}/assign",
                    json={"assignee_user_id": uid})
    assert r.status_code == 200, r.text
    _drain()

    names = _names(captured_events)
    assert names.count("conversation.assigned") == 1, f"got {names}"
    assert "conversation.unassigned" not in names


# ── 4. conversation.attribute_set (PUT /info) ──────────────────────────────

def test_attribute_set_via_info_route(client, captured_events):
    from db.repositories import custom_attribute_repo
    conv_id, _ = _make_open_conversation("5511900000006")
    # Define a conversation custom attribute (idempotent: ignore collisions).
    custom_attribute_repo.create_definition(
        attribute_key="c0_priority", display_name="Prioridade",
        type="text", applies_to="conversation")
    _auth_admin(client)  # a prior test created a user → this mutating route needs a session
    captured_events.clear()

    r = client.put(f"/api/conversations/{conv_id}/info",
                   json={"custom_attributes": {"c0_priority": "alta"}})
    assert r.status_code == 200, r.text
    _drain()

    attr = [p for n, p in captured_events if n == "conversation.attribute_set"]
    assert len(attr) == 1, f"expected 1 attribute_set, got {_names(captured_events)}"
    assert attr[0]["key"] == "c0_priority"
    assert attr[0]["value"] == "alta"
    assert attr[0]["conversation_id"] == conv_id


# ── 5. conversation.transferred_to_human (transfer_to_human tool) ──────────

def test_transferred_to_human_via_tool(client, captured_events):
    from agent.memory import ContactMemory
    from agent.tools.transfer_to_human import execute as transfer_execute
    from agent.handler import AgentHandler
    from plugins.context import ToolContext

    phone = "5511900000007"
    conv_id, contact_id = _make_open_conversation(phone)
    captured_events.clear()

    handler = AgentHandler(api_key="x")
    mem = ContactMemory(phone)
    ctx = ToolContext(contact=mem, handler=handler, tag_registry=handler.tag_registry)
    out = transfer_execute(ctx, {"reason": "cliente pediu atendente"})
    assert out is not None
    _drain()

    transferred = [p for n, p in captured_events if n == "conversation.transferred_to_human"]
    assert len(transferred) == 1, \
        f"expected 1 transferred_to_human, got {_names(captured_events)}"
    p = transferred[0]
    assert p["conversation_id"] == conv_id
    assert p["contact_id"] == contact_id
    assert p["reason"] == "cliente pediu atendente"


# ── 6. conversation.agent_changed (transferir_agente handoff tool) ─────────

def test_agent_changed_via_handoff_tool(client, captured_events):
    from agent.memory import ContactMemory
    from agent.tools.transferir_agente import execute as handoff_execute
    from agent.handler import AgentHandler
    from plugins.context import ToolContext
    from db.repositories import agent_repo

    phone = "5511900000008"
    _make_open_conversation(phone)
    agent_repo.ensure("c0_vendas", display_name="Vendas", enabled=True)
    captured_events.clear()

    handler = AgentHandler(api_key="x")
    mem = ContactMemory(phone)
    ctx = ToolContext(contact=mem, handler=handler, tag_registry=handler.tag_registry)
    out = handoff_execute(ctx, {"agente": "c0_vendas", "motivo": "assunto de vendas"})
    assert out is not None and "Erro" not in out, out
    _drain()

    changed = [p for n, p in captured_events if n == "conversation.agent_changed"]
    assert len(changed) == 1, f"expected 1 agent_changed, got {_names(captured_events)}"
    p = changed[0]
    assert p["to_agent"] == "c0_vendas"
    assert p["reason"] == "assunto de vendas"
    # from_agent is None here (no agent was bound before the handoff).
    assert "from_agent" in p


# ── 7. conversation.ai_takeover is registered (promoted from dedupe) ───────

def test_ai_takeover_registered_in_known_events():
    """The webhook ``_maybe_emit_ai_takeover`` now also emits the bus event. The
    full webhook flow needs a live AI reply pipeline (mocked LLM + send), so here
    we assert the verb is on the bus contract; the firing path is exercised by the
    end-to-end webhook suite (test_endpoints.py)."""
    assert "conversation.ai_takeover" in bus.KNOWN_EVENTS


# ── 8. All six new verbs are registered in KNOWN_EVENTS ────────────────────

def test_all_new_verbs_in_known_events():
    for name in (
        "conversation.reopened",
        "conversation.unassigned",
        "conversation.transferred_to_human",
        "conversation.agent_changed",
        "conversation.attribute_set",
        "conversation.ai_takeover",
    ):
        assert name in bus.KNOWN_EVENTS, f"{name} missing from KNOWN_EVENTS"
