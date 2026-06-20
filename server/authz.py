"""Authorization helpers for permission-gated endpoints (plano 03 Fase 4).

Additive and backward-compatible: when there is no user identity on the request
(legacy single-password or open install), checks pass — the middleware already
decided whether a token was required. Gates only bite a *logged-in user* who
lacks the permission, which is exactly the RBAC behavior we want.
"""

from __future__ import annotations

from fastapi import Request

from db.repositories import rbac_repo
from server.helpers import _err


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def permission_denied(request: Request, permission_key: str):
    """Return an ``_err(403)`` response if the current user lacks the permission.

    Returns ``None`` when access is allowed (no user identity ⇒ legacy/open path,
    or the user holds the permission). Usage in a handler::

        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
    """
    user = current_user(request)
    if user is None:
        return None  # legacy/open — nothing to gate
    if not rbac_repo.user_has_permission(user["id"], permission_key):
        return _err("Permissão negada.", status=403)
    return None
