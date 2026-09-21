"""Application entrypoints for canonical team routing.

Authorization belongs to the HTTP/tool/default caller. This service resolves
the current global/channel AI gates and delegates the single atomic write to
``team_routing_repo``. It intentionally emits no event or notice; R3 owns those
post-commit side effects.
"""

from __future__ import annotations

import asyncio

from channels import ai_settings
from db.repositories import (
    config_repo,
    conversation_repo,
    inbox_repo,
    team_routing_repo,
)
from domain.team_routing import TeamRoutingResult


def _fixed_ai_gate(conversation_id: int) -> tuple[bool, str | None]:
    """Resolve the same master/channel gates used by the message runtime."""
    if not bool(config_repo.get("auto_reply", True)):
        return False, "global_ai_disabled"
    conv = conversation_repo.get(int(conversation_id))
    if conv is None:
        # The repository will return the canonical identity error. This value is
        # only a conservative fallback should the row disappear between reads.
        return False, "conversation_not_found"
    inbox = inbox_repo.get(int(conv["inbox_id"]))
    if inbox is None or not inbox.get("channel_id"):
        return False, "channel_unavailable"
    if not bool(inbox.get("agent_bot_enabled", 1)):
        return False, "channel_ai_disabled"
    if not bool(ai_settings.value(str(inbox["channel_id"]), "ai_enabled", True)):
        return False, "channel_ai_disabled"
    return True, None


def route_to_team_sync(conversation_id: int, team_id: int) -> TeamRoutingResult:
    """Synchronous adapter for worker-thread tools and non-async jobs."""
    allowed, reason = _fixed_ai_gate(conversation_id)
    return team_routing_repo.route_to_team(
        conversation_id,
        team_id,
        fixed_ai_allowed=allowed,
        fixed_ai_gate_reason=reason,
    )


async def route_to_team(conversation_id: int, team_id: int) -> TeamRoutingResult:
    """Async adapter for routes; all blocking DB work stays off the event loop."""
    return await asyncio.to_thread(route_to_team_sync, conversation_id, team_id)

