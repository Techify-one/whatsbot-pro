"""Authorization helpers for permission-gated endpoints (plano 03 Fase 4).

Additive and backward-compatible: when there is no user identity on the request
(legacy single-password or open install), checks pass — the middleware already
decided whether a token was required. Gates only bite a *logged-in user* who
lacks the permission, which is exactly the RBAC behavior we want.
"""

from __future__ import annotations

from fastapi import Request

from db.repositories import rbac_repo, inbox_member_repo
from plugins.events import apply_filter, apply_filter_sync
from server.helpers import _err


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def _rbac_allows(user: dict | None, permission_key: str) -> bool:
    """RBAC-only decision. ``True`` for legacy/open (no user identity)."""
    if user is None:
        return True
    return rbac_repo.user_has_permission(user["id"], permission_key)


def check(request: Request, permission_key: str) -> bool:
    """Central authorization decision: RBAC + the ABAC extension seam.

    After the RBAC check, the result is passed through the
    ``filter.authz.decision`` filter so a plugin can downgrade allow→deny by
    attribute (e.g. business hours). No evaluator is embedded in the core (v1):
    with no filter registered the value passes through unchanged. Sync path —
    on the event-loop thread the filter is inert (see :func:`apply_filter_sync`);
    use :func:`acheck` from async dependencies to make the seam live."""
    user = current_user(request)
    allow = _rbac_allows(user, permission_key)
    value = apply_filter_sync(
        "filter.authz.decision",
        {"user": user, "permission_key": permission_key, "allow": allow},
        {"permission_key": permission_key},
    )
    if value is None:
        return False
    return bool(value.get("allow", allow)) if isinstance(value, dict) else allow


async def acheck(request: Request, permission_key: str) -> bool:
    """Async sibling of :func:`check` (awaits the ABAC filter).

    Used by the ``plugin_permission`` FastAPI dependency, which runs on the event
    loop where the sync filter is skipped — here the seam is genuinely live."""
    user = current_user(request)
    allow = _rbac_allows(user, permission_key)
    value = await apply_filter(
        "filter.authz.decision",
        {"user": user, "permission_key": permission_key, "allow": allow},
        {"permission_key": permission_key},
    )
    if value is None:
        return False
    return bool(value.get("allow", allow)) if isinstance(value, dict) else allow


def has_permission(request: Request, permission_key: str) -> bool:
    """Boolean form of :func:`permission_denied` for surfacing capability flags.

    Mirrors the same legacy/open semantics: returns ``True`` when there is no user
    identity (open/single-password install) or when the logged-in user holds the
    permission. Used to tell the UI whether to show an action (e.g. create/delete
    template), so the gate is decided server-side, not by drilling the user object."""
    return check(request, permission_key)


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


def can_access_inbox(request: Request, inbox_id: int | None) -> bool:
    """Whether the current user may act on a conversation in ``inbox_id``.

    Mirrors :func:`visible_inbox_ids` for WRITE paths (send, react, delete,
    assign, …) — leitura já é coberta pelo scoping da listagem. ``True`` quando o
    usuário não é escopado (legacy/open, admin, ``conversation.read_all``) ou é
    membro da inbox. Um ``inbox_id`` indeterminável + usuário escopado ⇒ ``False``
    (nega por segurança). Espelha o ``_inbox_hidden`` da leitura."""
    vis = visible_inbox_ids(request)
    return vis is None or (inbox_id is not None and inbox_id in vis)


def permission_denied(request: Request, permission_key: str):
    """Return an ``_err(403)`` response if the current user lacks the permission.

    Returns ``None`` when access is allowed (no user identity ⇒ legacy/open path,
    or the user holds the permission). Usage in a handler::

        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
    """
    if not check(request, permission_key):
        return _err("Permissão negada.", status=403)
    return None
