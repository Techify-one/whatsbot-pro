"""Golden-master characterization of the RBAC authorization PATH for the routes
converted in Plano 23 · Fase B1 (BLOCKING; guards the ``permission_denied`` →
``Depends(require_permission(...))`` swap in ``ai_engine.py`` / ``admin.py`` /
``plugins.py``).

R16 is NOT low risk — it changes the authorization code path (default-allow in
legacy/open installs). This file LOCKS the CURRENT 403/200 behavior + the EXACT
error envelope BEFORE the change, and must stay green AFTER it:

  (a) a logged-in user LACKING the permission → 403 with the BYTE-IDENTICAL
      ``{"ok": false, "error": "Permissão negada."}`` envelope (NOT FastAPI's
      default ``{"detail": ...}``);
  (b) a logged-in user HAVING the permission → 200/allowed (the gate passes;
      we assert it is NOT a 403, i.e. the route body runs);
  (c) the NO-USER legacy/open default-allow path → allowed (no 403);
  (d) the ``filter.authz.decision`` ABAC seam can still downgrade allow→deny at
      the decision layer (``authz.acheck``), which is exactly what the new async
      ``require_permission`` dependency calls so the seam runs LIVE.

Notes on the harness:
* The hermetic ``build_app`` TestClient + a real login (``/api/auth/login``)
  attaches a Bearer token so ``auth_middleware`` sets ``request.state.user`` —
  this is how a logged-in user with a known permission set reaches the gate.
* Users are created ``custom=True`` with an EXPLICIT permission set (no roles),
  so "lacks" vs "has" is exact and stable across the shared-DB session.
* Distinct emails/phones per test (the suite shares one process DB).

Discipline: ADDITIVE only. We characterize existing behavior; we never change
app/source from this file.
"""

from __future__ import annotations

import pytest
from fastapi import Request

from server.helpers import _err


@pytest.fixture(autouse=True)
def _no_restart(monkeypatch):
    """Stub ``schedule_restart`` in every route module that imports it so a route
    whose body runs to completion (the "has permission" / default-allow legs on
    restart-triggering routes) cannot fire ``os._exit`` and kill the test process.
    Mirrors ``tests/test_endpoints.py`` which no-ops the same symbol. The AUTH
    behavior — which this file characterizes — is unaffected by the stub."""
    import server.routes.ai_engine as _ai
    import server.routes.admin as _admin
    import server.routes.plugins as _plugins

    for mod in (_ai, _admin, _plugins):
        monkeypatch.setattr(mod, "schedule_restart", lambda *a, **k: None, raising=False)


# ── The byte-exact 403 envelope the inline ``permission_denied`` produced ──────
# authz.permission_denied → _err("Permissão negada.", status=403). We materialize
# it the same way the production helper does and compare bytes, so the assertion
# tracks the real envelope even if _err's shape ever changes.
_DENIED_BODY = _err("Permissão negada.", status=403).body
_DENIED_TEXT = _DENIED_BODY.decode("utf-8")


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture
def make_custom_user():
    """Create exact-permission users and remove their sessions/rows afterwards."""
    from db.repositories import session_repo, user_repo
    from server.auth import hash_password_argon2

    created_ids: list[int] = []

    def _make(email: str, permission_keys: list[str]) -> int:
        user = user_repo.create(
            email=email, name=email.split("@")[0],
            password_hash=hash_password_argon2("supersecret"),
            permission_keys=permission_keys, custom=True,
        )
        created_ids.append(user["id"])
        return user["id"]

    yield _make

    for user_id in reversed(created_ids):
        session_repo.delete_for_user(user_id)
        assert user_repo.delete(user_id), f"could not remove RBAC test user {user_id}"


