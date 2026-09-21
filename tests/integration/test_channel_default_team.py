"""Plano 166/02 R4: channel team defaults and reopen precedence."""

from __future__ import annotations

import json
import uuid

import pytest

from channels import ai_settings
from db.repositories import (
    agent_repo,
    channel_repo,
    config_repo,
    conversation_repo,
    inbox_member_repo,
    inbox_repo,
    message_repo,
    team_repo,
    user_repo,
)

_USERS: list[int] = []
_AGENTS: list[str] = []
_TEAMS: list[int] = []
_CHANNELS: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_r4(_engine_ready):
    yield
    from db.repositories import session_repo

    for channel_id in reversed(_CHANNELS):
        channel_repo.delete(channel_id)
    for team_id in reversed(_TEAMS):
        team_repo.delete(team_id)
    for agent_key in reversed(_AGENTS):
        agent_repo.delete(agent_key)
    for user_id in reversed(_USERS):
        session_repo.delete_for_user(user_id)
        user_repo.delete(user_id)


def _user() -> dict:
    marker = uuid.uuid4().hex
    user = user_repo.create(
        email=f"r4_{marker}@test.local", name=f"R4 {marker[:6]}",
        password_hash="x")
    _USERS.append(user["id"])
    return user


def _agent() -> dict:
    key = f"r4_agent_{uuid.uuid4().hex[:10]}"
    agent = agent_repo.save(
        key, display_name=key, prompt="R4", model_config={},
        tool_names=None, enabled=True)
    _AGENTS.append(key)
    return agent


def _channel(ai: dict | None = None) -> tuple[str, int]:
    channel_id = f"r4_{uuid.uuid4().hex[:12]}"
    channel_repo.create(
        id=channel_id, provider="test", display_name=channel_id,
        config=json.dumps({"ai": ai or {}}))
    inbox = inbox_repo.create(channel_id=channel_id, name=channel_id)
    _CHANNELS.append(channel_id)
    ai_settings.reset_cache(channel_id)
    return channel_id, int(inbox["id"])


def _set_ai_config(channel_id: str, ai: dict) -> None:
    channel_repo.update(channel_id, config=json.dumps({"ai": ai}))
    ai_settings.reset_cache(channel_id)


def _seed(built, phone: str, channel_id: str) -> dict:
    memory = built.agent_handler._get_contact(phone, channel_id=channel_id)
    saved = memory.add_message("user", "oi")
    return conversation_repo.get(saved["conversation_id"])


def _setup_ai(built) -> None:
    config_repo.set("auto_reply", True)
    config_repo.set("default_ai_enabled", True)
    built.agent_handler.default_ai_enabled = True


def test_team_default_routes_birth_without_extra_notice(build_app):
    built = build_app(["gowa"])
    _setup_ai(built)
    user = _user()
    channel_id, inbox_id = _channel()
    inbox_member_repo.set_members(inbox_id, [user["id"]])
    team = team_repo.create(
        f"R4 nascimento {uuid.uuid4().hex[:6]}",
        routing_mode="fixed_user", default_user_id=user["id"],
        member_user_ids=[user["id"]])
    _TEAMS.append(team["id"])
    _set_ai_config(channel_id, {"default_assignee_team_id": team["id"]})

    conv = _seed(built, f"55{uuid.uuid4().int}"[:13], channel_id)
    assert conv["team_id"] == team["id"]
    assert conv["assignee_user_id"] == user["id"]
    assert conv["active_agent_key"] is None
    assert conv["ai_active"] == 0
    notices = [m.get("content", "") for m in message_repo.get_by_conversation(conv["id"])
               if m.get("role") == "conversation_event"]
    assert not any("encaminh" in content.lower() for content in notices)
    assert not any("distribu" in content.lower() for content in notices)


