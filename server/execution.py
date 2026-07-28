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
import time

from agent.execution import (  # noqa: F401 — re-export
    set_current_execution,
    set_current_contact_id,
    get_current_contact_id,
    create_execution,
    complete_execution,
    track_step,
    set_execution_channel,
    set_execution_texts,
    mark_execution_has_ai,
    get_current_execution_id,
    prune_executions,
)
from plugins.events import emit_with_filter

# Module-level cache: exec_id -> {"phone", "trigger_type", "started_at"}
# Used to compute duration_ms and route phone/trigger into the ``ended``
# event without an extra DB hit.
_active: dict[int, dict] = {}


async def astart_execution(phone: str, trigger_type: str = "webhook") -> int:
    """Create execution in DB (via to_thread) and set contextvar in async context."""
    exec_id = await asyncio.to_thread(create_execution, phone, trigger_type)
    # Set contextvar HERE in the async context — this is inherited by to_thread calls
    set_current_execution(exec_id)
    started_at = time.time()
    _active[exec_id] = {
        "phone": phone, "trigger_type": trigger_type,
        "started_at": started_at,
    }
    await emit_with_filter("execution.started", {
        "exec_id": exec_id, "phone": phone,
        "trigger_type": trigger_type, "ts": started_at,
    })
    return exec_id


async def aend_execution(exec_id: int, error: str | None = None) -> None:
    """Finalize execution in DB and clear the contextvar."""
    if error:
        await asyncio.to_thread(complete_execution, exec_id, "failed", error)
    else:
        await asyncio.to_thread(complete_execution, exec_id, "completed")
    set_current_execution(None)
    meta = _active.pop(exec_id, None)
    if meta is not None:
        await emit_with_filter("execution.ended", {
            "exec_id": exec_id,
            "phone": meta.get("phone"),
            "trigger_type": meta.get("trigger_type"),
            "error": error,
            "duration_ms": int((time.time() - meta["started_at"]) * 1000),
            "ts": time.time(),
        })


async def atrack_step(step_type: str, data: dict | None = None, status: str = "ok") -> None:
    """Async wrapper — delegates to track_step via to_thread."""
    await asyncio.to_thread(track_step, step_type, data, status)


async def aset_execution_texts(*, input_text: str | None = None,
                               output_text: str | None = None,
                               msg_id: str | None = None) -> None:
    """Async wrapper — stamp the denormalized search columns onto the execution."""
    await asyncio.to_thread(
        set_execution_texts, input_text=input_text,
        output_text=output_text, msg_id=msg_id,
    )


async def amark_execution_has_ai() -> None:
    """Async wrapper — flag the current execution as AI-invoking (has_ai=1)."""
    await asyncio.to_thread(mark_execution_has_ai)


async def aset_execution_channel(conversation_id: int | None = None,
                                 channel_id: str | None = None,
                                 channel_label: str | None = None) -> None:
    """Async wrapper — stamp conversation + channel onto the current execution."""
    await asyncio.to_thread(
        set_execution_channel, conversation_id, channel_id, channel_label,
    )


async def astamp_execution_channel(contact, channel_id: str, *,
                                   channel_label: str | None = None) -> None:
    """Resolve the open conversation for this contact/inbox + a channel label and
    stamp both onto the current execution (plano 36).

    Fully best-effort: any lookup failure degrades to NULL/slug and never raises
    into the turn. ``channel_label`` may be forced (e.g. ``"Sandbox"``); when None
    it is derived from the channel row (``display_name``/``provider``).
    """
    from db.repositories import channel_repo, conversation_repo
    try:
        conv_id = None
        try:
            conv = await asyncio.to_thread(
                conversation_repo.get_open_for_contact_inbox,
                contact.id, contact.inbox_id,
            )
            conv_id = conv["id"] if conv else None
        except Exception:
            pass
        label = channel_label
        if label is None:
            try:
                ch = await asyncio.to_thread(channel_repo.get, channel_id)
                label = (ch.get("display_name") or ch.get("provider")) if ch else None
            except Exception:
                label = None
            label = label or channel_id
        await aset_execution_channel(
            conversation_id=conv_id, channel_id=channel_id, channel_label=label,
        )
    except Exception:
        pass