@pytest.fixture
def make_role_user():
    """Create a plain ROLE-based (non-custom, non-admin) user via an ad-hoc role.

    Plano 169 B2 fixture: `make_custom_user` above only ever creates
    ``custom_permissions=1`` users, so the "role comum" cell (a user whose
    permissions come from a role's ``role_permissions`` union) has no coverage
    of its own before this file. Each call mints a disposable role so tests
    don't share/contend on grants."""
    import uuid as _uuid

    from db.repositories import rbac_repo, session_repo, user_repo
    from server.auth import hash_password_argon2

    created_users: list[int] = []
    created_roles: list[int] = []

    def _make(email: str, permission_keys: list[str]) -> int:
        role_key = f"charz_role_{_uuid.uuid4().hex[:12]}"
        role = rbac_repo.create_role(role_key, "Charz role (RBAC test)", permission_keys)
        created_roles.append(role["id"])
        user = user_repo.create(
            email=email, name=email.split("@")[0],
            password_hash=hash_password_argon2("supersecret"),
            role_keys=[role_key], custom=False,
        )
        created_users.append(user["id"])
        return user["id"]

    yield _make

    for user_id in reversed(created_users):
        session_repo.delete_for_user(user_id)
        assert user_repo.delete(user_id), f"could not remove RBAC test user {user_id}"
    for role_id in reversed(created_roles):
        rbac_repo.delete_role(role_id)


def _auth_headers(client, email: str) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": "supersecret"})
    assert r.status_code == 200, r.text
    token = r.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def _assert_denied_envelope(resp) -> None:
    """The response is the canonical 403 the inline path returned: status 403 AND
    the byte-identical ``{"ok": false, "error": "Permissão negada."}`` body."""
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"ok": False, "error": "Permissão negada."}
    # Byte-level equality with the production _err(403) envelope — this is what
    # the new require_permission dependency MUST keep producing.
    assert resp.content == _DENIED_BODY, (
        f"403 body diverged from _err envelope.\n"
        f"  expected: {_DENIED_TEXT!r}\n"
        f"  actual:   {resp.content.decode('utf-8', 'replace')!r}")


# ── The exact (route, permission key) cells converted in B1 ───────────────────
# One representative gated route per file × its key. The gate is a dependency,
# so the 403 happens before observable route work. Prefer read-only routes when
# one directly exercises the same permission.
_CASES = [
    # As chaves granulares substituíram agent.manage. agent.config.manage segue
    # sendo leitura/edição; criar uma chave inexistente exige agent.create.
    ("ai_engine", "agent.config.manage", "get", "/api/ai/agents", None),
    ("ai_engine", "agent.tools.manage", "post", "/api/ai/restart", {}),
    # admin.py — key "database.manage" (moved from settings.manage in plano 24)
    ("admin", "database.manage", "get",  "/api/admin/database", None),
    ("admin", "database.manage", "post", "/api/admin/repair-sequences", {}),
    # plugins.py — key "plugins.manage"
    ("plugins", "plugins.manage", "post", "/api/plugins/restart", {}),
    ("plugins", "plugins.manage", "post", "/api/plugins/some_plugin/enable", {}),
]


def _call(client, method: str, path: str, body, headers: dict):
    fn = getattr(client, method)
    if method in ("get", "delete"):
        return fn(path, headers=headers)
    return fn(path, json=(body or {}), headers=headers)


