"""Execution tracking endpoints."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import Request

from db.repositories import execution_repo, usage_repo
from server.authz import permission_denied
from server.helpers import _ok, _err
from server.pagination import CAP_LIST, PAGE_LIST, clamp_limit, clamp_offset


def _period_window(period: str | None, date_from: str | None,
                   date_to: str | None) -> tuple[float | None, float | None]:
    """Resolve a ``period`` (day/week/month/year) or explicit dates to a window.

    Explicit ``date_from``/``date_to`` win when given; otherwise ``period`` maps to
    the last N days ending now. Default (no args) → last 24h. Powers the cost panel
    and the stat cards (both default to the same 24h window as the Nexus screen).
    """
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    if df is not None or dt is not None:
        return df, dt
    now = datetime.now(timezone.utc)
    spans = {"day": 1, "week": 7, "month": 30, "year": 365}
    days = spans.get((period or "").lower(), 1)
    return (now - timedelta(days=days)).timestamp(), now.timestamp()


def _parse_date(value: str | None, *, end_of_day: bool = False) -> float | None:
    """Convert a ``YYYY-MM-DD`` (or epoch) query value to an epoch float.

    ``end_of_day`` makes the ``date_to`` bound inclusive (start of the NEXT day,
    used with a strict ``<`` in the repo). Returns None for empty/invalid input so
    a bad date silently drops the filter instead of 500-ing.
    """
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return float(value)  # already an epoch
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt.timestamp() + 0.000001  # strict < next instant → inclusive day
    return dt.timestamp()


def register_routes(app, deps):

    @app.get("/api/executions")
    async def list_executions(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        phone: str | None = None,
        status: str | None = None,
        conversation_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search_input: str | None = None,
        search_output: str | None = None,
        msg_id: str | None = None,
        only_ai: int = 0,
        agent_key: str | None = None,
        channel_id: str | None = None,
    ):
        """List executions with pagination + filters (phone/status/conversation/period
        + Nexus: msg do cliente/IA, ID da mensagem, só-IA, agente, canal)."""
        denied = permission_denied(request, "execution.read")
        if denied:
            return denied
        limit = clamp_limit(limit, PAGE_LIST, CAP_LIST)  # plano 50 F1 — ?limit livre
        offset = clamp_offset(offset)
        df = _parse_date(date_from)
        dt = _parse_date(date_to, end_of_day=True)
        # channel_id chega como CSV (multi-seleção no painel): "a,b,c" → ["a","b","c"].
        channel_ids = (
            [c for c in (s.strip() for s in channel_id.split(",")) if c]
            if channel_id else None
        )
        kw = dict(
            search_input=search_input, search_output=search_output,
            msg_id=msg_id, only_ai=bool(only_ai), agent_key=agent_key,
            channel_ids=channel_ids,
        )
        items = await asyncio.to_thread(
            lambda: execution_repo.list_executions(
                limit, offset, phone, status, conversation_id, df, dt, **kw)
        )
        total = await asyncio.to_thread(
            lambda: execution_repo.count(
                phone, status, conversation_id, df, dt, **kw)
        )
        return _ok({"items": items, "total": total})

    @app.get("/api/executions/stats")
    async def executions_stats(
        request: Request,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        """Stat cards: total/success/error/running, avg duration, tokens (default 24h)."""
        denied = permission_denied(request, "execution.read")
        if denied:
            return denied
        df, dt = _period_window("day", date_from, date_to)
        data = await asyncio.to_thread(execution_repo.stats, df, dt)
        return _ok(data)

    @app.get("/api/executions/cost")
    async def executions_cost(
        request: Request,
        period: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        """Cost panel: LLM cost/tokens (excl. embedding) + a separate embedding block."""
        denied = permission_denied(request, "execution.read")
        if denied:
            return denied
        df, dt = _period_window(period or "day", date_from, date_to)
        summary = await asyncio.to_thread(usage_repo.global_summary, df, dt)
        by_type = summary.get("by_type", {}) or {}
        embedding = by_type.get("embedding") or {
            "cost_usd": 0.0, "total_tokens": 0, "call_count": 0,
        }
        # "Custo" = tudo menos embedding (embedding tem card próprio, estilo Nexus).
        cost_total = 0.0
        cost_tokens = 0
        cost_calls = 0
        for call_type, entry in by_type.items():
            if call_type == "embedding":
                continue
            cost_total += entry.get("cost_usd", 0.0) or 0.0
            cost_tokens += entry.get("total_tokens", 0) or 0
            cost_calls += entry.get("call_count", 0) or 0
        return _ok({
            "period": (period or "day"),
            "cost": {
                "total_cost": cost_total,
                "total_tokens": cost_tokens,
                "execution_count": cost_calls,
            },
            "embedding": {
                "total_cost": embedding.get("cost_usd", 0.0) or 0.0,
                "total_tokens": embedding.get("total_tokens", 0) or 0,
                "execution_count": embedding.get("call_count", 0) or 0,
            },
        })

    @app.get("/api/executions/models")
    async def executions_models(request: Request):
        """Distinct agent keys + channels seen (for the filter pills / model selector)."""
        denied = permission_denied(request, "execution.read")
        if denied:
            return denied
        agents = await asyncio.to_thread(execution_repo.distinct_agent_keys)
        channels = await asyncio.to_thread(execution_repo.distinct_channels)
        return _ok({"agent_keys": agents, "channels": channels})

    @app.get("/api/executions/{execution_id}")
    async def get_execution(execution_id: int, request: Request):
        """Get execution details with all steps."""
        denied = permission_denied(request, "execution.read")
        if denied:
            return denied
        result = await asyncio.to_thread(execution_repo.get_by_id, execution_id)
        if not result:
            return _err("Execução não encontrada.", status=404)
        return _ok(result)

    @app.delete("/api/executions")
    async def cleanup_executions(request: Request, days: int = 30):
        """Delete executions older than N days."""
        denied = permission_denied(request, "execution.delete")
        if denied:
            return denied
        import time
        cutoff = time.time() - (days * 86400)
        deleted = await asyncio.to_thread(execution_repo.delete_older_than, cutoff)
        return _ok({"deleted": deleted})
