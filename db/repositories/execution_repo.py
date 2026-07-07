"""Repository for execution tracking tables."""

from __future__ import annotations

import json
import time

from sqlalchemy import and_, delete as sa_delete, func, insert as sa_insert, select, update as sa_update

from db.engine import get_engine
from db.tables import execution_steps, executions


def create(phone: str, trigger_type: str = "webhook", *,
           conversation_id: int | None = None,
           channel_id: str | None = None,
           channel_label: str | None = None) -> int:
    """Create a new execution and return its ID.

    ``conversation_id``/``channel_id``/``channel_label`` (plano 36) identificam a
    conversa+canal do turno; costumam ser resolvidos só depois de materializar o
    contato, então normalmente entram via ``set_channel`` — os kwargs aqui existem
    para call sites que já os conhecem (e para testes). Todos opcionais/retrocompat.
    """
    values = dict(phone=phone, trigger_type=trigger_type, started_at=time.time())
    if conversation_id is not None:
        values["conversation_id"] = conversation_id
    if channel_id is not None:
        values["channel_id"] = channel_id
    if channel_label is not None:
        values["channel_label"] = channel_label
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(executions).values(**values))
        return result.inserted_primary_key[0]


def set_channel(execution_id: int, conversation_id: int | None = None,
                channel_id: str | None = None,
                channel_label: str | None = None) -> None:
    """Stamp the conversation + channel onto an already-created execution (plano 36).

    Best-effort: only the non-None fields are written, so a failed conversation
    lookup still lets the channel through (and vice-versa).
    """
    values = {}
    if conversation_id is not None:
        values["conversation_id"] = conversation_id
    if channel_id is not None:
        values["channel_id"] = channel_id
    if channel_label is not None:
        values["channel_label"] = channel_label
    if not values:
        return
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == execution_id).values(**values))


def add_step(execution_id: int, step_type: str,
             data: dict | None = None, status: str = "ok",
             agent_key: str | None = None) -> int:
    """Add a step to an execution and return step ID.

    ``agent_key`` (config-in-DB / within-turn routing) records which agent ran the
    step, so a multi-agent turn shows each hop's steps attributed correctly.
    """
    data_json = json.dumps(data, ensure_ascii=False) if data else None
    values = dict(
        execution_id=execution_id,
        step_type=step_type,
        status=status,
        data=data_json,
        ts=time.time(),
    )
    if agent_key:
        values["agent_key"] = agent_key
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(execution_steps).values(**values))
        return result.inserted_primary_key[0]


def complete(execution_id: int, status: str = "completed",
             error: str | None = None) -> None:
    """Mark an execution as completed or failed."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == execution_id).values(
            status=status,
            completed_at=time.time(),
            error=error,
        ))


def add_usage(execution_id: int, total_tokens: int = 0, cost_usd: float = 0.0) -> None:
    """Accumulate token/cost totals onto an execution.

    Called once per billable LLM call (main reply, audio, image, document), so
    an execution that does several calls sums them. No-op when both deltas are
    zero. Populates the ``executions.total_tokens``/``total_cost_usd`` columns
    that were created in 0007 but previously never written.
    """
    if not total_tokens and not cost_usd:
        return
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == execution_id).values(
            total_tokens=func.coalesce(executions.c.total_tokens, 0) + total_tokens,
            total_cost_usd=func.coalesce(executions.c.total_cost_usd, 0.0) + cost_usd,
        ))


def set_agent_key(execution_id: int, agent_key: str) -> None:
    """Record which AI agent (config-in-DB) handled this execution."""
    if not agent_key:
        return
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == execution_id).values(
            agent_key=agent_key,
        ))


def set_routing_steps(execution_id: int, routing_steps: list | None) -> None:
    """Record the within-turn handoff chain (plano 06) as JSON on the execution."""
    if not routing_steps:
        return
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == execution_id).values(
            routing_steps=json.dumps(routing_steps, ensure_ascii=False),
        ))


def get_by_id(execution_id: int) -> dict | None:
    """Return an execution with all its steps."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(executions).where(executions.c.id == execution_id)
        ).mappings().first()
        if not row:
            return None
        execution = dict(row)
        step_rows = conn.execute(
            select(execution_steps)
            .where(execution_steps.c.execution_id == execution_id)
            .order_by(execution_steps.c.ts)
        ).mappings().all()
    execution["steps"] = []
    for s in step_rows:
        step = dict(s)
        if step.get("data"):
            try:
                step["data"] = json.loads(step["data"])
            except (json.JSONDecodeError, TypeError):
                pass
        execution["steps"].append(step)
    return execution


