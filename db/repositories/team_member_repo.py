"""Repository for team membership (plano 153) — N:N usuário↔time.

Cópia estrutural de ``inbox_member_repo.py``: um usuário pode pertencer a
vários times (requisito #3) e um time pode ter vários membros. Sem efeito de
RBAC/visibilidade (D2) — pertencer a um time não muda quais conversas o
usuário vê.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select

from db.engine import get_engine
from db.tables import team_members, teams, users


def member_ids(team_id: int) -> list[int]:
    """User ids that are members of the team (sorted)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(team_members.c.user_id)
            .where(team_members.c.team_id == team_id)
            .order_by(team_members.c.user_id)
        ).all()
    return [r[0] for r in rows]


def team_ids_for_user(user_id: int) -> list[int]:
    """Team ids the user is a member of."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(team_members.c.team_id)
            .where(team_members.c.user_id == user_id)
        ).all()
    return [r[0] for r in rows]


def team_ids_for_users(user_ids) -> dict[int, list[int]]:
    """Batch sibling of :func:`team_ids_for_user` (plano 168 F3 / I7) — one round
    trip for N ids, scoped with ``IN (...)`` (unlike :func:`member_ids_by_team`,
    which reads every membership row in the table). A requested id with no
    membership is absent from the result (caller treats it as ``[]``)."""
    ids = list({int(u) for u in user_ids if u is not None})
    if not ids:
        return {}
    out: dict[int, list[int]] = {}
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(team_members.c.user_id, team_members.c.team_id)
            .where(team_members.c.user_id.in_(ids))
        ).all()
    for uid, tid in rows:
        out.setdefault(uid, []).append(tid)
    return out


def set_members(team_id: int, user_ids: list[int]) -> list[int]:
    """Replace the team's member set. Only existing users are kept."""
    now = time.time()
    wanted = list(dict.fromkeys(int(u) for u in user_ids))  # dedupe, preserve order
    with get_engine().begin() as conn:
        if wanted:
            valid = {r[0] for r in conn.execute(
                select(users.c.id).where(users.c.id.in_(wanted)))}
            wanted = [u for u in wanted if u in valid]
        conn.execute(sa_delete(team_members).where(team_members.c.team_id == team_id))
        for uid in wanted:
            conn.execute(insert(team_members).values(
                team_id=team_id, user_id=uid, created_at=now))
    return member_ids(team_id)


def set_teams_for_user(user_id: int, team_ids: list[int]) -> list[int]:
    """Replace the set of teams a user belongs to (inverse of ``set_members``)."""
    now = time.time()
    wanted = list(dict.fromkeys(int(t) for t in team_ids))  # dedupe, preserve order
    with get_engine().begin() as conn:
        if wanted:
            valid = {r[0] for r in conn.execute(
                select(teams.c.id).where(teams.c.id.in_(wanted)))}
            wanted = [t for t in wanted if t in valid]
        conn.execute(sa_delete(team_members).where(team_members.c.user_id == user_id))
        for tid in wanted:
            conn.execute(insert(team_members).values(
                team_id=tid, user_id=user_id, created_at=now))
    return team_ids_for_user(user_id)


def member_ids_by_team() -> dict[int, list[int]]:
    """Bulk ``{team_id: [user_id, ...]}`` for all members (feeds the Times screen)."""
    out: dict[int, list[int]] = {}
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(team_members.c.team_id, team_members.c.user_id)
            .order_by(team_members.c.team_id, team_members.c.user_id)
        ).all()
    for tid, uid in rows:
        out.setdefault(tid, []).append(uid)
    return out
