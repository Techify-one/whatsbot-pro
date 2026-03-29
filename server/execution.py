"""Execution tracking — async helpers and re-exports.

Core logic lives in agent/execution.py to avoid circular imports.
This module adds async wrappers for use in server routes.

Usage pattern in async code (webhook.py, sandbox.py):
    exec_id = await astart_execution(phone, "webhook")
    try:
        await atrack_step("webhook_received", {...})
        ...  # processing with asyncio.to_thread() calls
        await aend_execution(exec_id)
    except Exception as e:
        await aend_execution(exec_id, error=str(e))
"""

import asyncio

from agent.execution import (  # noqa: F401 — re-export
    set_current_execution,
    create_execution,
    complete_execution,
    track_step,
    get_current_execution_id,
    prune_executions,
)


async def astart_execution(phone: str, trigger_type: str = "webhook") -> int:
    """Create execution in DB (via to_thread) and set contextvar in async context."""
    exec_id = await asyncio.to_thread(create_execution, phone, trigger_type)
    # Set contextvar HERE in the async context — this is inherited by to_thread calls
    set_current_execution(exec_id)
    return exec_id


async def aend_execution(exec_id: int, error: str | None = None) -> None:
    """Finalize execution in DB and clear the contextvar."""
    if error:
        await asyncio.to_thread(complete_execution, exec_id, "failed", error)
    else:
        await asyncio.to_thread(complete_execution, exec_id, "completed")
    set_current_execution(None)


async def atrack_step(step_type: str, data: dict | None = None, status: str = "ok") -> None:
    """Async wrapper — delegates to track_step via to_thread."""
    await asyncio.to_thread(track_step, step_type, data, status)
