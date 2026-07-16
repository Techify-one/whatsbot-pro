"""Repository for ``ai_variables`` (global values referenceable by prompts).

Each row is a named value (``{name}`` in a prompt body resolves to ``value``).
Dedicated table rather than a ``config`` prefix, per the implementation
decision in the plan.

plano 51 (01 F3): versionamento no molde de ``tool_repo`` — ``save`` deduplica
(valor idêntico é no-op), bumpa ``version`` e grava snapshot em
``ai_variables_history``; ``rollback`` re-aplica um snapshot antigo como NOVA
versão (forward, não-destrutivo).
"""

from __future__ import annotations

import json
import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from db.engine import get_engine
from db.tables import ai_variables, ai_variables_history
from db.upsert import upsert


def get(name: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_variables).where(ai_variables.c.name == name)
        ).mappings().first()
    return dict(row) if row else None


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_variables).order_by(ai_variables.c.name)
        ).mappings().all()
    return [dict(r) for r in rows]


def as_map() -> dict[str, str]:
    """Return ``{name: value}`` for fast prompt rendering."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_variables.c.name, ai_variables.c.value)
        ).all()
    return {r.name: r.value for r in rows}


def save(name: str, value: str) -> dict:
    """Upsert com dedup + version bump + snapshot (uma transação).

    Valor idêntico ao atual é no-op (sem bump, sem history) — evita
    versões-lixo em saves repetidos.
    """
    now = time.time()
    existing = get(name)
    if existing is not None and (value or "") == (existing.get("value") or ""):
        return existing
    version = (existing.get("version") or 0) + 1 if existing else 1
    values = {"name": name, "value": value, "version": version, "updated_at": now}
    with get_engine().begin() as conn:
        conn.execute(upsert(
            ai_variables, values, conflict_cols=["name"],
            update_cols=["value", "version", "updated_at"],
        ))
        conn.execute(ai_variables_history.insert().values(
            name=name,
            version=version,
            snapshot=json.dumps({"name": name, "value": value},
                                ensure_ascii=False),
            created_at=now,
        ))
    return values


def list_history(name: str) -> list[dict]:
    """Versions of a variable, newest-first (``[{version, created_at}]``)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_variables_history.c.version,
                   ai_variables_history.c.created_at)
            .where(ai_variables_history.c.name == name)
            .order_by(ai_variables_history.c.version.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_snapshot(name: str, version: int) -> dict | None:
    with get_engine().connect() as conn:
        snap = conn.execute(
            select(ai_variables_history.c.snapshot).where(
                ai_variables_history.c.name == name,
                ai_variables_history.c.version == version)
        ).scalar()
    if not snap:
        return None
    try:
        return json.loads(snap)
    except (json.JSONDecodeError, TypeError):
        return None


def rollback(name: str, version: int) -> dict | None:
    """Re-apply a past snapshot as a NEW version (forward, não-destrutivo)."""
    snap = get_snapshot(name, version)
    if snap is None:
        return None
    return save(name, snap.get("value", ""))


def delete(name: str) -> int:
    """Delete the variable AND its history (como ``agent_repo.delete``)."""
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(ai_variables).where(ai_variables.c.name == name))
        conn.execute(sa_delete(ai_variables_history)
                     .where(ai_variables_history.c.name == name))
    return result.rowcount or 0
