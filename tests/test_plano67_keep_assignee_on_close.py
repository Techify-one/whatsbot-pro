"""Plano 67 — ``filter.conversation.clear_assignee_on_close`` seam.

The core drops the human assignee when a conversation is closed (default policy).
Plano 67 adds a generic seam so a plugin can KEEP the attendant assigned across the
close by returning ``False`` from ``filter.conversation.clear_assignee_on_close``.

These tests exercise the CORE seam directly (no plugin needed): they register an
in-process filter, drive ``conversation_service.set_status`` through the real HTTP
route (``build_app`` fixture), and assert the assignee is preserved (or cleared)
accordingly. The default (no filter) path is also locked by the lifecycle
characterization golden ``lifecycle_assign_then_close``; here we additionally prove
that ``active_agent_key`` is ALWAYS cleared regardless of the seam (D2).

    venv/bin/python -m pytest tests/test_plano67_keep_assignee_on_close.py -q
"""

from __future__ import annotations

_OPERATOR_TOKEN = None


def _operator_client(built):
    """Authenticate ``built.client`` as a fixed admin operator (plano 48 closes the
    API gate once ≥1 user exists). Mirrors the lifecycle-characterization helper."""
    global _OPERATOR_TOKEN
    from db.repositories import user_repo, session_repo
    from server.auth import generate_session_token
    if _OPERATOR_TOKEN is None:
        op = (user_repo.get_by_email("p67_operator@test.com")
              or user_repo.create(email="p67_operator@test.com", name="Operador",
                                  password_hash="x", role_keys=["admin"]))
        _OPERATOR_TOKEN = generate_session_token()
        session_repo.create(_OPERATOR_TOKEN, op["id"], user_agent="test", ip="127.0.0.1")
    built.client.headers["Authorization"] = f"Bearer {_OPERATOR_TOKEN}"
    return built


def _make_assigned_conversation(phone: str):
    """Create a contact + open conversation with a human assignee AND an active
    agent key set. Returns (conv_id, user_id). Pins the AI seed ON so the initial
    state is deterministic on the shared process DB."""
    from db.repositories import (
        contact_repo, conversation_repo, config_repo, user_repo,
    )
    config_repo.set("auto_reply", True)
    config_repo.set("default_ai_enabled", True)
    contact = contact_repo.get_or_create(phone)
    conv, _ = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net")
    user = user_repo.create(
        email=f"p67_{phone}@test.com", name="Atendente 67", password_hash="x")
    # assign_agent sets BOTH assignee_user_id and active_agent_key atomically, so we
    # can later prove that close keeps the assignee (seam) yet still drops the agent.
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=user["id"], active_agent_key="default")
    return conv["id"], user["id"]


def _conv(conv_id: int) -> dict:
    from db.repositories import conversation_repo
    return conversation_repo.get(conv_id)


def _close(built, conv_id: int):
    r = built.client.post(f"/api/conversations/{conv_id}/status",
                          json={"status": "closed"})
    assert r.status_code == 200, r.text


def test_close_keeps_assignee_when_filter_returns_false(build_app):
    """A plugin filter returning False → the human assignee is PRESERVED across the
    close, while active_agent_key is still cleared (D2)."""
    from plugins import events

    phone = "5511970000001"
    conv_id, uid = _make_assigned_conversation(phone)
    built = _operator_client(build_app(["gowa"]))

    calls = {"n": 0}

    def keep(ctx, value):
        calls["n"] += 1
        assert value is True  # the core passes its default (True) into the seam
        return False

    events.register_filter("p67", "filter.conversation.clear_assignee_on_close", keep)
    try:
        _close(built, conv_id)
    finally:
        events._filters.pop("filter.conversation.clear_assignee_on_close", None)

    row = _conv(conv_id)
    assert calls["n"] == 1, "the seam must be consulted exactly once on close"
    assert row["status"] == "closed"
    assert row["assignee_user_id"] == uid, \
        "filter returned False → attendant must stay assigned"
    assert row["active_agent_key"] is None, \
        "active_agent_key is ALWAYS cleared on close (D2)"


def test_close_clears_assignee_by_default(build_app):
    """No clear-assignee filter registered → legacy behavior: closing clears the
    assignee AND the active agent (byte-identical to before plano 67)."""
    from plugins import events

    phone = "5511970000002"
    conv_id, _uid = _make_assigned_conversation(phone)
    built = _operator_client(build_app(["gowa"]))
    events._filters.pop("filter.conversation.clear_assignee_on_close", None)

    _close(built, conv_id)

    row = _conv(conv_id)
    assert row["status"] == "closed"
    assert row["assignee_user_id"] is None, "default close must clear the assignee"
    assert row["active_agent_key"] is None


def test_close_clears_when_filter_returns_none(build_app):
    """Defensive: a broken/aborting filter (returns None) falls back to the SAFE
    default = clear (never accidentally keeps the assignee)."""
    from plugins import events

    phone = "5511970000003"
    conv_id, _uid = _make_assigned_conversation(phone)
    built = _operator_client(build_app(["gowa"]))

    def broken(ctx, value):
        return None

    events.register_filter("p67n", "filter.conversation.clear_assignee_on_close", broken)
    try:
        _close(built, conv_id)
    finally:
        events._filters.pop("filter.conversation.clear_assignee_on_close", None)

    row = _conv(conv_id)
    assert row["assignee_user_id"] is None, \
        "None from the filter must fall back to clearing (safe default)"
