"""Repository for usage (cost tracking) table."""

from __future__ import annotations

import time

from sqlalchemy import and_, false, func, insert as sa_insert, select

from db.engine import get_engine
from db.tables import contacts, usage


def add(contact_id: int, call_type: str, model: str,
        prompt_tokens: int, completion_tokens: int,
        total_tokens: int, cost_usd: float) -> None:
    """Insert a usage record."""
    with get_engine().begin() as conn:
        conn.execute(sa_insert(usage).values(
            contact_id=contact_id,
            call_type=call_type,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            ts=time.time(),
        ))


def _time_clauses(start_ts: float | None, end_ts: float | None) -> list:
    """Build a list of column-based filter expressions for the time range."""
    clauses = []
    if start_ts is not None:
        clauses.append(usage.c.ts >= start_ts)
    if end_ts is not None:
        clauses.append(usage.c.ts <= end_ts)
    return clauses


def _aggregate_columns():
    return [
        func.coalesce(func.sum(usage.c.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(usage.c.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(usage.c.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(usage.c.cost_usd), 0.0).label("cost_usd"),
        func.count().label("call_count"),
    ]


def _by_type_entry(row) -> dict:
    """Shape one aggregated ``call_type`` row into the public ``by_type`` value.

    Single source of the 5-key shape (``cost_usd``/``prompt_tokens``/
    ``completion_tokens``/``total_tokens``/``call_count``) used by every usage
    summary — the order/keys are part of the API contract."""
    return {
        "cost_usd": row["cost_usd"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "call_count": row["call_count"],
    }


def _shape_by_type(rows) -> dict:
    """Build a ``{call_type: entry}`` map from aggregated rows (grouped by call_type)."""
    return {r["call_type"]: _by_type_entry(r) for r in rows}


def summary(contact_id: int, start_ts: float | None = None,
            end_ts: float | None = None) -> dict:
    """Return aggregated usage stats for a single contact."""
    where_clauses = [usage.c.contact_id == contact_id, *_time_clauses(start_ts, end_ts)]
    with get_engine().connect() as conn:
        totals_row = conn.execute(
            select(*_aggregate_columns()).where(and_(*where_clauses))
        ).mappings().first()
        by_type_rows = conn.execute(
            select(usage.c.call_type, *_aggregate_columns())
            .where(and_(*where_clauses))
            .group_by(usage.c.call_type)
        ).mappings().all()

    totals = {
        "prompt_tokens": totals_row["prompt_tokens"],
        "completion_tokens": totals_row["completion_tokens"],
        "total_tokens": totals_row["total_tokens"],
        "cost_usd": totals_row["cost_usd"],
        "call_count": totals_row["call_count"],
        "by_type": _shape_by_type(by_type_rows),
    }
    return totals


def global_summary(start_ts: float | None = None,
                   end_ts: float | None = None) -> dict:
    """Return aggregated usage stats across ALL contacts."""
    time_clauses = _time_clauses(start_ts, end_ts)
    with get_engine().connect() as conn:
        totals_stmt = select(*_aggregate_columns())
        by_type_stmt = (
            select(usage.c.call_type, *_aggregate_columns()).group_by(usage.c.call_type)
        )
        if time_clauses:
            totals_stmt = totals_stmt.where(and_(*time_clauses))
            by_type_stmt = by_type_stmt.where(and_(*time_clauses))
        totals_row = conn.execute(totals_stmt).mappings().first()
        by_type_rows = conn.execute(by_type_stmt).mappings().all()

    totals = {
        "prompt_tokens": totals_row["prompt_tokens"],
        "completion_tokens": totals_row["completion_tokens"],
        "total_tokens": totals_row["total_tokens"],
        "cost_usd": totals_row["cost_usd"],
        "call_count": totals_row["call_count"],
        "by_type": _shape_by_type(by_type_rows),
    }
    return totals


def by_contact(start_ts: float | None = None,
               end_ts: float | None = None, *,
               limit: int | None = None, offset: int = 0) -> list[dict]:
    """Return usage breakdown per contact (for the by-contact endpoint).

    Já ordena por custo desc = "top-N gastadores". ``limit``/``offset`` (plano 50 F9)
    paginam essa lista (top-N + páginas); ``limit=None`` ⇒ tudo (byte-idêntico legado).
    Com paginação, o agregado ``by_type`` é restringido aos contatos DA PÁGINA (não
    reagrega a base inteira)."""
    time_clauses = _time_clauses(start_ts, end_ts)
    agg = _aggregate_columns()
    base_stmt = (
        select(
            usage.c.contact_id,
            contacts.c.phone,
            contacts.c.name,
            *agg,
        )
        .join(contacts, contacts.c.id == usage.c.contact_id)
        .group_by(usage.c.contact_id, contacts.c.phone, contacts.c.name)
        .having(func.count() > 0)
        .order_by(func.coalesce(func.sum(usage.c.cost_usd), 0.0).desc())
    )
    if time_clauses:
        base_stmt = base_stmt.where(and_(*time_clauses))
    if limit is not None:
        base_stmt = base_stmt.limit(limit).offset(offset)

    results: list[dict] = []
    with get_engine().connect() as conn:
        rows = conn.execute(base_stmt).mappings().all()
        # by_type em UMA query (fixes o N+1 por contato). Com paginação, restringe aos
        # contatos da página; sem paginação agrega todos (comportamento legado).
        by_type_stmt = (
            select(usage.c.contact_id, usage.c.call_type, *_aggregate_columns())
            .group_by(usage.c.contact_id, usage.c.call_type)
        )
        if time_clauses:
            by_type_stmt = by_type_stmt.where(and_(*time_clauses))
        if limit is not None:
            page_ids = [row["contact_id"] for row in rows]
            if page_ids:
                by_type_stmt = by_type_stmt.where(usage.c.contact_id.in_(page_ids))
            else:
                by_type_stmt = by_type_stmt.where(false())
        by_type_by_contact: dict = {}
        for r in conn.execute(by_type_stmt).mappings().all():
            by_type_by_contact.setdefault(r["contact_id"], {})[r["call_type"]] = \
                _by_type_entry(r)
        for row in rows:
            results.append({
                "phone": row["phone"],
                "name": row["name"] or "",
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "total_tokens": row["total_tokens"],
                "cost_usd": row["cost_usd"],
                "call_count": row["call_count"],
                "by_type": by_type_by_contact.get(row["contact_id"], {}),
            })
    return results


def count_by_contact(start_ts: float | None = None,
                     end_ts: float | None = None) -> int:
    """Número de contatos com uso na janela (o ``total`` da paginação de :func:`by_contact`)."""
    time_clauses = _time_clauses(start_ts, end_ts)
    grouped = (
        select(usage.c.contact_id)
        .join(contacts, contacts.c.id == usage.c.contact_id)
        .group_by(usage.c.contact_id)
        .having(func.count() > 0)
    )
    if time_clauses:
        grouped = grouped.where(and_(*time_clauses))
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(grouped.subquery())
        ).scalar() or 0


def detail(contact_id: int, start_ts: float | None = None,
           end_ts: float | None = None, *,
           limit: int | None = None, offset: int = 0) -> list[dict]:
    """Return raw usage records for a specific contact.

    ``limit``/``offset`` (plano 50 F9) paginam os registros brutos (crescem por contato);
    ``limit=None`` ⇒ todos (byte-idêntico legado)."""
    where_clauses = [usage.c.contact_id == contact_id, *_time_clauses(start_ts, end_ts)]
    stmt = (
        select(
            usage.c.call_type, usage.c.model, usage.c.prompt_tokens,
            usage.c.completion_tokens, usage.c.total_tokens,
            usage.c.cost_usd, usage.c.ts,
        )
        .where(and_(*where_clauses))
        .order_by(usage.c.ts)
    )
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def count_detail(contact_id: int, start_ts: float | None = None,
                 end_ts: float | None = None) -> int:
    """Número de registros brutos do contato na janela (``total`` de :func:`detail`)."""
    where_clauses = [usage.c.contact_id == contact_id, *_time_clauses(start_ts, end_ts)]
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(usage).where(and_(*where_clauses))
        ).scalar() or 0
