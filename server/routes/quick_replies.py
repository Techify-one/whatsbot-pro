"""Quick replies (canned responses) CRUD endpoints — plano 04.

Global single list (P42, no scope), plain text (P47). ``short_code`` is UNIQUE
global (P41), stored without the leading slash. The same GET feeds both the
composer autocomplete and the management screen.

Authorization (RBAC): mutations are gated by ``quickreply.manage``. The GET stays
open so the message composer's autocomplete works for any authenticated operator.
"""

import asyncio
import logging
import re

from fastapi import Request

from db.repositories import quick_reply_repo
from server.authz import permission_denied
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

# Backend safety net for short_code (primary validation is in the front, P45):
# lowercase, no spaces/accents, does not start with '/'.
_SHORT_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_MAX_SHORT_CODE = 40
_MAX_CONTENT = 5000


def _normalize_short_code(raw: str) -> str:
    """strip → lowercase → drop a leading slash."""
    return (raw or "").strip().lstrip("/").lower()


def _validate(short_code: str, content: str) -> str | None:
    """Return an error message, or None if valid."""
    if not short_code:
        return "O atalho é obrigatório."
    if len(short_code) > _MAX_SHORT_CODE:
        return f"O atalho deve ter no máximo {_MAX_SHORT_CODE} caracteres."
    if not _SHORT_CODE_RE.match(short_code):
        return "O atalho deve usar só minúsculas, números, '-' e '_' (sem espaços ou acentos)."
    if not content:
        return "O conteúdo é obrigatório."
    if len(content) > _MAX_CONTENT:
        return f"O conteúdo deve ter no máximo {_MAX_CONTENT} caracteres."
    return None


def register_routes(app, deps):
    ws_manager = deps.ws_manager

    async def _broadcast_changed():
        try:
            await ws_manager.broadcast("quick_replies_changed", {})
        except Exception:
            pass

    @app.get("/api/quick-replies")
    async def list_quick_replies():
        """Return the whole global list (autocomplete + management screen)."""
        rows = await asyncio.to_thread(quick_reply_repo.list_all)
        return _ok(rows)

    @app.post("/api/quick-replies")
    async def create_quick_reply(request: Request):
        denied = permission_denied(request, "quickreply.manage")
        if denied:
            return denied
        body = await request.json()
        short_code = _normalize_short_code(body.get("short_code", ""))
        content = (body.get("content") or "").strip()
        err = _validate(short_code, content)
        if err:
            return _err(err)
        if await asyncio.to_thread(quick_reply_repo.exists, short_code):
            return _err(f"O atalho '/{short_code}' já existe.")
        row = await asyncio.to_thread(quick_reply_repo.create, short_code=short_code, content=content)
        if row is None:
            return _err(f"O atalho '/{short_code}' já existe.")
        await _broadcast_changed()
        return _ok(row)

    @app.put("/api/quick-replies/{qr_id}")
    async def update_quick_reply(qr_id: int, request: Request):
        denied = permission_denied(request, "quickreply.manage")
        if denied:
            return denied
        body = await request.json()
        existing = await asyncio.to_thread(quick_reply_repo.get_by_id, qr_id)
        if existing is None:
            return _err("Resposta rápida não encontrada.", 404)
        short_code = existing["short_code"]
        if "short_code" in body:
            short_code = _normalize_short_code(body.get("short_code", ""))
        content = existing["content"] if "content" not in body else (body.get("content") or "").strip()
        err = _validate(short_code, content)
        if err:
            return _err(err)
        if short_code != existing["short_code"] and await asyncio.to_thread(
            quick_reply_repo.exists, short_code, qr_id
        ):
            return _err(f"O atalho '/{short_code}' já existe.")
        row = await asyncio.to_thread(
            quick_reply_repo.update, qr_id, short_code=short_code, content=content
        )
        if row is None:
            return _err(f"O atalho '/{short_code}' já existe.")
        await _broadcast_changed()
        return _ok(row)

    @app.delete("/api/quick-replies/{qr_id}")
    async def delete_quick_reply(qr_id: int, request: Request):
        denied = permission_denied(request, "quickreply.manage")
        if denied:
            return denied
        ok = await asyncio.to_thread(quick_reply_repo.delete, qr_id)
        if not ok:
            return _err("Resposta rápida não encontrada.", 404)
        await _broadcast_changed()
        return _ok({"deleted": qr_id})
