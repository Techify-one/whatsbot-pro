"""Execution tracking core — contextvar and track_step.

This module lives in agent/ to avoid circular imports with server/.
It is re-exported from server/execution.py which adds async helpers.

Context variable design:
- _current_execution holds the execution_id for the current context
- In async code: set via set_current_execution() DIRECTLY (not in to_thread)
- In sync code called via asyncio.to_thread(): the contextvar is automatically
  copied from the parent async context, so track_step() can read it
"""

import contextvars
import logging

from db.repositories import execution_repo

logger = logging.getLogger(__name__)

_current_execution: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_execution", default=None
)
# Which agent (config-in-DB) is running right now — stamped onto each step so a
# within-turn multi-agent turn attributes its steps to the correct hop.
_current_step_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_step_agent", default=None
)


def set_current_execution(exec_id: int | None) -> None:
    """Set the current execution ID in the contextvar (call from async context)."""
    _current_execution.set(exec_id)


def set_current_step_agent(agent_key: str | None) -> None:
    """Set the agent attributed to subsequent steps (within-turn routing)."""
    _current_step_agent.set(agent_key)


def create_execution(phone: str, trigger_type: str = "webhook", *,
                     conversation_id: int | None = None,
                     channel_id: str | None = None,
                     channel_label: str | None = None) -> int:
    """Create an execution row in the DB. Returns execution_id.

    This is a sync DB call — use via asyncio.to_thread() from async code.
    Does NOT set the contextvar (that must be done in the async context).
    ``conversation_id``/``channel_id``/``channel_label`` (plano 36) são opcionais.
    """
    return execution_repo.create(
        phone, trigger_type, conversation_id=conversation_id,
        channel_id=channel_id, channel_label=channel_label,
    )


def set_execution_channel(conversation_id: int | None = None,
                          channel_id: str | None = None,
                          channel_label: str | None = None) -> None:
    """Stamp conversation + channel onto the current execution (plano 36).

    Reads execution_id from the contextvar (inherited inside asyncio.to_thread).
    Best-effort: never raises into the turn.
    """
    exec_id = _current_execution.get()
    if exec_id is None:
        return
    try:
        execution_repo.set_channel(exec_id, conversation_id, channel_id, channel_label)
    except Exception as e:
        logger.warning("Failed to set execution channel: %s", e)


def complete_execution(execution_id: int, status: str = "completed",
                       error: str | None = None) -> None:
    """Finalize an execution in the DB.

    This is a sync DB call — use via asyncio.to_thread() from async code.
    Does NOT clear the contextvar.
    """
    execution_repo.complete(execution_id, status, error)


def track_step(step_type: str, data: dict | None = None, status: str = "ok") -> None:
    """Record a step against the current execution.

    Reads execution_id from the contextvar. When called inside asyncio.to_thread(),
    the contextvar is inherited (copied) from the parent async context.
    Safe to call when no execution is active (silently returns).
    """
    exec_id = _current_execution.get()
    if exec_id is None:
        return
    try:
        execution_repo.add_step(exec_id, step_type, data, status,
                                agent_key=_current_step_agent.get())
    except Exception as e:
        logger.warning("Failed to track step %s: %s", step_type, e)


def add_execution_usage(total_tokens: int = 0, cost_usd: float = 0.0) -> None:
    """Accumulate token/cost totals onto the current execution.

    Reads execution_id from the contextvar (inherited inside asyncio.to_thread).
    Safe to call when no execution is active (silently returns).
    """
    exec_id = _current_execution.get()
    if exec_id is None:
        return
    try:
        execution_repo.add_usage(exec_id, total_tokens, cost_usd)
    except Exception as e:
        logger.warning("Failed to record execution usage: %s", e)


def set_execution_agent_key(agent_key: str) -> None:
    """Record the AI agent_key on the current execution (config-in-DB path)."""
    exec_id = _current_execution.get()
    if exec_id is None or not agent_key:
        return
    try:
        execution_repo.set_agent_key(exec_id, agent_key)
    except Exception as e:
        logger.warning("Failed to record execution agent_key: %s", e)


def set_execution_routing_steps(routing_steps: list | None) -> None:
    """Record the within-turn handoff chain on the current execution (plano 06)."""
    exec_id = _current_execution.get()
    if exec_id is None or not routing_steps:
        return
    try:
        execution_repo.set_routing_steps(exec_id, routing_steps)
    except Exception as e:
        logger.warning("Failed to record routing_steps: %s", e)


def get_current_execution_id() -> int | None:
    """Get the current execution ID (or None if not tracking)."""
    return _current_execution.get()


def prune_executions(max_keep: int) -> None:
    """Remove old executions beyond the configured limit."""
    try:
        deleted = execution_repo.prune(max_keep)
        if deleted:
            logger.info("Pruned %d old executions (keeping %d).", deleted, max_keep)
    except Exception as e:
        logger.warning("Failed to prune executions: %s", e)