def _slug(path: str) -> str:
    """A filesystem/email-safe slug of a route path (unique per case email)."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


# ── (a) user LACKING the permission → exact 403 envelope ──────────────────────

@pytest.mark.parametrize("file_id,key,method,path,body", _CASES,
                         ids=[f"{c[0]}:{c[3]}" for c in _CASES])
def test_lacking_permission_returns_exact_403(
    build_app, make_custom_user, file_id, key, method, path, body,
):
    """A logged-in user WITHOUT the route's permission → the canonical 403 with the
    byte-identical ``permission_denied`` envelope (NOT FastAPI's ``{detail}``)."""
    built = build_app(["gowa"])
    email = f"rbac_lacks_{_slug(path)}@test.com"
    # Custom user holding ONLY contact.read → guaranteed to lack agent/settings/plugins.manage.
    make_custom_user(email, ["contact.read"])
    headers = _auth_headers(built.client, email)

    resp = _call(built.client, method, path, body, headers)
    _assert_denied_envelope(resp)


# ── (b) user HAVING the permission → gate passes (not 403) ────────────────────

@pytest.mark.parametrize("file_id,key,method,path,body", _CASES,
                         ids=[f"{c[0]}:{c[3]}" for c in _CASES])
def test_having_permission_passes_gate(
    build_app, make_custom_user, file_id, key, method, path, body,
):
    """A logged-in user WITH the route's permission → the gate passes. We assert
    the response is NOT the 403 envelope (the route body ran); the route's own
    success/validation status is out of scope for the AUTH characterization."""
    built = build_app(["gowa"])
    email = f"rbac_has_{_slug(path)}@test.com"
    make_custom_user(email, [key])
    headers = _auth_headers(built.client, email)

    resp = _call(built.client, method, path, body, headers)
    assert resp.status_code != 403, (
        f"user holding {key!r} must pass the gate; got 403 {resp.text}")


# ── (c) no-user legacy/open default-allow → allowed ───────────────────────────

@pytest.mark.parametrize("file_id,key,method,path,body", _CASES,
                         ids=[f"{c[0]}:{c[3]}" for c in _CASES])
def test_no_user_legacy_default_allow(build_app, file_id, key, method, path, body):
    """No user identity (legacy single-password / open install) ⇒ default-allow:
    the gate passes (no 403). The hermetic TestClient sends no token, so
    ``request.state.user`` is None and ``_rbac_allows`` returns True."""
    built = build_app(["gowa"])
    resp = _call(built.client, method, path, body, headers={})
    assert resp.status_code != 403, (
        f"legacy/open (no user) must default-allow; got 403 {resp.text}")


# ── (d) the filter.authz.decision seam can downgrade allow→deny ───────────────

def test_authz_seam_can_downgrade_allow_to_deny(make_custom_user):
    """The ABAC seam (``filter.authz.decision``) runs AFTER RBAC and can flip
    allow→deny. Characterized at the DECISION layer (``authz.acheck``) because
    that is where the seam is genuinely live (the async path the new
    ``require_permission`` dependency uses); the OLD inline sync ``check`` ran on
    the event-loop thread where ``apply_filter_sync`` is inert.

    Locks: (1) with no filter, a granted permission ⇒ acheck True; (2) a filter
    that sets allow=False ⇒ acheck False even though RBAC granted it; (3) the
    seam is removed cleanly afterwards (no leak into other tests)."""
    import asyncio
    from unittest.mock import MagicMock

    from plugins import events as bus
    from server import authz

    uid = make_custom_user("rbac_seam@test.com", ["agent.config.manage"])

    # A fake Request whose state.user is our granted user (acheck reads
    # request.state.user via current_user()).
    req = MagicMock()
    req.state.user = {"id": uid}

    # Run acheck on a private loop wired into the bus so the async filter fires.
    loop = asyncio.new_event_loop()
    prev_loop = getattr(bus, "_loop", None)
    prev_handler = getattr(bus, "_agent_handler", None)
    bus.set_runtime(loop, prev_handler)
    try:
        # (1) baseline: granted + no seam filter ⇒ allowed.
        baseline = loop.run_until_complete(authz.acheck(req, "agent.config.manage"))
        assert baseline is True, "granted permission must be allowed with no seam filter"

        # (2) register a downgrade filter and re-check ⇒ denied.
        def _deny(ctx, value):
            value["allow"] = False
            return value

        bus.register_filter("_charz_authz_seam", "filter.authz.decision", _deny, priority=1)
        downgraded = loop.run_until_complete(authz.acheck(req, "agent.config.manage"))
        assert downgraded is False, "filter.authz.decision must be able to downgrade allow→deny"
    finally:
        # Remove ONLY our seam filter, restore the prior runtime, tear the loop down.
        bucket = bus._filters.get("filter.authz.decision")
        if bucket:
            bus._filters["filter.authz.decision"] = [
                t for t in bucket if t[1] != "_charz_authz_seam"]
        bus.set_runtime(prev_loop, prev_handler)
        loop.close()


# ── plano 169 B2 — cells `effective_permissions` will add a NEW derivation
# path for (role union, admin short-circuit) or a NEW identity-resolution
# branch for (API key). Custom user + no-identity are already covered above;
# these lock the CURRENT (pre-B2) behavior for the remaining three so a
# regression in the new code shows up here, not in production. ───────────────

def test_role_based_user_lacking_permission_returns_exact_403(build_app, make_role_user):
    """'role comum': permissions come from role_permissions, not an explicit
    per-user grant — the branch `rbac_repo.user_permissions` takes today when
    `custom_permissions=0` and the role isn't ``admin``."""
    built = build_app(["gowa"])
    make_role_user("rbac_role_lacks@test.com", ["contact.read"])
    headers = _auth_headers(built.client, "rbac_role_lacks@test.com")

    resp = _call(built.client, "get", "/api/ai/agents", None, headers)
    _assert_denied_envelope(resp)


def test_role_based_user_having_permission_passes_gate(build_app, make_role_user):
    built = build_app(["gowa"])
    make_role_user("rbac_role_has@test.com", ["agent.config.manage"])
    headers = _auth_headers(built.client, "rbac_role_has@test.com")

    resp = _call(built.client, "get", "/api/ai/agents", None, headers)
    assert resp.status_code != 403, (
        f"role-based user holding agent.config.manage must pass the gate; got {resp.text}")


def test_admin_role_passes_every_gate(build_app):
    """Admin is a short-circuit (ALL permissions, ``role_permissions`` not even
    seeded for it) — a distinct branch from both custom and plain-role users."""
    from db.repositories import session_repo, user_repo
    from server.auth import hash_password_argon2

    built = build_app(["gowa"])
    user = user_repo.create(
        email="rbac_admin@test.com", name="admin",
        password_hash=hash_password_argon2("supersecret"), role_keys=["admin"])
    try:
        headers = _auth_headers(built.client, "rbac_admin@test.com")
        resp = _call(built.client, "get", "/api/ai/agents", None, headers)
        assert resp.status_code != 403, f"admin must pass every gate; got {resp.text}"
    finally:
        session_repo.delete_for_user(user["id"])
        assert user_repo.delete(user["id"])


def test_api_key_identity_gates_the_same_as_a_session(build_app, make_custom_user):
    """The key is a badge that resolves to the SAME ``request.state.user`` a
    session does (server/api_keys.py docstring) — the gate must decide off the
    SAME permission set, whichever identity-resolution branch set it."""
    from db.repositories import api_key_repo
    from server import api_keys as keylib

    built = build_app(["gowa"])
    uid = make_custom_user("rbac_apikey@test.com", ["agent.config.manage"])
    raw, prefix, key_hash = keylib.generate_key()
    api_key_repo.create(user_id=uid, label="rbac-characterization",
                        key_hash=key_hash, prefix=prefix, last4=keylib.last4(raw))

    granted = built.client.get("/api/ai/agents", headers={"X-Api-Key": raw})
    assert granted.status_code != 403, f"key must carry the owner's grant; got {granted.text}"

    lacking = built.client.post(
        "/api/admin/repair-sequences", headers={"X-Api-Key": raw}, json={})
    _assert_denied_envelope(lacking)


def test_two_permission_checks_in_one_request_query_the_catalog_once(
        build_app, make_custom_user):
    """Plano 169 B2 item 6: a request that (a) is gated by a permission
    dependency AND (b) makes a SECOND, independent authz decision from within
    the route body — exactly what ``visible_inbox_ids``/``ConversationAccessScope.
    for_request`` do on top of a route's own gate in real conversation routes —
    pays exactly ONE permission-catalog query for the whole request, not one per
    check. Before B2 this was two: ``acheck`` (the dependency) and ``check``
    (the body) each ran their own ``rbac_repo.user_has_permission``.

    Mounted as a throwaway route on the built app so the proof doesn't depend on
    a production route happening to shape this way — no production file is
    touched here."""
    from fastapi import APIRouter, Depends
    from sqlalchemy import event

    from db.engine import get_engine
    from server import authz
    from server.deps import require_permission

    router = APIRouter()

    @router.get("/api/_charz_multi_check",
               dependencies=[Depends(require_permission("channel.manage"))])
    async def _multi_check(request: Request):
        second = authz.check(request, "conversation.read_all")
        return {"ok": True, "data": {"second": second}}

    built = build_app(["gowa"])
    built.app.include_router(router)
    email = "rbac_multi_check@test.com"
    make_custom_user(email, ["channel.manage", "conversation.read_all"])
    headers = _auth_headers(built.client, email)

    queries: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        resp = built.client.get("/api/_charz_multi_check", headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["second"] is True

    permission_queries = [
        q for q in queries if "user_permissions" in q or "role_permissions" in q]
    assert len(permission_queries) == 1, (
        "expected exactly 1 permission-catalog query (the middleware's own, "
        f"reused by both checks); got {len(permission_queries)}:\n" +
        "\n---\n".join(permission_queries))
