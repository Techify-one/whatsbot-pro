"""Usage / cost tracking endpoints."""

import asyncio
import logging
import time

import httpx

from server.helpers import _ok
from server.routes.config import get_models_cache

logger = logging.getLogger(__name__)


def _get_model_pricing(model_id: str) -> tuple[float, float]:
    """Return (prompt_price_per_token, completion_price_per_token) from cache.

    If the cache is empty, fetches models synchronously (runs in to_thread).
    """
    _models_cache = get_models_cache()
    if not _models_cache["data"]:
        try:
            resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=15)
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

    # Wire up pricing function
    agent_handler.pricing_fn = _get_model_pricing

    def _load_all_contacts() -> list:
        """Load all contact files from disk (for usage aggregation)."""
        contacts_dir = agent_handler.memory_dir
        result = []
        if not contacts_dir.exists():
            return result
        for f in contacts_dir.glob("*.json"):
            phone = f.stem
            contact = agent_handler._get_contact(phone)
            result.append(contact)
        return result

    @app.get("/api/usage/summary")
    async def usage_summary(period: str | None = None, start: float | None = None, end: float | None = None):
        start_ts, end_ts = _parse_period(period, start, end)
        contacts = await asyncio.to_thread(_load_all_contacts)
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                  "cost_usd": 0.0, "call_count": 0, "by_type": {}}
        for c in contacts:
            s = c.get_usage_summary(start_ts, end_ts)
            totals["prompt_tokens"] += s["prompt_tokens"]
            totals["completion_tokens"] += s["completion_tokens"]
            totals["total_tokens"] += s["total_tokens"]
            totals["cost_usd"] += s["cost_usd"]
            totals["call_count"] += s["call_count"]
            for ct, bt in s["by_type"].items():
                agg = totals["by_type"].setdefault(ct, {
                    "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "call_count": 0,
                })
                agg["cost_usd"] += bt["cost_usd"]
                agg["prompt_tokens"] += bt["prompt_tokens"]
                agg["completion_tokens"] += bt["completion_tokens"]
                agg["total_tokens"] += bt["total_tokens"]
                agg["call_count"] += bt["call_count"]
        totals["period_start"] = start_ts
        totals["period_end"] = end_ts
        return _ok(totals)

    @app.get("/api/usage/by-contact")
    async def usage_by_contact(period: str | None = None, start: float | None = None, end: float | None = None):
        start_ts, end_ts = _parse_period(period, start, end)
        contacts = await asyncio.to_thread(_load_all_contacts)
        rows = []
        for c in contacts:
            s = c.get_usage_summary(start_ts, end_ts)
            if s["call_count"] == 0:
                continue
            s["phone"] = c.phone
            s["name"] = c.info.get("name", "") or ""
            rows.append(s)
        rows.sort(key=lambda r: r["cost_usd"], reverse=True)
        return _ok(rows)

    @app.get("/api/usage/contact/{phone}")
    async def usage_contact_detail(phone: str, period: str | None = None, start: float | None = None, end: float | None = None):
        start_ts, end_ts = _parse_period(period, start, end)
        contact = agent_handler._get_contact(phone)
        filtered = contact.usage
        if start_ts is not None:
            filtered = [u for u in filtered if u.get("ts", 0) >= start_ts]
        if end_ts is not None:
            filtered = [u for u in filtered if u.get("ts", 0) <= end_ts]
        return _ok(filtered)