def _exec_filters(phone: str | None, status: str | None,
                  conversation_id: int | None,
                  date_from: float | None, date_to: float | None) -> list:
    """Shared WHERE clauses for list_executions/count (plano 36 F4)."""
    clauses = []
    if phone:
        clauses.append(executions.c.phone == phone)
    if status:
        clauses.append(executions.c.status == status)
    if conversation_id is not None:
        clauses.append(executions.c.conversation_id == conversation_id)
    if date_from is not None:
        clauses.append(executions.c.started_at >= date_from)
    if date_to is not None:
        clauses.append(executions.c.started_at < date_to)
    return clauses


def list_executions(limit: int = 50, offset: int = 0,
                    phone: str | None = None,
                    status: str | None = None,
                    conversation_id: int | None = None,
                    date_from: float | None = None,
                    date_to: float | None = None) -> list[dict]:
    """List executions (newest first) with step count and duration."""
    step_count = (
        select(func.count())
        .where(execution_steps.c.execution_id == executions.c.id)
        .correlate(executions)
        .scalar_subquery()
        .label("step_count")
    )
    stmt = (
        select(executions, step_count)
        .order_by(executions.c.id.desc())
        .limit(limit)
        .offset(offset)
    )
    where_clauses = _exec_filters(phone, status, conversation_id, date_from, date_to)
    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))

    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()

    results = []
    for r in rows:
        d = dict(r)
        if d.get("started_at") and d.get("completed_at"):
            d["duration_ms"] = round((d["completed_at"] - d["started_at"]) * 1000)
        else:
            d["duration_ms"] = None
        results.append(d)
    return results


def count(phone: str | None = None, status: str | None = None,
          conversation_id: int | None = None,
          date_from: float | None = None, date_to: float | None = None) -> int:
    """Count total executions for pagination (honours the same filters as list)."""
    stmt = select(func.count()).select_from(executions)
    where_clauses = _exec_filters(phone, status, conversation_id, date_from, date_to)
    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))
    with get_engine().connect() as conn:
        return conn.execute(stmt).scalar() or 0


def prune(max_keep: int) -> int:
    """Delete oldest executions keeping only the most recent ``max_keep``."""
    with get_engine().begin() as conn:
        total = conn.execute(select(func.count()).select_from(executions)).scalar() or 0
        if total <= max_keep:
            return 0
        keep_ids = conn.execute(
            select(executions.c.id).order_by(executions.c.id.desc()).limit(max_keep)
        ).scalars().all()
        result = conn.execute(sa_delete(executions).where(executions.c.id.notin_(keep_ids)))
    return result.rowcount or 0


def delete_older_than(cutoff_ts: float) -> int:
    """Delete executions whose ``started_at`` is before ``cutoff_ts``."""
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(executions).where(executions.c.started_at < cutoff_ts))
    return result.rowcount or 0


def get_webhook_payloads(limit: int = 50) -> list[dict]:
    """Get recent webhook payloads from execution steps (replaces in-memory deque)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(execution_steps.c.ts, execution_steps.c.data, executions.c.phone)
            .join(executions, executions.c.id == execution_steps.c.execution_id)
            .where(execution_steps.c.step_type == "webhook_received")
            .order_by(execution_steps.c.ts.desc())
            .limit(limit)
        ).mappings().all()

    results = []
    for r in rows:
        entry = {"ts": r["ts"], "phone": r["phone"]}
        if r["data"]:
            try:
                entry["payload"] = json.loads(r["data"])
            except (json.JSONDecodeError, TypeError):
                entry["payload"] = r["data"]
        else:
            entry["payload"] = {}
        results.append(entry)
    results.reverse()
    return results
