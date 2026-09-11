"""Repository for teams (plano 153) — CRUD simples (nome + descrição).

Sem bloqueio de exclusão: a FK ``atendimentos.team_id`` é ``ON DELETE SET NULL``
de propósito (D4) — apagar um time só limpa a etiqueta das conversas.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy import insert, select

from db.engine import get_engine
from db.tables import teams


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(teams).order_by(teams.c.name)).mappings().all()
    return [dict(r) for r in rows]


def get(team_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(teams).where(teams.c.id == team_id)).mappings().first()
    return dict(row) if row else None


def create(name: str, description: str = "", restrict_visibility: bool = False,
          visible_to_assignee: bool = False) -> dict:
    now = time.time()
    with get_engine().begin() as conn:
        result = conn.execute(insert(teams).values(
            name=name, description=description,
            restrict_visibility=1 if restrict_visibility else 0,
            visible_to_assignee=1 if visible_to_assignee else 0,
            created_at=now, updated_at=now))
        team_id = result.inserted_primary_key[0]
    return get(team_id)


def update(team_id: int, *, name: str | None = None,
          description: str | None = None,
          restrict_visibility: bool | None = None,
          visible_to_assignee: bool | None = None) -> dict | None:
    values = {}
    if name is not None:
        values["name"] = name
    if description is not None:
        values["description"] = description
    if restrict_visibility is not None:
        values["restrict_visibility"] = 1 if restrict_visibility else 0
    if visible_to_assignee is not None:
        values["visible_to_assignee"] = 1 if visible_to_assignee else 0
    if values:
        values["updated_at"] = time.time()
        with get_engine().begin() as conn:
            conn.execute(sa_update(teams).where(teams.c.id == team_id).values(**values))
    return get(team_id)


def delete(team_id: int) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(teams).where(teams.c.id == team_id))
    return (result.rowcount or 0) > 0
