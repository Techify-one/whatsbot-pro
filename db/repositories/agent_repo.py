"""Repository for ``ai_agents`` (config-in-DB agent definitions).

One row per agent; in the single-agent MVP there is exactly one (``default``).
``model_config`` and ``tool_names`` are stored as JSON-encoded TEXT and decoded
on read. Every ``save`` bumps ``version`` and writes a snapshot to
``ai_agents_history`` for rollback / change trail.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select

from db.engine import get_engine
from db.repositories._mapping import coerce_json
from db.tables import ai_agents, ai_agents_history
from db.upsert import upsert, upsert_ignore

DEFAULT_AGENT_KEY = "default"


def _decode_json(value, fallback):
    return coerce_json(value, fallback)


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["model_config"] = coerce_json(d.get("model_config"), {})
    d["tool_names"] = coerce_json(d.get("tool_names"), None)
    d["routing_targets"] = coerce_json(d.get("routing_targets"), None)
    d["hooks_config"] = coerce_json(d.get("hooks_config"), {})
    d["enabled"] = bool(d.get("enabled", 1))
    d["is_router"] = bool(d.get("is_router", 0))
    return d


def get(agent_key: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_agents).where(ai_agents.c.agent_key == agent_key)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def get_default() -> dict | None:
    return get(DEFAULT_AGENT_KEY)


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_agents).order_by(ai_agents.c.agent_key)
        ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def ensure(
    agent_key: str,
    *,
    display_name: str = "",
    prompt: str = "",
    prompt_key: str = "",
    model_config: dict | None = None,
    tool_names: list[str] | None = None,
    enabled: bool = True,
) -> None:
    """Insert the agent only if it does not exist yet (no version bump).

    Used to seed the default agent at boot without clobbering user edits.
    """
    now = time.time()
    values = {
        "agent_key": agent_key,
        "display_name": display_name,
        "prompt": prompt,
        "prompt_key": prompt_key,
        "model_config": json.dumps(model_config or {}, ensure_ascii=False),
        "tool_names": None if tool_names is None else json.dumps(tool_names, ensure_ascii=False),
        "enabled": 1 if enabled else 0,
        "version": 1,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(upsert_ignore(ai_agents, values, conflict_cols=["agent_key"]))


def save(
    agent_key: str,
    *,
    display_name: str,
    prompt: str = "",
    prompt_key: str = "",
    model_config: dict,
    tool_names: list[str] | None,
    enabled: bool,
    description: str = "",
    is_router: bool = False,
    routing_targets: list[str] | None = None,
    hooks_config: dict | None = None,
) -> dict:
    """Upsert an agent, bump version and snapshot to history. Returns the row.

    ``prompt`` is the inline, per-agent system prompt (source of truth).
    ``prompt_key`` is legacy (a reference to a shared ai_prompts template) and is
    no longer read for resolution; it is still accepted/persisted for back-compat.
    """
    now = time.time()
    existing = get(agent_key)
    version = (existing["version"] + 1) if existing else 1
    values = {
        "agent_key": agent_key,
        "display_name": display_name,
        "prompt": prompt or "",
        "prompt_key": prompt_key or "",
        "model_config": json.dumps(model_config or {}, ensure_ascii=False),
        "tool_names": None if tool_names is None else json.dumps(tool_names, ensure_ascii=False),
        "enabled": 1 if enabled else 0,
        "description": description or "",
        "is_router": 1 if is_router else 0,
        "routing_targets": (None if routing_targets is None
                            else json.dumps(routing_targets, ensure_ascii=False)),
        "hooks_config": json.dumps(hooks_config or {}, ensure_ascii=False),
        "version": version,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(upsert(
            ai_agents, values, conflict_cols=["agent_key"],
            update_cols=["display_name", "prompt", "prompt_key", "model_config",
                         "tool_names", "enabled", "description", "is_router",
                         "routing_targets", "hooks_config", "version", "updated_at"],
        ))
        conn.execute(ai_agents_history.insert().values(
            agent_key=agent_key,
            version=version,
            snapshot=json.dumps(values, ensure_ascii=False),
            created_at=now,
        ))
    return _row_to_dict(values)


def list_history(agent_key: str) -> list[dict]:
    """Version trail (newest first), sem o snapshot completo."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_agents_history.c.version, ai_agents_history.c.created_at)
            .where(ai_agents_history.c.agent_key == agent_key)
            .order_by(ai_agents_history.c.version.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_snapshot(agent_key: str, version: int) -> dict | None:
    with get_engine().connect() as conn:
        snap = conn.execute(
            select(ai_agents_history.c.snapshot).where(
                ai_agents_history.c.agent_key == agent_key,
                ai_agents_history.c.version == version)
        ).scalar()
    return _decode_json(snap, None)


def rollback(agent_key: str, version: int) -> dict | None:
    """Re-apply a past snapshot as a NEW version (preserva o trail)."""
    snap = get_snapshot(agent_key, version)
    if not snap:
        return None
    return save(
        agent_key,
        display_name=snap.get("display_name", ""),
        prompt=snap.get("prompt", ""),
        prompt_key=snap.get("prompt_key", ""),
        model_config=_decode_json(snap.get("model_config"), {}),
        tool_names=_decode_json(snap.get("tool_names"), None),
        enabled=bool(snap.get("enabled", 1)),
        description=snap.get("description", ""),
        is_router=bool(snap.get("is_router", 0)),
        routing_targets=_decode_json(snap.get("routing_targets"), None),
        hooks_config=_decode_json(snap.get("hooks_config"), {}),
    )


def delete(agent_key: str) -> bool:
    """Remove an agent and its version history. Returns ``True`` if a row went.

    The ``default`` agent is the engine fallback (``_resolve_active_agent``) and
    must never be removed — refuse defensively even though the route guards too.
    Conversations/inboxes still pointing at the deleted key degrade gracefully to
    the default; ``executions.agent_key`` is historical and intentionally left
    untouched (no FK), so usage history survives.
    """
    if agent_key == DEFAULT_AGENT_KEY:
        return False
    with get_engine().begin() as conn:
        conn.execute(
            ai_agents_history.delete().where(ai_agents_history.c.agent_key == agent_key)
        )
        result = conn.execute(
            ai_agents.delete().where(ai_agents.c.agent_key == agent_key)
        )
    return (result.rowcount or 0) > 0
