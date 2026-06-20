"""Authorization helpers for permission-gated endpoints (plano 03 Fase 4).

Additive and backward-compatible: when there is no user identity on the request
(legacy single-password or open install), checks pass — the middleware already
decided whether a token was required. Gates only bite a *logged-in user* who
lacks the permission, which is exactly the RBAC behavior we want.
"""

from __future__ import annotations

from fastapi import Request

from db.repositories import rbac_repo, inbox_member_repo
from server.helpers import _err


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def visible_inbox_ids(request: Request) -> list[int] | None:
    """Inbox ids the current user is allowed to see, or ``None`` for "all".

    ``None`` means no scoping (legacy/open install, admin, or anyone holding
    ``conversation.read_all``). Otherwise the user only sees conversations of the
    inboxes they are a member of (``inbox_members``). An empty list ⇒ member of
    no inbox ⇒ sees nothing.
    """
    user = current_user(request)
    if user is None:
        return None  # legacy/open — no scoping
    if rbac_repo.user_has_permission(user["id"], "conversation.read_all"):
        return None  # admin (short-circuit) or explicit read_all ⇒ sees all
    return inbox_member_repo.inbox_ids_for_user(user["id"])


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
