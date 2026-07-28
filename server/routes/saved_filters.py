"""Saved conversation filters — named inbox filter presets, per user.

Chatwoot-style: the operator builds a filter in the inbox toolbar and saves it
under a name; saved presets can be re-applied with one click and an active-preset
chip is shown in the toolbar. Presets are scoped to the logged-in user (or shared
on the install in open mode, before the first admin — where there's no identity).
"""

import asyncio
import logging

from fastapi import Request

from db.repositories import saved_filter_repo
from server.authz import current_user
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

NAME_MAX = 60


def _user_id(request: Request) -> int | None:
    user = current_user(request)
    return (user or {}).get("id")


def _clean_name(raw) -> str:
    return (raw or "").strip()


def register_routes(app, deps):

    @app.get("/api/me/conversation-filters")
    async def list_filters(request: Request):
        """List the current user's saved conversation-filter presets."""
        uid = _user_id(request)
        rows = await asyncio.to_thread(saved_filter_repo.list_for_user, uid)
        return _ok(rows)

    @app.post("/api/me/conversation-filters")
    async def create_filter(request: Request):
        """Create a named preset from the current filter spec."""
        body = await request.json()
        name = _clean_name(body.get("name"))
        spec = body.get("spec")
        if not name:
            return _err("Nome do filtro é obrigatório.")
        if len(name) > NAME_MAX:
            return _err(f"Nome do filtro deve ter no máximo {NAME_MAX} caracteres.")
        if not isinstance(spec, dict):
            return _err("Filtro inválido.")
        uid = _user_id(request)
        # Reject duplicate name for this user (case-insensitive) to keep the list tidy.
        existing = await asyncio.to_thread(saved_filter_repo.list_for_user, uid)
        if any(r["name"].lower() == name.lower() for r in existing):
            return _err(f"Já existe um filtro chamado '{name}'.")
        row = await asyncio.to_thread(saved_filter_repo.create, uid, name, spec)
        return _ok(row)

    @app.put("/api/me/conversation-filters/{filter_id}")
    async def update_filter(filter_id: int, request: Request):
        """Rename a preset and/or overwrite its spec with the current filter."""
        body = await request.json()
        uid = _user_id(request)
        name = None
        if "name" in body:
            name = _clean_name(body.get("name"))
            if not name:
                return _err("Nome do filtro é obrigatório.")
            if len(name) > NAME_MAX:
                return _err(f"Nome do filtro deve ter no máximo {NAME_MAX} caracteres.")
            existing = await asyncio.to_thread(saved_filter_repo.list_for_user, uid)
            if any(r["id"] != filter_id and r["name"].lower() == name.lower() for r in existing):
                return _err(f"Já existe um filtro chamado '{name}'.")
        spec = body.get("spec") if "spec" in body else None
        if spec is not None and not isinstance(spec, dict):
            return _err("Filtro inválido.")
        row = await asyncio.to_thread(
            saved_filter_repo.update, filter_id, uid, name=name, spec=spec)
        if row is None:
            return _err("Filtro não encontrado.", status=404)
        return _ok(row)

    @app.delete("/api/me/conversation-filters/{filter_id}")
    async def delete_filter(filter_id: int, request: Request):
        """Delete an owned preset."""
        uid = _user_id(request)
        ok = await asyncio.to_thread(saved_filter_repo.delete, filter_id, uid)
        if not ok:
            return _err("Filtro não encontrado.", status=404)
        return _ok({"deleted": filter_id})
