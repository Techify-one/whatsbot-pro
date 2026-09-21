"""Shared classification of terminal AI handoffs for routing and send guards."""

from __future__ import annotations

from contextvars import ContextVar


TEAM_HANDOFF_TERMINAL_FIELD = "team_handoff_terminal"
_team_handoff_outcome: ContextVar[bool | None] = ContextVar(
    "team_handoff_outcome", default=None)


def clear_team_handoff_outcome() -> None:
    """Clear executor-to-engine metadata before dispatching one tool."""
    _team_handoff_outcome.set(None)


def record_team_handoff_outcome(terminal: bool) -> None:
    """Record an internal result without mutating model-provided arguments."""
    _team_handoff_outcome.set(bool(terminal))


def consume_team_handoff_outcome() -> bool | None:
    """Consume the current tool's internal result exactly once."""
    outcome = _team_handoff_outcome.get()
    _team_handoff_outcome.set(None)
    return outcome


def is_terminal_handoff_call(call: dict | None) -> bool:
    """Whether one executed tool call handed the turn to a human/team queue."""
    call = call or {}
    if call.get("skipped"):
        return False
    if call.get("tool") == "transfer_to_human":
        return True
    return (
        call.get("tool") == "transfer_to_team"
        and call.get(TEAM_HANDOFF_TERMINAL_FIELD) is True
    )


def turn_handed_off(tool_calls) -> bool:
    return any(is_terminal_handoff_call(call) for call in (tool_calls or []))
