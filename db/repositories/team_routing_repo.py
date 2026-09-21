"""Canonical transactional write for team routing.

The operation owns team, human/AI assignment, the conversation AI gate and the
round-robin cursor in one PostgreSQL transaction. Callers authorize the actor
before entering here; this module contains no HTTP or Request dependency.
"""

from __future__ import annotations

import time

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.engine import get_engine
from db.tables import (
    ai_agents,
    conversations,
    inbox_members,
    team_members,
    team_routing_state,
    teams,
    users,
)
from domain.team_routing import RoutingSnapshot, TeamRoutingError, TeamRoutingResult


def _snapshot(row) -> RoutingSnapshot:
    return RoutingSnapshot(
        team_id=row.get("team_id"),
        assignee_user_id=row.get("assignee_user_id"),
        active_agent_key=row.get("active_agent_key"),
        ai_active=bool(row.get("ai_active", 0)),
    )


def _eligible_user_ids(conn, *, team_id: int, inbox_id: int) -> list[int]:
    """Active users belonging to both the team and the conversation inbox."""
    rows = conn.execute(
        select(users.c.id)
        .select_from(
            users.join(team_members, team_members.c.user_id == users.c.id)
            .join(
                inbox_members,
                and_(
                    inbox_members.c.user_id == users.c.id,
                    inbox_members.c.inbox_id == inbox_id,
                ),
            )
        )
        .where(users.c.is_active == 1)
        .where(team_members.c.team_id == team_id)
        .order_by(users.c.id)
    ).all()
    return [int(row[0]) for row in rows]


def _write_conversation(conn, conversation_id: int, values: dict) -> None:
    """Small seam kept separate so rollback of cursor+conversation is testable."""
    result = conn.execute(
        update(conversations)
        .where(conversations.c.id == conversation_id)
        .values(**values)
    )
    if (result.rowcount or 0) != 1:
        raise TeamRoutingError(
            "conversation_disappeared",
            "A conversa deixou de existir durante o encaminhamento.",
        )


def route_to_team(
    conversation_id: int,
    team_id: int,
    *,
    fixed_ai_allowed: bool = True,
    fixed_ai_gate_reason: str | None = None,
) -> TeamRoutingResult:
    """Route once, using lock order team -> state -> conversation.

    A preliminary unlocked read obtains the immutable ``inbox_id`` needed for
    the state key. The conversation itself is then locked before any ownership
    write. If it vanished or changed inbox, the transaction rolls back, including
    a state row/cursor already created for this attempt.
    """
    conversation_id = int(conversation_id)
    team_id = int(team_id)
    now = time.time()

    with get_engine().begin() as conn:
        seed = conn.execute(
            select(conversations.c.id, conversations.c.inbox_id)
            .where(conversations.c.id == conversation_id)
        ).mappings().first()
        if seed is None:
            raise TeamRoutingError(
                "conversation_not_found", "Conversa não encontrada.")
        inbox_id = int(seed["inbox_id"])

        team = conn.execute(
            select(teams).where(teams.c.id == team_id).with_for_update()
        ).mappings().first()
        if team is None:
            raise TeamRoutingError("team_not_found", "Time não encontrado.")

        mode = str(team["routing_mode"])
        state = None
        if mode == "round_robin":
            conn.execute(
                pg_insert(team_routing_state)
                .values(
                    team_id=team_id,
                    inbox_id=inbox_id,
                    last_user_id=None,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        team_routing_state.c.team_id,
                        team_routing_state.c.inbox_id,
                    ]
                )
            )
            state = conn.execute(
                select(team_routing_state)
                .where(
                    team_routing_state.c.team_id == team_id,
                    team_routing_state.c.inbox_id == inbox_id,
                )
                .with_for_update()
            ).mappings().one()

        conv = conn.execute(
            select(conversations)
            .where(conversations.c.id == conversation_id)
            .with_for_update()
        ).mappings().first()
        if conv is None:
            raise TeamRoutingError(
                "conversation_disappeared",
                "A conversa deixou de existir durante o encaminhamento.",
            )
        if int(conv["inbox_id"]) != inbox_id:
            raise TeamRoutingError(
                "conversation_inbox_changed",
                "A inbox da conversa mudou durante o encaminhamento.",
            )

        before = _snapshot(conv)
        assignee_user_id = None
        active_agent_key = None
        ai_active = 0
        fallback_reason = None

        if not bool(team["is_active"]):
            fallback_reason = "team_inactive"
        elif mode == "manual":
            pass
        elif mode == "round_robin":
            eligible = _eligible_user_ids(
                conn, team_id=team_id, inbox_id=inbox_id)
            if not eligible:
                fallback_reason = "no_eligible_users"
            else:
                last_user_id = state["last_user_id"] if state else None
                assignee_user_id = next(
                    (user_id for user_id in eligible
                     if last_user_id is None or user_id > last_user_id),
                    eligible[0],
                )
                # Cursor first on purpose: any later failure must roll it back
                # together with the conversation write.
                conn.execute(
                    update(team_routing_state)
                    .where(
                        team_routing_state.c.team_id == team_id,
                        team_routing_state.c.inbox_id == inbox_id,
                    )
                    .values(last_user_id=assignee_user_id, updated_at=now)
                )
        elif mode == "fixed_user":
            target = team["default_user_id"]
            if target is None:
                fallback_reason = "fixed_user_target_missing"
            else:
                eligible = _eligible_user_ids(
                    conn, team_id=team_id, inbox_id=inbox_id)
                if int(target) not in eligible:
                    fallback_reason = "fixed_user_target_ineligible"
                else:
                    assignee_user_id = int(target)
        elif mode == "fixed_ai":
            target = team["default_agent_key"]
            if not target:
                fallback_reason = "fixed_ai_target_missing"
            else:
                enabled = conn.execute(
                    select(ai_agents.c.enabled)
                    .where(ai_agents.c.agent_key == target)
                ).scalar_one_or_none()
                if enabled is None:
                    fallback_reason = "fixed_ai_target_missing"
                elif not bool(enabled):
                    fallback_reason = "fixed_ai_target_disabled"
                elif not fixed_ai_allowed:
                    fallback_reason = fixed_ai_gate_reason or "fixed_ai_gate_disabled"
                else:
                    active_agent_key = str(target)
                    ai_active = 1
        else:  # The DB CHECK should make this unreachable; fail safe if drifted.
            fallback_reason = "invalid_routing_mode"

        values = {
            "team_id": team_id,
            "assignee_user_id": assignee_user_id,
            "active_agent_key": active_agent_key,
            "ai_active": ai_active,
            "updated_at": now,
        }
        _write_conversation(conn, conversation_id, values)
        after = RoutingSnapshot(
            team_id=team_id,
            assignee_user_id=assignee_user_id,
            active_agent_key=active_agent_key,
            ai_active=bool(ai_active),
        )

    return TeamRoutingResult(
        conversation_id=conversation_id,
        inbox_id=inbox_id,
        team_id=team_id,
        strategy=mode,
        before=before,
        after=after,
        fallback_reason=fallback_reason,
    )


def routing_state(team_id: int, inbox_id: int) -> dict | None:
    """Read-only diagnostic used by tests and future administration surfaces."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(team_routing_state).where(
                team_routing_state.c.team_id == int(team_id),
                team_routing_state.c.inbox_id == int(inbox_id),
            )
        ).mappings().first()
    return dict(row) if row else None

