"""Plano 166/02 R5: safe AI-driven routing to teams."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from agent.execution import set_current_step_agent
from agent.handoff import (
    TEAM_HANDOFF_TERMINAL_FIELD,
    clear_team_handoff_outcome,
    consume_team_handoff_outcome,
    turn_handed_off,
)
from agent.tools.transfer_to_team import (
    TRANSFER_TO_TEAM_TOOL,
    execute,
    team_destinations,
)
from channels import ai_settings
from db.repositories import (
    agent_repo,
    channel_repo,
    config_repo,
    conversation_repo,
    inbox_repo,
    team_repo,
    user_repo,
)

_CHANNELS: list[str] = []
_TEAMS: list[int] = []
_AGENTS: list[str] = []
_USERS: list[int] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_r5(_engine_ready):
    yield
    from db.repositories import session_repo

    set_current_step_agent(None)
    for channel_id in reversed(_CHANNELS):
        channel_repo.delete(channel_id)
    for team_id in reversed(_TEAMS):
        team_repo.delete(team_id)
    for agent_key in reversed(_AGENTS):
        agent_repo.delete(agent_key)
    for user_id in reversed(_USERS):
        session_repo.delete_for_user(user_id)
        user_repo.delete(user_id)


def _agent(*, routing_team_ids=None, tool_names=None) -> dict:
    key = f"r5_agent_{uuid.uuid4().hex[:10]}"
    row = agent_repo.save(
        key, display_name=key, prompt="R5", model_config={},
        tool_names=tool_names, enabled=True,
        routing_team_ids=routing_team_ids)
    _AGENTS.append(key)
    return row


def _team(*, mode="manual", ai_assignable=True,
          default_user_id=None, default_agent_key=None, members=None) -> dict:
    row = team_repo.create(
        f"R5 {uuid.uuid4().hex[:8]}", routing_mode=mode,
        ai_assignable=ai_assignable, default_user_id=default_user_id,
        default_agent_key=default_agent_key,
        member_user_ids=list(members or []))
    _TEAMS.append(row["id"])
    return row


def _user() -> dict:
    marker = uuid.uuid4().hex
    row = user_repo.create(
        email=f"r5_{marker}@test.local", name=f"R5 {marker[:6]}",
        password_hash="x")
    _USERS.append(row["id"])
    return row


def _channel() -> tuple[str, int]:
    channel_id = f"r5_{uuid.uuid4().hex[:12]}"
    channel_repo.create(
        id=channel_id, provider="test", display_name=channel_id,
        config=json.dumps({"ai": {"ai_enabled": True}}))
    inbox = inbox_repo.create(channel_id=channel_id, name=channel_id)
    ai_settings.reset_cache(channel_id)
    _CHANNELS.append(channel_id)
    return channel_id, int(inbox["id"])


def _prepare_ai(built) -> None:
    config_repo.set("auto_reply", True)
    config_repo.set("default_ai_enabled", True)
    built.agent_handler.default_ai_enabled = True


def test_destination_source_is_fail_closed_and_dual_opt_in(_engine_ready):
    allowed = _team(ai_assignable=True)
    no_opt_in = _team(ai_assignable=False)
    inactive = _team(ai_assignable=True)
    team_repo.deactivate(inactive["id"])

    assert team_destinations({"routing_team_ids": None}) == []
    assert team_destinations({"routing_team_ids": []}) == []
    result = team_destinations({
        "routing_team_ids": [allowed["id"], no_opt_in["id"], inactive["id"]],
    })
    assert [team["id"] for team in result] == [allowed["id"]]

    from agent.agent_factory import _team_destinations_section
    section = _team_destinations_section({"routing_team_ids": [
        allowed["id"], no_opt_in["id"], inactive["id"]]})
    assert f"- {allowed['id']} — {allowed['name']}" in section
    assert no_opt_in["name"] not in section
    assert inactive["name"] not in section


def test_engine_keeps_handoff_outcome_outside_model_args(monkeypatch, _engine_ready):
    """Internal routing metadata is not exposed or persisted as model input."""
    from agent import agno_engine
    from agent.handoff import record_team_handoff_outcome

    assert TEAM_HANDOFF_TERMINAL_FIELD not in json.dumps(TRANSFER_TO_TEAM_TOOL)
    monkeypatch.setattr(
        agno_engine, "apply_filter_sync", lambda _hook, payload, _ctx: payload)
    monkeypatch.setattr(agno_engine, "emit_with_filter_sync", lambda *_a, **_kw: None)
    persisted: list[dict] = []
    monkeypatch.setattr(
        agno_engine, "track_step", lambda _kind, payload: persisted.append(payload))

    class Handler:
        @staticmethod
        def _dispatch_tool(_contact, _name, _args):
            record_team_handoff_outcome(True)
            return "encaminhado"

    executed: list[dict] = []
    entrypoint = agno_engine._make_sync_entrypoint(
        Handler(), object(), "5511999999999", "transfer_to_team", executed)
    entrypoint(team_id=42, reason="financeiro")

    assert executed == [{
        "tool": "transfer_to_team",
        "args": {"team_id": 42, "reason": "financeiro"},
        "result": "encaminhado",
        TEAM_HANDOFF_TERMINAL_FIELD: True,
    }]
    assert persisted == [{
        "tool": "transfer_to_team",
        "args": {"team_id": 42, "reason": "financeiro"},
        "result": "encaminhado",
    }]


def test_executor_is_channel_scoped_and_terminal_for_queue(build_app):
    built = build_app(["gowa"])
    _prepare_ai(built)
    team = _team(mode="manual")
    caller = _agent(routing_team_ids=[team["id"]], tool_names=None)
    channel_a, _inbox_a = _channel()
    channel_b, _inbox_b = _channel()
    phone = f"55{uuid.uuid4().int}"[:13]
    memory_a = built.agent_handler._get_contact(phone, channel_id=channel_a)
    memory_b = built.agent_handler._get_contact(phone, channel_id=channel_b)
    conv_a = conversation_repo.get(
        memory_a.add_message("user", "canal A")["conversation_id"])
    conv_b = conversation_repo.get(
        memory_b.add_message("user", "canal B")["conversation_id"])
    set_current_step_agent(caller["agent_key"])
    args = {"team_id": team["id"], "reason": "fila especializada"}
    clear_team_handoff_outcome()

    feedback = execute(SimpleNamespace(contact=memory_a), args)

    assert "Erro" not in feedback
    assert conversation_repo.get(conv_a["id"])["team_id"] == team["id"]
    assert conversation_repo.get(conv_b["id"])["team_id"] is None
    assert args == {"team_id": team["id"], "reason": "fila especializada"}
    assert consume_team_handoff_outcome() is True
    assert turn_handed_off([{
        "tool": "transfer_to_team", TEAM_HANDOFF_TERMINAL_FIELD: True,
    }]) is True


def test_executor_fixed_ai_continues_and_fallback_ends_hop(build_app):
    built = build_app(["gowa"])
    _prepare_ai(built)
    target_agent = _agent()
    fixed_ai = _team(
        mode="fixed_ai", default_agent_key=target_agent["agent_key"])
    caller = _agent(routing_team_ids=[fixed_ai["id"]])
    channel_id, _inbox_id = _channel()
    phone = f"55{uuid.uuid4().int}"[:13]
    memory = built.agent_handler._get_contact(phone, channel_id=channel_id)
    conv = conversation_repo.get(
        memory.add_message("user", "preciso do especialista")["conversation_id"])
    set_current_step_agent(caller["agent_key"])
    args = {"team_id": fixed_ai["id"], "reason": "especialidade"}
    clear_team_handoff_outcome()

    feedback = execute(SimpleNamespace(contact=memory), args)

    assert "Erro" not in feedback
    after = conversation_repo.get(conv["id"])
    assert after["active_agent_key"] == target_agent["agent_key"]
    assert after["ai_active"] == 1
    assert args == {"team_id": fixed_ai["id"], "reason": "especialidade"}
    assert consume_team_handoff_outcome() is False
    assert turn_handed_off([{
        "tool": "transfer_to_team", TEAM_HANDOFF_TERMINAL_FIELD: False,
    }]) is False

    # A fixed-user target outside the inbox triggers the canonical safe fallback.
    outside = _user()
    invalid = _team(
        mode="fixed_user", default_user_id=outside["id"],
        members=[outside["id"]])
    caller = agent_repo.save(
        caller["agent_key"], display_name=caller["display_name"], prompt="R5",
        model_config={}, tool_names=None, enabled=True,
        routing_team_ids=[fixed_ai["id"], invalid["id"]])
    set_current_step_agent(caller["agent_key"])
    args = {"team_id": invalid["id"], "reason": "fila humana"}
    clear_team_handoff_outcome()
    feedback = execute(SimpleNamespace(contact=memory), args)
    after = conversation_repo.get(conv["id"])
    assert "fixed_user_target_ineligible" in feedback
    assert after["team_id"] == invalid["id"]
    assert after["assignee_user_id"] is None
    assert after["active_agent_key"] is None
    assert after["ai_active"] == 0
    assert args == {"team_id": invalid["id"], "reason": "fila humana"}
    assert consume_team_handoff_outcome() is True


def test_executor_rejects_non_allowlisted_team_and_missing_step_agent(build_app):
    built = build_app(["gowa"])
    _prepare_ai(built)
    allowed = _team()
    denied = _team()
    caller = _agent(routing_team_ids=[allowed["id"]])
    channel_id, _inbox_id = _channel()
    memory = built.agent_handler._get_contact(
        f"55{uuid.uuid4().int}"[:13], channel_id=channel_id)
    memory.add_message("user", "oi")

    set_current_step_agent(caller["agent_key"])
    args = {"team_id": denied["id"], "reason": "não autorizado"}
    feedback = execute(SimpleNamespace(contact=memory), args)
    assert "não está autorizado" in feedback
    assert args == {"team_id": denied["id"], "reason": "não autorizado"}

    set_current_step_agent(None)
    feedback = execute(SimpleNamespace(contact=memory), {
        "team_id": allowed["id"], "reason": "sem autoria"})
    assert "identificar o agente" in feedback