def test_reopen_precedence_live_owner_then_existing_team_then_channel(build_app):
    built = build_app(["gowa"])
    _setup_ai(built)
    live_owner = _user()
    existing_target = _user()
    channel_human = _user()
    channel_agent = _agent()
    channel_id, inbox_id = _channel()
    inbox_member_repo.set_members(
        inbox_id, [live_owner["id"], existing_target["id"], channel_human["id"]])
    existing_team = team_repo.create(
        f"R4 existente {uuid.uuid4().hex[:6]}", routing_mode="fixed_user",
        default_user_id=existing_target["id"], member_user_ids=[existing_target["id"]])
    _TEAMS.append(existing_team["id"])
    default_team = team_repo.create(
        f"R4 canal {uuid.uuid4().hex[:6]}", routing_mode="manual")
    _TEAMS.append(default_team["id"])
    raw_triple = {
        "default_assignee_user_id": channel_human["id"],
        "default_assignee_agent_key": channel_agent["agent_key"],
        "default_assignee_team_id": default_team["id"],
    }

    # 1) A live human owner survives both the existing team and all channel defaults.
    _set_ai_config(channel_id, {})
    phone_owner = f"55{uuid.uuid4().int}"[:13]
    memory_owner = built.agent_handler._get_contact(phone_owner, channel_id=channel_id)
    conv_owner = conversation_repo.get(
        memory_owner.add_message("user", "abre")["conversation_id"])
    conversation_repo.set_team(conv_owner["id"], existing_team["id"])
    conversation_repo.set_assignee(conv_owner["id"], live_owner["id"])
    conversation_repo.set_status(
        conv_owner["id"], "closed", clear_assignee=False)
    _set_ai_config(channel_id, raw_triple)
    memory_owner.add_message("user", "reabre")
    reopened = conversation_repo.get(conv_owner["id"])
    assert reopened["assignee_user_id"] == live_owner["id"]
    assert reopened["team_id"] == existing_team["id"]

    # 2) Without a live owner, the conversation's existing team beats the channel.
    _set_ai_config(channel_id, {})
    phone_team = f"55{uuid.uuid4().int}"[:13]
    memory_team = built.agent_handler._get_contact(phone_team, channel_id=channel_id)
    conv_team = conversation_repo.get(
        memory_team.add_message("user", "abre")["conversation_id"])
    conversation_repo.set_team(conv_team["id"], existing_team["id"])
    conversation_repo.set_status(conv_team["id"], "closed")
    _set_ai_config(channel_id, raw_triple)
    memory_team.add_message("user", "reabre")
    reopened = conversation_repo.get(conv_team["id"])
    assert reopened["team_id"] == existing_team["id"]
    assert reopened["assignee_user_id"] == existing_target["id"]

    # 3) Without an existing team, the legacy-safe channel precedence is
    # human > AI > team when a hand-edited config contains all three.
    _set_ai_config(channel_id, {})
    phone_channel = f"55{uuid.uuid4().int}"[:13]
    memory_channel = built.agent_handler._get_contact(phone_channel, channel_id=channel_id)
    conv_channel = conversation_repo.get(
        memory_channel.add_message("user", "abre")["conversation_id"])
    conversation_repo.set_status(conv_channel["id"], "closed")
    _set_ai_config(channel_id, raw_triple)
    memory_channel.add_message("user", "reabre")
    reopened = conversation_repo.get(conv_channel["id"])
    assert reopened["team_id"] is None
    assert reopened["assignee_user_id"] == channel_human["id"]
    assert reopened["active_agent_key"] is None


def test_inactive_or_invalid_team_strategy_stays_in_safe_queue(build_app):
    built = build_app(["gowa"])
    _setup_ai(built)
    channel_id, inbox_id = _channel()
    target = _user()
    # The target belongs to the team but not the inbox: fixed_user is invalid.
    invalid_target_team = team_repo.create(
        f"R4 alvo inválido {uuid.uuid4().hex[:6]}", routing_mode="fixed_user",
        default_user_id=target["id"], member_user_ids=[target["id"]])
    _TEAMS.append(invalid_target_team["id"])
    _set_ai_config(channel_id, {
        "default_assignee_team_id": invalid_target_team["id"]})
    conv = _seed(built, f"55{uuid.uuid4().int}"[:13], channel_id)
    assert conv["team_id"] == invalid_target_team["id"]
    assert conv["assignee_user_id"] is None
    assert conv["active_agent_key"] is None
    assert conv["ai_active"] == 0

    inactive = team_repo.create(f"R4 inativo {uuid.uuid4().hex[:6]}")
    _TEAMS.append(inactive["id"])
    team_repo.deactivate(inactive["id"])
    _set_ai_config(channel_id, {"default_assignee_team_id": inactive["id"]})
    conv = _seed(built, f"55{uuid.uuid4().int}"[:13], channel_id)
    assert conv["team_id"] == inactive["id"]
    assert conv["assignee_user_id"] is None
    assert conv["active_agent_key"] is None
    assert conv["ai_active"] == 0


def test_channel_update_normalizes_partial_destination_and_catalog(
        build_app, authenticated_admin):
    built = build_app(["gowa"])
    authenticated_admin(built.client)
    user = _user()
    agent = _agent()
    team = team_repo.create(f"R4 catálogo {uuid.uuid4().hex[:6]}")
    _TEAMS.append(team["id"])
    channel_id, _inbox_id = _channel()

    response = built.client.put(f"/api/channels/{channel_id}", json={
        "config": {"ai": {"ai_enabled": True,
                          "default_assignee_team_id": str(team["id"])}},
    })
    assert response.status_code == 200, response.text
    saved = json.loads(channel_repo.get(channel_id)["config"])["ai"]
    assert saved["default_assignee_team_id"] == team["id"]
    assert "default_assignee_user_id" not in saved
    assert "default_assignee_agent_key" not in saved

    response = built.client.put(f"/api/channels/{channel_id}", json={
        "config": {"ai": {
            "default_assignee_user_id": user["id"],
            "default_assignee_agent_key": agent["agent_key"],
            "default_assignee_team_id": team["id"],
        }},
    })
    assert response.status_code == 200, response.text
    saved = json.loads(channel_repo.get(channel_id)["config"])["ai"]
    assert saved == {"default_assignee_user_id": user["id"]}

    catalog = built.client.get("/api/channels/assignable-users")
    assert catalog.status_code == 200, catalog.text
    assert team["id"] in {item["id"] for item in catalog.json()["data"]["teams"]}
