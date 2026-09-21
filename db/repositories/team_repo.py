"""Repository for teams, including atomic membership and safe lifecycle."""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy import func, insert, select

from db.engine import get_engine
from db.tables import conversations, team_members, teams, users


class TeamNameConflict(ValueError):
    pass


class TeamHasConversations(ValueError):
    def __init__(self, count: int):
        self.count = count
        super().__init__(f"O time possui {count} conversa(s) vinculada(s).")


def _serialize(row) -> dict | None:
    if not row:
        return None
    result = dict(row)
    if result.get("enforce_team_access"):
        result["access_mode"] = "private"
    elif result.get("restrict_visibility"):
        result["access_mode"] = "list_hidden"
    else:
        result["access_mode"] = "open"
    return result


def _member_ids(conn, team_id: int) -> list[int]:
    return [r[0] for r in conn.execute(
        select(team_members.c.user_id)
        .where(team_members.c.team_id == team_id)
        .order_by(team_members.c.user_id))]


def _replace_members(conn, team_id: int, user_ids: list[int]) -> list[int]:
    wanted = list(dict.fromkeys(int(user_id) for user_id in user_ids))
    if wanted:
        valid = {r[0] for r in conn.execute(
            select(users.c.id)
            .where(users.c.id.in_(wanted))
            .where(users.c.is_active == 1))}
        invalid = [user_id for user_id in wanted if user_id not in valid]
        if invalid:
            raise ValueError(
                "Usuário(s) inexistente(s) ou inativo(s) na lista de membros: "
                + ", ".join(str(user_id) for user_id in invalid))
    conn.execute(sa_delete(team_members).where(team_members.c.team_id == team_id))
    now = time.time()
    if wanted:
        conn.execute(insert(team_members), [
            {"team_id": team_id, "user_id": user_id, "created_at": now}
            for user_id in wanted
        ])
    return wanted


