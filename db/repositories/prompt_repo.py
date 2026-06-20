"""Repository for ``ai_prompts`` (prompt templates, config-in-DB).

Each row is a template ``body`` with ``{placeholder}`` slots resolved from
``ai_variables`` at render time. Every ``save`` bumps ``version`` and snapshots
to ``ai_prompts_history``.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select

from db.engine import get_engine
from db.tables import ai_prompts, ai_prompts_history
from db.upsert import upsert, upsert_ignore


def get(prompt_key: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_prompts).where(ai_prompts.c.prompt_key == prompt_key)
        ).mappings().first()
    return dict(row) if row else None


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_prompts).order_by(ai_prompts.c.prompt_key)
        ).mappings().all()
    return [dict(r) for r in rows]


def ensure(prompt_key: str, body: str = "") -> None:
    """Insert the prompt only if it does not exist yet (no version bump)."""
    now = time.time()
    with get_engine().begin() as conn:
        conn.execute(upsert_ignore(
            ai_prompts,
            {"prompt_key": prompt_key, "body": body, "version": 1, "updated_at": now},
            conflict_cols=["prompt_key"],
        ))


def save(prompt_key: str, body: str) -> dict:
    """Upsert a prompt, bump version and snapshot to history. Returns the row."""
    now = time.time()
    existing = get(prompt_key)
    version = (existing["version"] + 1) if existing else 1
    values = {
        "prompt_key": prompt_key,
        "body": body,
        "version": version,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(upsert(
            ai_prompts, values, conflict_cols=["prompt_key"],
            update_cols=["body", "version", "updated_at"],
        ))
        conn.execute(ai_prompts_history.insert().values(
            prompt_key=prompt_key,
            version=version,
            snapshot=json.dumps(values, ensure_ascii=False),
            created_at=now,
        ))
    return values


def list_history(prompt_key: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_prompts_history.c.version, ai_prompts_history.c.created_at)
            .where(ai_prompts_history.c.prompt_key == prompt_key)
            .order_by(ai_prompts_history.c.version.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_snapshot(prompt_key: str, version: int) -> dict | None:
    with get_engine().connect() as conn:
        snap = conn.execute(
            select(ai_prompts_history.c.snapshot).where(
                ai_prompts_history.c.prompt_key == prompt_key,
                ai_prompts_history.c.version == version)
        ).scalar()
    if not snap:
        return None
    try:
        return json.loads(snap)
    except (json.JSONDecodeError, TypeError):
        return None


def rollback(prompt_key: str, version: int) -> dict | None:
    snap = get_snapshot(prompt_key, version)
    if not snap:
        return None
    return save(prompt_key, snap.get("body", ""))
