"""Plano 166/02 R1-R2: schema and canonical transactional team routing."""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, inspect, select, update


@pytest.fixture
def routing_world(_engine_ready):
    """Create isolated channels/inboxes/users/teams and clean them after a test."""
    from db.engine import get_engine
    from db.repositories import config_repo
    from db.tables import config

    created = {
        "channels": [], "contacts": [], "teams": [], "users": [], "agents": [],
    }
    with get_engine().connect() as conn:
        original_auto_reply_raw = conn.execute(
            select(config.c.value).where(config.c.key == "auto_reply")
        ).scalar_one_or_none()
    original_auto_reply = config_repo.get("auto_reply", True)

    class World:
        def inbox(self, *, ai_enabled: bool = True) -> int:
            from db.repositories import channel_repo, inbox_repo

            key = f"routing_{uuid.uuid4().hex[:12]}"
            channel_repo.create(
                id=key,
                provider="test",
                display_name=key,
                config=json.dumps({"ai": {"ai_enabled": ai_enabled}}),
            )
            created["channels"].append(key)
            return int(inbox_repo.create(
                channel_id=key, name=key, channel_type="test")["id"])

        def user(self, *, active: bool = True) -> dict:
            from db.repositories import user_repo

            marker = uuid.uuid4().hex
            row = user_repo.create(
                email=f"routing_{marker}@test.com",
                name=f"Routing {marker[:6]}",
                password_hash="x",
            )
            created["users"].append(row["id"])
            if not active:
                row = user_repo.update_info(row["id"], is_active=0)
            return row

        def agent(self, *, enabled: bool = True,
                  routing_team_ids=None) -> dict:
            from db.repositories import agent_repo

            key = f"routing_agent_{uuid.uuid4().hex[:10]}"
            row = agent_repo.save(
                key,
                display_name=key,
                prompt="",
                model_config={},
                tool_names=None,
                enabled=enabled,
                routing_team_ids=routing_team_ids,
            )
            created["agents"].append(key)
            return row

        def team(self, *, mode: str = "manual", members=None,
                 default_user_id=None, default_agent_key=None,
                 active: bool = True) -> dict:
            from db.repositories import team_repo

            row = team_repo.create(
                f"Routing {uuid.uuid4().hex[:10]}",
                member_user_ids=list(members or []),
                routing_mode=mode,
                default_user_id=default_user_id,
                default_agent_key=default_agent_key,
            )
            created["teams"].append(row["id"])
            if not active:
                row = team_repo.update(row["id"], is_active=False)
            return row

        def memberships(self, inbox_id: int, user_ids: list[int]) -> None:
            from db.repositories import inbox_member_repo

            inbox_member_repo.set_members(inbox_id, user_ids)

        def conversation(self, inbox_id: int) -> dict:
            from db.repositories import contact_repo, conversation_repo

            phone = "55" + str(uuid.uuid4().int)[:11]
            contact = contact_repo.get_or_create(phone)
            created["contacts"].append(contact["id"])
            conv, _event = conversation_repo.resolve_for_contact_ex(
                contact["id"], f"{phone}@test", inbox_id=inbox_id,
                ai_active_seed=0,
            )
            return conv

        def set_auto_reply(self, enabled: bool) -> None:
            config_repo.set("auto_reply", enabled)

    yield World()

    # Restore process-shared config first, then remove dependants in FK order.
    if original_auto_reply_raw is None:
        with get_engine().begin() as conn:
            conn.execute(delete(config).where(config.c.key == "auto_reply"))
    else:
        config_repo.set("auto_reply", original_auto_reply)
    from channels import ai_settings
    from db.tables import ai_agents, channels, contacts, teams, users

    ai_settings.reset_cache()
    with get_engine().begin() as conn:
        if created["channels"]:
            conn.execute(delete(channels).where(channels.c.id.in_(created["channels"])))
        if created["contacts"]:
            conn.execute(delete(contacts).where(contacts.c.id.in_(created["contacts"])))
        if created["teams"]:
            conn.execute(delete(teams).where(teams.c.id.in_(created["teams"])))
        if created["agents"]:
            conn.execute(delete(ai_agents).where(
                ai_agents.c.agent_key.in_(created["agents"])))
        if created["users"]:
            conn.execute(delete(users).where(users.c.id.in_(created["users"])))


def test_routing_schema_matches_contract(_engine_ready):
    from db.engine import get_engine

    inspector = inspect(get_engine())
    team_columns = {column["name"]: column for column in inspector.get_columns("teams")}
    assert team_columns["routing_mode"]["default"] in ("'manual'::text", "'manual'")
    assert {"default_user_id", "default_agent_key", "ai_assignable"} <= set(team_columns)
    assert "routing_team_ids" in {
        column["name"] for column in inspector.get_columns("ai_agents")}
    assert inspector.get_pk_constraint("team_routing_state")["constrained_columns"] == [
        "team_id", "inbox_id"]


