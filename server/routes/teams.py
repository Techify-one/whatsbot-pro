"""Team administration endpoints with atomic membership and safe lifecycle."""

import asyncio
import logging

from fastapi import Request

from db.repositories import team_repo, team_member_repo
from server.authz import permission_denied
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def _serialize(team: dict, members_by_team: dict[int, list[int]]) -> dict:
    return {**team, "member_user_ids": members_by_team.get(team["id"], [])}


def _access_fields(body: dict) -> tuple[bool | None, bool | None]:
    """Return ``(restrict_visibility, enforce_team_access)`` from either API form."""
    mode = body.get("access_mode")
    if mode is not None:
        if mode not in {"open", "list_hidden", "private"}:
            raise ValueError("Modo de acesso inválido.")
        return mode != "open", mode == "private"
    restrict = body.get("restrict_visibility")
    enforce = body.get("enforce_team_access")
    return (
        bool(restrict) if restrict is not None else None,
        bool(enforce) if enforce is not None else None,
    )


def register_routes(app, deps):

    @app.get("/api/teams")
    async def list_teams(request: Request, include_inactive: bool = False):
        denied = permission_denied(request, "team.manage")
        if denied:
            return denied
        teams = await asyncio.to_thread(
            team_repo.list_all, include_inactive=include_inactive)
        by_team = await asyncio.to_thread(team_member_repo.member_ids_by_team)
        return _ok({"teams": [_serialize(t, by_team) for t in teams]})

    @app.post("/api/teams")
    async def create_team(body: dict, request: Request):
        denied = permission_denied(request, "team.manage")
        if denied:
            return denied
        name = (body.get("name") or "").strip()
        if not name:
            return _err("Informe um nome para o time.", status=400)
        description = (body.get("description") or "").strip()
        try:
            restrict_visibility, enforce_team_access = _access_fields(body)
        except ValueError as exc:
            return _err(str(exc), status=400)
        restrict_visibility = bool(restrict_visibility)
        visible_to_assignee = bool(body.get("visible_to_assignee"))
        member_ids = body.get("member_user_ids")
        try:
            team = await asyncio.to_thread(
                team_repo.create, name, description,
                restrict_visibility=restrict_visibility,
                visible_to_assignee=visible_to_assignee,
                enforce_team_access=bool(enforce_team_access),
                member_user_ids=(member_ids if isinstance(member_ids, list) else []))
        except (ValueError, team_repo.TeamNameConflict) as exc:
            return _err(str(exc), status=409 if isinstance(exc, team_repo.TeamNameConflict) else 400)
        logger.info("Team created: %s", name)
        await deps.ws_manager.broadcast(
            "conversation_access_changed", {"team_id": team["id"]})
        return _ok({"team": team})

    @app.put("/api/teams/{team_id}")
    async def update_team(team_id: int, body: dict, request: Request):
        denied = permission_denied(request, "team.manage")
        if denied:
            return denied
        existing = await asyncio.to_thread(team_repo.get, team_id)
        if not existing:
            return _err("Time não encontrado.", status=404)
        name = body.get("name")
        if name is not None and not name.strip():
            return _err("Informe um nome para o time.", status=400)
        description = body.get("description")
        try:
            restrict_visibility, enforce_team_access = _access_fields(body)
        except ValueError as exc:
            return _err(str(exc), status=400)
        visible_to_assignee = body.get("visible_to_assignee")
        try:
            team = await asyncio.to_thread(
                team_repo.update, team_id,
                name=(name.strip() if isinstance(name, str) else None),
                description=(description.strip() if isinstance(description, str) else None),
                restrict_visibility=restrict_visibility,
                visible_to_assignee=(bool(visible_to_assignee) if visible_to_assignee is not None else None),
                enforce_team_access=enforce_team_access,
                is_active=(bool(body["is_active"]) if "is_active" in body else None),
                member_user_ids=(body["member_user_ids"]
                                 if isinstance(body.get("member_user_ids"), list) else None),
                confirm_access_expansion=bool(body.get("confirm_access_expansion")))
        except (ValueError, team_repo.TeamNameConflict) as exc:
            return _err(str(exc), status=409 if isinstance(exc, team_repo.TeamNameConflict) else 400)
        await deps.ws_manager.broadcast(
            "conversation_access_changed", {"team_id": team_id})
        return _ok({"team": team})

    @app.delete("/api/teams/{team_id}")
    async def delete_team(team_id: int, request: Request, hard: bool = False):
        denied = permission_denied(request, "team.manage")
        if denied:
            return denied
        existing = await asyncio.to_thread(team_repo.get, team_id)
        if not existing:
            return _err("Time não encontrado.", status=404)
        if not hard:
            team = await asyncio.to_thread(team_repo.deactivate, team_id)
            logger.info("Team deactivated: %s", existing.get("name"))
            await deps.ws_manager.broadcast(
                "conversation_access_changed", {"team_id": team_id})
            return _ok({"deleted": False, "deactivated": True, "team": team})
        try:
            await asyncio.to_thread(team_repo.delete, team_id)
        except team_repo.TeamHasConversations as exc:
            return _err(
                f"O time possui {exc.count} conversa(s) vinculada(s). "
                "Desative-o ou remova os vínculos antes da exclusão definitiva.",
                status=409)
        logger.info("Team permanently deleted: %s", existing.get("name"))
        await deps.ws_manager.broadcast(
            "conversation_access_changed", {"team_id": team_id})
        return _ok({"deleted": True, "deactivated": False})
