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


def set_current_execution(exec_id: int | None) -> None:
    """Set the current execution ID in the contextvar (call from async context)."""
    _current_execution.set(exec_id)


def create_execution(phone: str, trigger_type: str = "webhook") -> int:
    """Create an execution row in the DB. Returns execution_id.

    This is a sync DB call — use via asyncio.to_thread() from async code.
    Does NOT set the contextvar (that must be done in the async context).
    """
    return execution_repo.create(phone, trigger_type)


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
        execution_repo.add_step(exec_id, step_type, data, status)
    except Exception as e:
        logger.warning("Failed to track step %s: %s", step_type, e)


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