def test_agent_routing_team_allowlist_round_trip_and_history(routing_world):
    from db.repositories import agent_repo

    agent = routing_world.agent(routing_team_ids=None)
    assert agent["routing_team_ids"] is None
    v1 = agent["version"]

    agent = agent_repo.save(
        agent["agent_key"], display_name=agent["display_name"], prompt="",
        model_config={}, tool_names=None, enabled=True, routing_team_ids=[])
    assert agent["routing_team_ids"] == []
    v2 = agent["version"]

    agent = agent_repo.save(
        agent["agent_key"], display_name=agent["display_name"], prompt="",
        model_config={}, tool_names=None, enabled=True, routing_team_ids=[7, 11])
    assert agent_repo.get(agent["agent_key"])["routing_team_ids"] == [7, 11]
    assert agent_repo.get_snapshot(agent["agent_key"], v1)["routing_team_ids"] is None
    assert agent_repo.get_snapshot(agent["agent_key"], v2)["routing_team_ids"] == []

    rolled = agent_repo.rollback(agent["agent_key"], v2)
    assert rolled["routing_team_ids"] == []


def test_manual_route_clears_both_owners_and_pauses_ai(routing_world):
    from app.services import team_routing_service
    from db.repositories import conversation_repo

    inbox_id = routing_world.inbox()
    human = routing_world.user()
    agent = routing_world.agent()
    team = routing_world.team(mode="manual")
    conv = routing_world.conversation(inbox_id)
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=human["id"],
        active_agent_key=agent["agent_key"], ai_active=1)

    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])

    assert result.strategy == "manual"
    assert result.fallback_reason is None
    assert result.after.team_id == team["id"]
    assert result.after.assignee_user_id is None
    assert result.after.active_agent_key is None
    assert result.after.ai_active is False


def test_fixed_user_requires_active_team_and_inbox_membership(routing_world):
    from app.services import team_routing_service
    from db.repositories import team_repo, user_repo

    inbox_id = routing_world.inbox()
    eligible = routing_world.user()
    outside_inbox = routing_world.user()
    outside_team = routing_world.user()
    inactive = routing_world.user()
    routing_world.memberships(
        inbox_id, [eligible["id"], outside_team["id"], inactive["id"]])
    team = routing_world.team(
        mode="fixed_user",
        members=[eligible["id"], outside_inbox["id"], inactive["id"]],
        default_user_id=eligible["id"],
    )
    user_repo.update_info(inactive["id"], is_active=0)
    conv = routing_world.conversation(inbox_id)

    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.after.assignee_user_id == eligible["id"]
    assert result.fallback_reason is None

    for target in (outside_inbox["id"], outside_team["id"], inactive["id"]):
        team_repo.update(team["id"], default_user_id=target)
        result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
        assert result.after.assignee_user_id is None
        assert result.after.active_agent_key is None
        assert result.after.ai_active is False
        assert result.fallback_reason == "fixed_user_target_ineligible"


def test_fixed_ai_validates_agent_and_global_channel_gates(routing_world):
    from app.services import team_routing_service
    from channels import ai_settings
    from db.engine import get_engine
    from db.repositories import channel_repo
    from db.tables import ai_agents, inboxes

    inbox_id = routing_world.inbox(ai_enabled=True)
    agent = routing_world.agent(enabled=True)
    team = routing_world.team(
        mode="fixed_ai", default_agent_key=agent["agent_key"])
    conv = routing_world.conversation(inbox_id)

    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.after.active_agent_key == agent["agent_key"]
    assert result.after.ai_active is True

    routing_world.set_auto_reply(False)
    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.fallback_reason == "global_ai_disabled"
    assert result.after.ai_active is False

    routing_world.set_auto_reply(True)
    with get_engine().begin() as conn:
        channel_id = conn.execute(
            select(inboxes.c.channel_id).where(inboxes.c.id == inbox_id)).scalar_one()
    channel_repo.update(
        channel_id, config=json.dumps({"ai": {"ai_enabled": False}}))
    ai_settings.reset_cache(channel_id)
    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.fallback_reason == "channel_ai_disabled"

    channel_repo.update(
        channel_id, config=json.dumps({"ai": {"ai_enabled": True}}))
    ai_settings.reset_cache(channel_id)
    with get_engine().begin() as conn:
        conn.execute(update(ai_agents).where(
            ai_agents.c.agent_key == agent["agent_key"]).values(enabled=0))
    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.fallback_reason == "fixed_ai_target_disabled"
    assert result.after.active_agent_key is None


