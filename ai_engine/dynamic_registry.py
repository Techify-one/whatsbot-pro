"""In-memory cache for config-in-DB reads (plano 06 F6).

The config-in-DB path reads ai_agents/ai_prompts/ai_variables on every message.
This cache takes those reads off the hot path: values are cached with a short
TTL and the whole cache is cleared on any ``ai.config.changed`` (wired in
``server/routes/ai_engine.py::_emit_changed``).

P57 — single uvicorn worker in the MVP, so event invalidation in-process + a
short TTL fallback are enough; no LISTEN/NOTIFY. Clearing the entire cache on any
change is intentional: config edits are rare and the cache is tiny, so precise
per-key invalidation buys nothing.

``cached()`` and ``invalidate()`` are pure (injectable loader) and unit-tested;
the typed accessors wrap the repos. ``time.monotonic`` is read indirectly so the
file works without Date.now-style globals.
"""

from __future__ import annotations

import threading
import time as _time

TTL_SECONDS = 60.0

_lock = threading.Lock()
_cache: dict[str, tuple[object, float]] = {}


def _now() -> float:
    return _time.monotonic()


def cached(key: str, loader):
    """Return the cached value for ``key`` or compute+store it (TTL ``TTL_SECONDS``)."""
    now = _now()
    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[1] > now:
            return entry[0]
    value = loader()  # compute outside the lock (may hit the DB)
    with _lock:
        _cache[key] = (value, _now() + TTL_SECONDS)
    return value


def invalidate(*_args, **_kwargs) -> None:
    """Drop the whole cache (called on ai.config.changed). Args ignored by design."""
    with _lock:
        _cache.clear()


def stats() -> dict:
    with _lock:
        return {"entries": len(_cache), "ttl": TTL_SECONDS}


# ── Typed accessors (wrap the repos) ────────────────────────────────────
def get_agent(agent_key: str):
    from db.repositories import agent_repo
    return cached(f"agent:{agent_key}", lambda: agent_repo.get(agent_key))


def get_default_agent():
    from db.repositories import agent_repo
    return cached("agent:__default__", agent_repo.get_default)


def get_prompt(prompt_key: str):
    from db.repositories import prompt_repo
    return cached(f"prompt:{prompt_key}", lambda: prompt_repo.get(prompt_key))


def variables_map() -> dict:
    from db.repositories import variable_repo
    return cached("variables:__map__", variable_repo.as_map)
