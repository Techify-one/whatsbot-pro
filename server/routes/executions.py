"""Execution tracking endpoints."""

import asyncio
from datetime import datetime, timezone

from fastapi import Request

from db.repositories import execution_repo
from server.authz import permission_denied
from server.helpers import _ok, _err


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
    ):
        """List executions with pagination + filters (phone/status/conversation/period)."""
        denied = permission_denied(request, "execution.read")
        if denied:
            return denied
        df = _parse_date(date_from)
        dt = _parse_date(date_to, end_of_day=True)
        items = await asyncio.to_thread(
            execution_repo.list_executions, limit, offset, phone, status,
            conversation_id, df, dt,
        )
        total = await asyncio.to_thread(
            execution_repo.count, phone, status, conversation_id, df, dt,
        )
        return _ok({"items": items, "total": total})

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
