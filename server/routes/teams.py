"""Team CRUD endpoints (plano 153). All gated by ``users.manage`` (D5) — the
SAME permission that already gates "Usuários" and "Grupos de permissão" on the
same screen. No RBAC/visibility effect: belonging to a team neither widens nor
narrows which conversations a user sees (D2).
"""

import asyncio
import logging

from fastapi import Request

from db.repositories import team_repo, team_member_repo
from server.authz import permission_denied
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def _serialize(team: dict, members_by_team: dict[int, list[int]]) -> dict:
    return {**team, "member_user_ids": members_by_team.get(team["id"], [])}


def register_routes(app, deps):

    @app.get("/api/teams")
    async def list_teams(request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        teams = await asyncio.to_thread(team_repo.list_all)
        by_team = await asyncio.to_thread(team_member_repo.member_ids_by_team)
        return _ok({"teams": [_serialize(t, by_team) for t in teams]})

    @app.post("/api/teams")
    async def create_team(body: dict, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        name = (body.get("name") or "").strip()
        if not name:
            return _err("Informe um nome para o time.", status=400)
        description = (body.get("description") or "").strip()
        restrict_visibility = bool(body.get("restrict_visibility"))
        visible_to_assignee = bool(body.get("visible_to_assignee"))
        team = await asyncio.to_thread(
            team_repo.create, name, description,
            restrict_visibility=restrict_visibility,
            visible_to_assignee=visible_to_assignee)
        member_ids = body.get("member_user_ids")
        if isinstance(member_ids, list):
            await asyncio.to_thread(team_member_repo.set_members, team["id"], member_ids)
        team["member_user_ids"] = await asyncio.to_thread(
            team_member_repo.member_ids, team["id"])
        logger.info("Team created: %s", name)
        return _ok({"team": team})

    @app.put("/api/teams/{team_id}")
    async def update_team(team_id: int, body: dict, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        existing = await asyncio.to_thread(team_repo.get, team_id)
        if not existing:
            return _err("Time não encontrado.", status=404)
        name = body.get("name")
        if name is not None and not name.strip():
            return _err("Informe um nome para o time.", status=400)
        description = body.get("description")
        restrict_visibility = body.get("restrict_visibility")
        visible_to_assignee = body.get("visible_to_assignee")
        team = await asyncio.to_thread(
            team_repo.update, team_id,
            name=(name.strip() if isinstance(name, str) else None),
            description=(description.strip() if isinstance(description, str) else None),
            restrict_visibility=(bool(restrict_visibility) if restrict_visibility is not None else None),
            visible_to_assignee=(bool(visible_to_assignee) if visible_to_assignee is not None else None))
        if isinstance(body.get("member_user_ids"), list):
            await asyncio.to_thread(
                team_member_repo.set_members, team_id, body["member_user_ids"])
        team["member_user_ids"] = await asyncio.to_thread(
            team_member_repo.member_ids, team_id)
        return _ok({"team": team})

    @app.delete("/api/teams/{team_id}")
    async def delete_team(team_id: int, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        existing = await asyncio.to_thread(team_repo.get, team_id)
        if not existing:
            return _err("Time não encontrado.", status=404)
        await asyncio.to_thread(team_repo.delete, team_id)
        logger.info("Team deleted: %s", existing.get("name"))
        return _ok({"deleted": True})
