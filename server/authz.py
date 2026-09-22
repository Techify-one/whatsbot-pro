"""Authorization helpers for permission-gated endpoints (plano 03 Fase 4).

Additive and backward-compatible: when there is no user identity on the request
(open install, before the first admin exists), checks pass — the middleware
already decided whether a token was required. Gates only bite a *logged-in user*
who lacks the permission, which is exactly the RBAC behavior we want.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import and_, false as sa_false, or_, true as sa_true

from db.repositories import rbac_repo, inbox_member_repo, team_member_repo, team_repo, user_repo
from db.tables import conversations
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

    Mirrors the same open-install semantics: returns ``True`` when there is no user
    identity (open install, before the first admin) or when the logged-in user holds the
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


@dataclass(frozen=True)
class ConversationAccessScope:
    """One request's conversation visibility policy.

    The same immutable inputs drive SQL collection filters and object guards,
    preventing list/detail/write from drifting apart.  Team privacy never widens
    inbox access.  ``list_hidden`` affects collections only; ``private`` also
    protects direct reads, writes, realtime and media.
    """

    user_id: int | None
    inbox_ids: list[int] | None
    team_ids: frozenset[int]
    read_any_team: bool
    teams_by_id: dict[int, dict]

    @property
    def is_unrestricted(self) -> bool:
        """True when neither inbox nor team policy narrows this actor."""
        return self.inbox_ids is None and self.read_any_team

    @classmethod
    def for_request(cls, request: Request) -> "ConversationAccessScope":
        cached = getattr(request.state, "conversation_access_scope", None)
        if cached is not None:
            return cached
        user = current_user(request)
        user_id = user.get("id") if user else None
        # Open/legacy installs keep their historical unrestricted behavior.
        read_any = user_id is None or rbac_repo.user_has_permission(
            user_id, "conversation.team.read_any")
        team_ids = (frozenset() if user_id is None else
                    frozenset(team_member_repo.team_ids_for_user(user_id)))
        team_rows = team_repo.list_all(include_inactive=True)
        scope = cls(
            user_id=user_id,
            inbox_ids=visible_inbox_ids(request),
            team_ids=team_ids,
            read_any_team=read_any,
            teams_by_id={int(team["id"]): team for team in team_rows},
        )
        request.state.conversation_access_scope = scope
        return scope

    @classmethod
    def for_user(cls, user_id: int | None) -> "ConversationAccessScope":
        """Build the same policy outside HTTP (notably WebSocket fan-out)."""
        if user_id is None:
            inbox_ids = None
            read_any = True
            team_ids = frozenset()
        else:
            inbox_ids = (None if rbac_repo.user_has_permission(
                user_id, "conversation.read_all") else
                inbox_member_repo.inbox_ids_for_user(user_id))
            read_any = rbac_repo.user_has_permission(
                user_id, "conversation.team.read_any")
            team_ids = frozenset(team_member_repo.team_ids_for_user(user_id))
        team_rows = team_repo.list_all(include_inactive=True)
        return cls(
            user_id=user_id, inbox_ids=inbox_ids, team_ids=team_ids,
            read_any_team=read_any,
            teams_by_id={int(team["id"]): team for team in team_rows},
        )

    @classmethod
    def for_users(cls, user_ids) -> dict[int | None, "ConversationAccessScope"]:
        """Batch sibling of :func:`for_user` (plano 168 F3) — the WS fan-out used
        to call ``for_user`` once per CONNECTED SOCKET, serially (~14 round trips
        each); this builds the SAME per-user policy in a small, constant number
        of batched reads regardless of how many ids are in ``user_ids``.

        ``None`` (no identity — legacy/open) maps to the unrestricted scope, same
        as ``for_user(None)``. A deactivated user (``is_active=0``, I9) gets a
        scope that denies every conversation instead of its real membership —
        deactivation doesn't close an already-open socket, so it must not keep
        receiving live conversation data through it.
        """
        wanted = set(user_ids)
        team_rows = team_repo.list_all(include_inactive=True)
        teams_by_id = {int(team["id"]): team for team in team_rows}
        out: dict[int | None, "ConversationAccessScope"] = {}
        if None in wanted:
            out[None] = cls(user_id=None, inbox_ids=None, team_ids=frozenset(),
                            read_any_team=True, teams_by_id=teams_by_id)

        ids = {uid for uid in wanted if uid is not None}
        if not ids:
            return out

        active_by_id = user_repo.is_active_many(ids)
        perms_by_id = rbac_repo.user_permissions_many(ids)
        inbox_by_id = inbox_member_repo.inbox_ids_for_users(ids)
        team_by_id = team_member_repo.team_ids_for_users(ids)

        for uid in ids:
            if not active_by_id.get(uid, False):
                out[uid] = cls(user_id=uid, inbox_ids=[], team_ids=frozenset(),
                               read_any_team=False, teams_by_id=teams_by_id)
                continue
            perms = perms_by_id.get(uid, set())
            has_read_all = "*" in perms or "conversation.read_all" in perms
            has_read_any_team = "*" in perms or "conversation.team.read_any" in perms
            out[uid] = cls(
                user_id=uid,
                inbox_ids=(None if has_read_all else inbox_by_id.get(uid, [])),
                team_ids=frozenset(team_by_id.get(uid, [])),
                read_any_team=has_read_any_team,
                teams_by_id=teams_by_id,
            )
        return out

    def _team_exception_clause(self, restricted_ids: list[int]):
        if not restricted_ids:
            return sa_true()
        allowed_ids = sorted(self.team_ids)
        visible_assignee_ids = [
            team_id for team_id in restricted_ids
            if bool(self.teams_by_id[team_id].get("visible_to_assignee"))
        ]
        clauses = [
            conversations.c.team_id.is_(None),
            conversations.c.team_id.notin_(restricted_ids),
        ]
        if allowed_ids:
            clauses.append(conversations.c.team_id.in_(allowed_ids))
        if self.user_id is not None and visible_assignee_ids:
            clauses.append(and_(
                conversations.c.team_id.in_(visible_assignee_ids),
                conversations.c.assignee_user_id == self.user_id,
            ))
        return or_(*clauses)

    def collection_clause(self, surface: str = "list"):
        clauses = []
        if self.inbox_ids is not None:
            clauses.append(
                conversations.c.inbox_id.in_(self.inbox_ids)
                if self.inbox_ids else sa_false())
        if not self.read_any_team:
            if surface == "list":
                restricted_ids = [
                    team_id for team_id, team in self.teams_by_id.items()
                    if bool(team.get("restrict_visibility"))
                ]
            else:
                restricted_ids = [
                    team_id for team_id, team in self.teams_by_id.items()
                    if bool(team.get("enforce_team_access"))
                ]
            clauses.append(self._team_exception_clause(restricted_ids))
        return and_(*clauses) if clauses else sa_true()

    def allows(self, conversation: dict | None, surface: str = "direct") -> bool:
        if not conversation:
            return False
        inbox_id = conversation.get("inbox_id")
        if self.inbox_ids is not None and inbox_id not in self.inbox_ids:
            return False
        if self.read_any_team:
            return True
        team_id = conversation.get("team_id")
        if team_id is None:
            return True
        team = self.teams_by_id.get(int(team_id))
        if not team:
            return False
        restricted = (bool(team.get("restrict_visibility")) if surface == "list"
                      else bool(team.get("enforce_team_access")))
        if not restricted:
            return True
        if int(team_id) in self.team_ids:
            return True
        return bool(
            team.get("visible_to_assignee")
            and self.user_id is not None
            and conversation.get("assignee_user_id") == self.user_id
        )


def conversation_access_scope(request: Request) -> ConversationAccessScope:
    return ConversationAccessScope.for_request(request)


def can_assign_team(request: Request, current_team_id: int | None,
                    target_team_id: int | None) -> bool:
    """Whether the actor may move between the origin and destination teams."""
    user = current_user(request)
    if user is None:
        return True
    user_id = int(user["id"])
    if rbac_repo.user_has_permission(user_id, "conversation.team.assign_any"):
        return True
    if not rbac_repo.user_has_permission(user_id, "conversation.team.assign"):
        return False
    memberships = set(team_member_repo.team_ids_for_user(user_id))
    return all(team_id is None or team_id in memberships
               for team_id in (current_team_id, target_team_id))


def can_assign_user_to_conversation(conversation: dict, assignee_user_id) -> bool:
    """Keep private conversations from receiving an assignee who cannot open them."""
    if assignee_user_id in (None, ""):
        return True
    team_id = conversation.get("team_id")
    if team_id is None:
        return True
    team = team_repo.get(int(team_id))
    if not team or not team.get("enforce_team_access"):
        return True
    if team.get("visible_to_assignee"):
        return True
    try:
        assignee_id = int(assignee_user_id)
    except (TypeError, ValueError):
        return False
    return assignee_id in set(team_member_repo.member_ids(int(team_id)))


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
