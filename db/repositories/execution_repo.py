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


def set_texts(execution_id: int, *, input_text: str | None = None,
              output_text: str | None = None, msg_id: str | None = None) -> None:
    """Stamp the denormalized search columns onto an execution (best-effort).

    Only the non-None fields are written (mirrors ``set_channel``), so a call that
    only knows the input doesn't clobber a previously-written output. Powers the
    "buscar por mensagem gerada / ID da mensagem" filters without scanning steps.
    """
    values = {}
    if input_text is not None:
        values["input_text"] = input_text
    if output_text is not None:
        values["output_text"] = output_text
    if msg_id is not None:
        values["msg_id"] = msg_id
    if not values:
        return
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == execution_id).values(**values))


def mark_has_ai(execution_id: int) -> None:
    """Flag that this execution actually invoked the model (has an ``llm_*`` step).

    Idempotent; only writes when the flag is still 0 so the "só execuções com IA"
    filter (``has_ai = 1``) reflects reality without a per-call UPDATE storm.
    """
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(executions)
            .where(and_(executions.c.id == execution_id, executions.c.has_ai == 0))
            .values(has_ai=1)
        )


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
                  date_from: float | None, date_to: float | None,
                  *, search_input: str | None = None,
                  search_output: str | None = None,
                  msg_id: str | None = None,
                  only_ai: bool = False,
                  agent_key: str | None = None,
                  channel_ids: list[str] | None = None) -> list:
    """Shared WHERE clauses for list_executions/count (plano 36 F4 + Nexus).

    The Nexus-style filters (``search_input``/``search_output`` ILIKE, ``msg_id``
    equality, ``only_ai`` boolean, ``agent_key`` equality) hit the denormalized
    columns added in migration 0046. ``channel_ids`` filters by the channel(s)
    that handled the turn — a multi-select (``IN``), pills fed by
    ``distinct_channels``.
    """
    clauses = []
    if phone:
        # Busca parcial: o telefone no banco é o número completo
        # (ex. 556492827555); o operador costuma digitar só um trecho.
        clauses.append(executions.c.phone.ilike(f"%{phone}%"))
    if status:
        # Aceita um único status ou uma lista separada por vírgula (multi-select).
        vals = [s for s in status.split(",") if s] if isinstance(status, str) else list(status)
        if len(vals) == 1:
            clauses.append(executions.c.status == vals[0])
        elif vals:
            clauses.append(executions.c.status.in_(vals))
    if conversation_id is not None:
        clauses.append(executions.c.conversation_id == conversation_id)
    if date_from is not None:
        clauses.append(executions.c.started_at >= date_from)
    if date_to is not None:
        clauses.append(executions.c.started_at < date_to)
    if search_input:
        clauses.append(executions.c.input_text.ilike(f"%{search_input}%"))
    if search_output:
        clauses.append(executions.c.output_text.ilike(f"%{search_output}%"))
    if msg_id:
        clauses.append(executions.c.msg_id == msg_id)
    if only_ai:
        clauses.append(executions.c.has_ai == 1)
    if agent_key:
        # Aceita um único agent_key ou uma lista separada por vírgula (multi-select).
        keys = [k for k in agent_key.split(",") if k] if isinstance(agent_key, str) else list(agent_key)
        if len(keys) == 1:
            clauses.append(executions.c.agent_key == keys[0])
        elif keys:
            clauses.append(executions.c.agent_key.in_(keys))
    if channel_ids:
        clauses.append(executions.c.channel_id.in_(channel_ids))
    return clauses


def list_executions(limit: int = 50, offset: int = 0,
                    phone: str | None = None,
                    status: str | None = None,
                    conversation_id: int | None = None,
                    date_from: float | None = None,
                    date_to: float | None = None,
                    *, search_input: str | None = None,
                    search_output: str | None = None,
                    msg_id: str | None = None,
                    only_ai: bool = False,
                    agent_key: str | None = None,
                    channel_ids: list[str] | None = None) -> list[dict]:
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
    where_clauses = _exec_filters(
        phone, status, conversation_id, date_from, date_to,
        search_input=search_input, search_output=search_output,
        msg_id=msg_id, only_ai=only_ai, agent_key=agent_key,
        channel_ids=channel_ids,
    )
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
          date_from: float | None = None, date_to: float | None = None,
          *, search_input: str | None = None,
          search_output: str | None = None,
          msg_id: str | None = None,
          only_ai: bool = False,
          agent_key: str | None = None,
          channel_ids: list[str] | None = None) -> int:
    """Count total executions for pagination (honours the same filters as list)."""
    stmt = select(func.count()).select_from(executions)
    where_clauses = _exec_filters(
        phone, status, conversation_id, date_from, date_to,
        search_input=search_input, search_output=search_output,
        msg_id=msg_id, only_ai=only_ai, agent_key=agent_key,
        channel_ids=channel_ids,
    )
    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))
    with get_engine().connect() as conn:
        return conn.execute(stmt).scalar() or 0


def stats(date_from: float | None = None, date_to: float | None = None) -> dict:
    """Aggregate counters for the Nexus-style stat cards over a time window.

    Mirrors the Nexus ``stats`` shape: total/success/error/running counts, average
    duration of completed runs, and summed tokens. One query using
    ``COUNT(*) FILTER (WHERE ...)`` — cheap even over the full table.
    """
    duration_s = executions.c.completed_at - executions.c.started_at
    stmt = select(
        func.count().label("total_count"),
        func.count().filter(executions.c.status == "completed").label("success_count"),
        func.count().filter(executions.c.status == "failed").label("error_count"),
        func.count().filter(executions.c.status == "running").label("running_count"),
        func.coalesce(
            func.avg(duration_s).filter(executions.c.status == "completed"), 0.0
        ).label("avg_duration_s"),
        func.coalesce(func.sum(executions.c.total_tokens), 0).label("total_tokens"),
    )
    clauses = []
    if date_from is not None:
        clauses.append(executions.c.started_at >= date_from)
    if date_to is not None:
        clauses.append(executions.c.started_at < date_to)
    if clauses:
        stmt = stmt.where(and_(*clauses))
    with get_engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return {
        "total_count": row["total_count"] or 0,
        "success_count": row["success_count"] or 0,
        "error_count": row["error_count"] or 0,
        "running_count": row["running_count"] or 0,
        "avg_duration_ms": round((row["avg_duration_s"] or 0.0) * 1000),
        "total_tokens": row["total_tokens"] or 0,
    }


def distinct_agent_keys() -> list[str]:
    """Return the distinct non-null ``agent_key`` values for the filter pills."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(executions.c.agent_key)
            .where(executions.c.agent_key.isnot(None))
            .where(executions.c.agent_key != "")
            .distinct()
            .order_by(executions.c.agent_key)
        ).scalars().all()
    return [r for r in rows if r]


def distinct_channels() -> list[dict]:
    """Return the distinct channels seen in executions (for the filter pills).

    Each entry is ``{channel_id, channel_label}``. Only rows with a non-null
    ``channel_id`` count; the label is the friendly name stamped at execution
    time (falls back to the id when absent). Newest label wins per channel.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(
                executions.c.channel_id,
                func.max(executions.c.channel_label).label("channel_label"),
            )
            .where(executions.c.channel_id.isnot(None))
            .where(executions.c.channel_id != "")
            .group_by(executions.c.channel_id)
            .order_by(func.max(executions.c.channel_label))
        ).mappings().all()
    return [
        {"channel_id": r["channel_id"],
         "channel_label": r["channel_label"] or r["channel_id"]}
        for r in rows
    ]


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