def _ensure_unique_name(conn, name: str, *, exclude_id: int | None = None) -> None:
    stmt = select(teams.c.id).where(
        func.lower(func.btrim(teams.c.name)) == name.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(teams.c.id != exclude_id)
    if conn.execute(stmt.limit(1)).first():
        raise TeamNameConflict("Já existe um time com esse nome.")


def list_all(*, include_inactive: bool = True) -> list[dict]:
    with get_engine().connect() as conn:
        stmt = select(teams)
        if not include_inactive:
            stmt = stmt.where(teams.c.is_active == 1)
        rows = conn.execute(stmt.order_by(teams.c.name)).mappings().all()
    return [_serialize(r) for r in rows]


def get(team_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(teams).where(teams.c.id == team_id)).mappings().first()
    return _serialize(row)


def create(name: str, description: str = "", restrict_visibility: bool = False,
          visible_to_assignee: bool = False, enforce_team_access: bool = False,
          member_user_ids: list[int] | None = None) -> dict:
    if enforce_team_access:
        restrict_visibility = True
    now = time.time()
    with get_engine().begin() as conn:
        _ensure_unique_name(conn, name)
        result = conn.execute(insert(teams).values(
            name=name, description=description,
            restrict_visibility=1 if restrict_visibility else 0,
            visible_to_assignee=1 if visible_to_assignee else 0,
            enforce_team_access=1 if enforce_team_access else 0,
            is_active=1,
            created_at=now, updated_at=now))
        team_id = result.inserted_primary_key[0]
        members = _replace_members(conn, team_id, member_user_ids or [])
        if enforce_team_access and not members:
            raise ValueError("Um time privado precisa ter pelo menos um membro.")
        row = conn.execute(select(teams).where(teams.c.id == team_id)).mappings().first()
        result = _serialize(row)
        result["member_user_ids"] = members
        return result


def update(team_id: int, *, name: str | None = None,
          description: str | None = None,
          restrict_visibility: bool | None = None,
          visible_to_assignee: bool | None = None,
          enforce_team_access: bool | None = None,
          is_active: bool | None = None,
          member_user_ids: list[int] | None = None,
          confirm_access_expansion: bool = False) -> dict | None:
    with get_engine().begin() as conn:
        current = conn.execute(
            select(teams).where(teams.c.id == team_id).with_for_update()
        ).mappings().first()
        if not current:
            return None
        values = {}
        if name is not None:
            _ensure_unique_name(conn, name, exclude_id=team_id)
            values["name"] = name
        if description is not None:
            values["description"] = description
        final_enforce = (bool(enforce_team_access) if enforce_team_access is not None
                         else bool(current["enforce_team_access"]))
        final_restrict = (bool(restrict_visibility) if restrict_visibility is not None
                          else bool(current["restrict_visibility"]))
        if (current["enforce_team_access"] and not final_enforce
                and not confirm_access_expansion):
            raise ValueError(
                "Confirme a ampliação de acesso ao retirar o modo privado.")
        if final_enforce:
            final_restrict = True
        if restrict_visibility is not None or enforce_team_access is not None:
            values["restrict_visibility"] = 1 if final_restrict else 0
        if visible_to_assignee is not None:
            values["visible_to_assignee"] = 1 if visible_to_assignee else 0
        if enforce_team_access is not None:
            values["enforce_team_access"] = 1 if enforce_team_access else 0
        if is_active is not None:
            values["is_active"] = 1 if is_active else 0
        members = (_replace_members(conn, team_id, member_user_ids)
                   if member_user_ids is not None else _member_ids(conn, team_id))
        final_active = (bool(is_active) if is_active is not None
                        else bool(current["is_active"]))
        if final_enforce and final_active and not members:
            raise ValueError("Um time privado precisa ter pelo menos um membro.")
        final_visible = (bool(visible_to_assignee) if visible_to_assignee is not None
                         else bool(current["visible_to_assignee"]))
        if final_enforce and final_active and not final_visible:
            external_assignee = conn.execute(
                select(conversations.c.assignee_user_id)
                .where(conversations.c.team_id == team_id)
                .where(conversations.c.assignee_user_id.is_not(None))
                .where(conversations.c.assignee_user_id.notin_(members))
                .limit(1)
            ).scalar()
            if external_assignee is not None:
                raise ValueError(
                    "Há conversa atribuída a atendente fora do time. "
                    "Inclua o atendente, remova a atribuição ou habilite a exceção.")
        if values:
            values["updated_at"] = time.time()
            conn.execute(sa_update(teams).where(teams.c.id == team_id).values(**values))
        row = conn.execute(select(teams).where(teams.c.id == team_id)).mappings().first()
        result = _serialize(row)
        result["member_user_ids"] = members
        return result


def conversation_count(team_id: int) -> int:
    with get_engine().connect() as conn:
        return int(conn.execute(
            select(func.count()).select_from(conversations)
            .where(conversations.c.team_id == team_id)).scalar() or 0)


def private_membership_blockers(user_id: int) -> list[dict]:
    """Active private teams that would lose their last active member."""
    other_members = team_members.alias("other_team_members")
    other_users = users.alias("other_team_users")
    with get_engine().connect() as conn:
        candidates = conn.execute(
            select(teams.c.id, teams.c.name)
            .join(team_members, team_members.c.team_id == teams.c.id)
            .where(team_members.c.user_id == user_id)
            .where(teams.c.is_active == 1)
            .where(teams.c.enforce_team_access == 1)
            .order_by(teams.c.name)
        ).mappings().all()
        blockers = []
        for team in candidates:
            active_others = conn.execute(
                select(func.count())
                .select_from(other_members.join(
                    other_users, other_users.c.id == other_members.c.user_id))
                .where(other_members.c.team_id == team["id"])
                .where(other_members.c.user_id != user_id)
                .where(other_users.c.is_active == 1)
            ).scalar() or 0
            if not active_others:
                blockers.append(dict(team))
        return blockers


def deactivate(team_id: int) -> dict | None:
    return update(team_id, is_active=False)


def delete(team_id: int) -> bool:
    with get_engine().begin() as conn:
        count = int(conn.execute(
            select(func.count()).select_from(conversations)
            .where(conversations.c.team_id == team_id)).scalar() or 0)
        if count:
            raise TeamHasConversations(count)
        result = conn.execute(sa_delete(teams).where(teams.c.id == team_id))
    return (result.rowcount or 0) > 0