@pytest.mark.parametrize(("mode", "fallback"), [
    ("round_robin", "no_eligible_users"),
    ("fixed_user", "fixed_user_target_missing"),
    ("fixed_ai", "fixed_ai_target_missing"),
])
def test_empty_or_missing_targets_fall_back_to_paused_team_queue(
        routing_world, mode, fallback):
    from app.services import team_routing_service

    inbox_id = routing_world.inbox()
    team = routing_world.team(mode=mode)
    conv = routing_world.conversation(inbox_id)

    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.fallback_reason == fallback
    assert result.after.team_id == team["id"]
    assert result.after.assignee_user_id is None
    assert result.after.active_agent_key is None
    assert result.after.ai_active is False


def test_inactive_team_falls_back_without_losing_team_link(routing_world):
    from app.services import team_routing_service

    inbox_id = routing_world.inbox()
    team = routing_world.team(mode="manual", active=False)
    conv = routing_world.conversation(inbox_id)

    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.fallback_reason == "team_inactive"
    assert result.after.team_id == team["id"]
    assert result.after.assignee_user_id is None
    assert result.after.active_agent_key is None
    assert result.after.ai_active is False


def test_round_robin_cycles_and_isolates_cursor_by_inbox(routing_world):
    from app.services import team_routing_service

    inbox_a = routing_world.inbox()
    inbox_b = routing_world.inbox()
    members = [routing_world.user() for _ in range(3)]
    ids = [user["id"] for user in members]
    routing_world.memberships(inbox_a, ids)
    routing_world.memberships(inbox_b, ids)
    team = routing_world.team(mode="round_robin", members=ids)
    conv_a = routing_world.conversation(inbox_a)
    conv_b = routing_world.conversation(inbox_b)

    chosen_a = [
        team_routing_service.route_to_team_sync(conv_a["id"], team["id"])
        .after.assignee_user_id
        for _ in range(4)
    ]
    assert chosen_a == [ids[0], ids[1], ids[2], ids[0]]

    # Inbox B owns a distinct cursor and starts its own sequence at A.
    first_b = team_routing_service.route_to_team_sync(conv_b["id"], team["id"])
    assert first_b.after.assignee_user_id == ids[0]


def test_round_robin_removed_cursor_restarts_at_first_eligible(routing_world):
    from app.services import team_routing_service
    from db.engine import get_engine
    from db.tables import users

    inbox_id = routing_world.inbox()
    first = routing_world.user()
    second = routing_world.user()
    ids = [first["id"], second["id"]]
    routing_world.memberships(inbox_id, ids)
    team = routing_world.team(mode="round_robin", members=ids)
    conv = routing_world.conversation(inbox_id)

    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.after.assignee_user_id == first["id"]

    # FKs cascade memberships and SET NULL the durable cursor.
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id == first["id"]))
    result = team_routing_service.route_to_team_sync(conv["id"], team["id"])
    assert result.after.assignee_user_id == second["id"]


def test_round_robin_concurrent_routes_consume_distinct_turns(routing_world):
    from app.services import team_routing_service

    inbox_id = routing_world.inbox()
    members = [routing_world.user(), routing_world.user()]
    ids = [user["id"] for user in members]
    routing_world.memberships(inbox_id, ids)
    team = routing_world.team(mode="round_robin", members=ids)
    conversations = [
        routing_world.conversation(inbox_id),
        routing_world.conversation(inbox_id),
    ]
    barrier = threading.Barrier(2)

    def route(conv_id: int) -> int:
        barrier.wait(timeout=5)
        result = team_routing_service.route_to_team_sync(conv_id, team["id"])
        return result.after.assignee_user_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        selected = list(pool.map(route, [row["id"] for row in conversations]))
    assert set(selected) == set(ids)


def test_cursor_and_conversation_roll_back_together(routing_world, monkeypatch):
    from db.repositories import conversation_repo, team_routing_repo

    inbox_id = routing_world.inbox()
    user = routing_world.user()
    routing_world.memberships(inbox_id, [user["id"]])
    team = routing_world.team(mode="round_robin", members=[user["id"]])
    conv = routing_world.conversation(inbox_id)
    before = conversation_repo.get(conv["id"])

    def explode(_conn, _conversation_id, _values):
        raise RuntimeError("forced write failure")

    monkeypatch.setattr(team_routing_repo, "_write_conversation", explode)
    with pytest.raises(RuntimeError, match="forced write failure"):
        team_routing_repo.route_to_team(conv["id"], team["id"])

    assert team_routing_repo.routing_state(team["id"], inbox_id) is None
    after = conversation_repo.get(conv["id"])
    assert after["team_id"] == before["team_id"]
    assert after["assignee_user_id"] == before["assignee_user_id"]
    assert after["active_agent_key"] == before["active_agent_key"]
    assert after["ai_active"] == before["ai_active"]
