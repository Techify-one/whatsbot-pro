"""Self-service account endpoints (plano 47).

``POST /api/me/password`` lets a logged-in RBAC user change their OWN password,
requiring the current password (re-authentication). This is a universal account
capability, NOT an RBAC permission — any authenticated user can rotate their own
credential. It is distinct from the admin reset (``POST /api/users/{id}/password``,
gated by ``users.manage``, which sets OTHER users' passwords without the old one).

⚠️ Mounted under ``/api/me/*`` (NOT ``/api/auth/*``): the ``/api/auth/`` prefix is
auth-exempt in the middleware, so a handler there would never see the logged-in
user (``request.state.user`` stays ``None``). ``/api/me/*`` is protected, so the
middleware resolves the bearer token and populates the identity — same precedent
as :mod:`server.routes.saved_filters`.
"""

import asyncio
import logging

from fastapi import Request

from db.repositories import user_repo
from server.auth import hash_password_argon2, verify_password_argon2
from server.authz import current_user
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

MIN_PASSWORD_LEN = 8


def register_routes(app, deps):

    @app.post("/api/me/password")
    async def change_my_password(body: dict, request: Request):
        """Change the current RBAC user's own password (requires the current one)."""
        user = current_user(request)
        if not user:
            # Legacy single-password / open install has no user identity — the panel
            # password is managed elsewhere. Never 401 (that triggers a global logout).
            return _err("Disponível apenas para usuários (não no modo de senha única).",
                        status=403)

        current_password = body.get("current_password") or ""
        new_password = body.get("new_password") or ""

        if len(new_password) < MIN_PASSWORD_LEN:
            return _err(f"A nova senha deve ter ao menos {MIN_PASSWORD_LEN} caracteres.",
                        status=400)
        if new_password == current_password:
            return _err("A nova senha deve ser diferente da atual.", status=400)

        auth_row = await asyncio.to_thread(user_repo.get_auth_row_by_id, user["id"])
        if not auth_row or not verify_password_argon2(
                current_password, auth_row.get("password_hash", "")):
            # 400 (business error), never 401 — a wrong current password must not
            # log the user out via the shared 401 branch in the frontend httpClient.
            return _err("A senha atual está incorreta.", status=400)

        phc = hash_password_argon2(new_password)
        await asyncio.to_thread(user_repo.set_password, user["id"], phc)
        logger.info("User %s changed their own password.", user.get("email"))
        return _ok({"updated": True})
