"""Usage / cost tracking endpoints."""

import asyncio
import logging
import time

import httpx

from fastapi import Request

from config.settings import LLM_API_BASE_URL
from db.repositories import usage_repo, contact_repo
from server.authz import permission_denied
from server.helpers import _ok
from server.pagination import CAP_LIST, PAGE_LIST, clamp_limit, clamp_offset
from server.routes.config import get_models_cache

logger = logging.getLogger(__name__)


def _get_model_pricing(model_id: str, api_key: str = "") -> tuple[float, float]:
    """Return (prompt_price_per_token, completion_price_per_token) from cache."""
    _models_cache = get_models_cache()
    if not _models_cache["data"]:
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = httpx.get(f"{LLM_API_BASE_URL}/models", headers=headers, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
            models = []
            for m in raw.get("data", []):
                arch = m.get("architecture", {})
                models.append({
                    "id": m.get("id", ""),
                    "name": m.get("name", ""),
                    "input_modalities": arch.get("input_modalities", ["text"]),
                    "pricing": m.get("pricing", {}),
                })
            models.sort(key=lambda x: x["name"].lower())
            _models_cache["data"] = models
            _models_cache["fetched_at"] = time.time()
            logger.info("Models cache populated for pricing (%d models)", len(models))
        except Exception as e:
            logger.warning("Failed to fetch models for pricing: %s", e)
            return 0.0, 0.0
    for m in _models_cache["data"]:
        if m["id"] == model_id:
            p = m.get("pricing", {})
            return float(p.get("prompt", "0") or "0"), float(p.get("completion", "0") or "0")
    return 0.0, 0.0


def _parse_period(period: str | None, start: float | None, end: float | None) -> tuple[float | None, float | None]:
    """Convert period shorthand or explicit timestamps to (start_ts, end_ts)."""
    if start is not None or end is not None:
        return start, end
    if not period:
        return None, None
    now = time.time()
    mapping = {"24h": 86400, "3d": 259200, "7d": 604800, "30d": 2592000}
    seconds = mapping.get(period)
    if seconds:
        return now - seconds, now
    return None, None


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    settings = deps.settings

    # Wire up pricing function — closure captures settings to forward the API key
    # to the upstream proxy (Techify / OpenRouter-compatible), which requires it.
    def pricing_fn(model_id: str) -> tuple[float, float]:
        return _get_model_pricing(model_id, settings.get("openrouter_api_key", ""))

    agent_handler.pricing_fn = pricing_fn

    @app.get("/api/usage/summary")
    async def usage_summary_endpoint(request: Request, period: str | None = None, start: float | None = None, end: float | None = None):
        denied = permission_denied(request, "usage.read")
        if denied:
            return denied
        start_ts, end_ts = _parse_period(period, start, end)
        totals = await asyncio.to_thread(usage_repo.global_summary, start_ts, end_ts)
        totals["period_start"] = start_ts
        totals["period_end"] = end_ts
        return _ok(totals)

    @app.get("/api/usage/by-contact")
    async def usage_by_contact_endpoint(request: Request, period: str | None = None, start: float | None = None, end: float | None = None, limit: int | None = None, offset: int = 0):
        denied = permission_denied(request, "usage.read")
        if denied:
            return denied
        start_ts, end_ts = _parse_period(period, start, end)
        if limit is None:
            # Legado: lista completa (top gastadores, sem teto).
            rows = await asyncio.to_thread(usage_repo.by_contact, start_ts, end_ts)
            return _ok(rows)
        # Paginado (plano 50 F9): top-N por custo + {items, total, has_more}.
        lim, off = clamp_limit(limit, PAGE_LIST, CAP_LIST), clamp_offset(offset)
        items = await asyncio.to_thread(
            usage_repo.by_contact, start_ts, end_ts, limit=lim, offset=off)
        total = await asyncio.to_thread(usage_repo.count_by_contact, start_ts, end_ts)
        return _ok({"items": items, "total": total, "has_more": (off + len(items)) < total})

    @app.get("/api/usage/contact/{phone}")
    async def usage_contact_detail(phone: str, request: Request, period: str | None = None, start: float | None = None, end: float | None = None, limit: int | None = None, offset: int = 0):
        denied = permission_denied(request, "usage.read")
        if denied:
            return denied
        start_ts, end_ts = _parse_period(period, start, end)
        contact = await asyncio.to_thread(contact_repo.get_by_phone, phone)
        if contact is None:
            return _ok([] if limit is None else {"items": [], "total": 0, "has_more": False})
        if limit is None:
            filtered = await asyncio.to_thread(usage_repo.detail, contact["id"], start_ts, end_ts)
            return _ok(filtered)
        lim, off = clamp_limit(limit, PAGE_LIST, CAP_LIST), clamp_offset(offset)
        items = await asyncio.to_thread(
            usage_repo.detail, contact["id"], start_ts, end_ts, limit=lim, offset=off)
        total = await asyncio.to_thread(usage_repo.count_detail, contact["id"], start_ts, end_ts)
        return _ok({"items": items, "total": total, "has_more": (off + len(items)) < total})
