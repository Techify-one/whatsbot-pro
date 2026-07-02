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

def _make_custom_user(email: str, permission_keys: list[str]) -> int:
    """Create an ACTIVE user with an explicit (custom) permission set + a known
    password, so login works and the effective perms are exactly ``permission_keys``."""
    from db.repositories import user_repo
    from server.auth import hash_password_argon2

    user = user_repo.create(
        email=email, name=email.split("@")[0],
        password_hash=hash_password_argon2("supersecret"),
        permission_keys=permission_keys, custom=True,
    )
    return user["id"]


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
# One representative gated route per file × its key. Each is a PUT/POST whose
# FIRST action in the body is the permission check (verified by recon), so the
# 403 happens before any observable work — safe to also enforce via a dependency.
_CASES = [
    # ai_engine.py — key "agent.manage"
    ("ai_engine", "agent.manage", "put",  "/api/ai/agents/some_agent",
     {"display_name": "x", "prompt_key": "p"}),
    ("ai_engine", "agent.manage", "post", "/api/ai/restart", {}),
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
def test_lacking_permission_returns_exact_403(build_app, file_id, key, method, path, body):
    """A logged-in user WITHOUT the route's permission → the canonical 403 with the
    byte-identical ``permission_denied`` envelope (NOT FastAPI's ``{detail}``)."""
    built = build_app(["gowa"])
    email = f"rbac_lacks_{_slug(path)}@test.com"
    # Custom user holding ONLY contact.read → guaranteed to lack agent/settings/plugins.manage.
    _make_custom_user(email, ["contact.read"])
    headers = _auth_headers(built.client, email)

    resp = _call(built.client, method, path, body, headers)
    _assert_denied_envelope(resp)


# ── (b) user HAVING the permission → gate passes (not 403) ────────────────────

@pytest.mark.parametrize("file_id,key,method,path,body", _CASES,
                         ids=[f"{c[0]}:{c[3]}" for c in _CASES])
def test_having_permission_passes_gate(build_app, file_id, key, method, path, body):
    """A logged-in user WITH the route's permission → the gate passes. We assert
    the response is NOT the 403 envelope (the route body ran); the route's own
    success/validation status is out of scope for the AUTH characterization."""
    built = build_app(["gowa"])
    email = f"rbac_has_{_slug(path)}@test.com"
    _make_custom_user(email, [key])
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

def test_authz_seam_can_downgrade_allow_to_deny():
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

    uid = _make_custom_user("rbac_seam@test.com", ["agent.manage"])

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
        baseline = loop.run_until_complete(authz.acheck(req, "agent.manage"))
        assert baseline is True, "granted permission must be allowed with no seam filter"

        # (2) register a downgrade filter and re-check ⇒ denied.
        def _deny(ctx, value):
            value["allow"] = False
            return value

        bus.register_filter("_charz_authz_seam", "filter.authz.decision", _deny, priority=1)
        downgraded = loop.run_until_complete(authz.acheck(req, "agent.manage"))
        assert downgraded is False, "filter.authz.decision must be able to downgrade allow→deny"
    finally:
        # Remove ONLY our seam filter, restore the prior runtime, tear the loop down.
        bucket = bus._filters.get("filter.authz.decision")
        if bucket:
            bus._filters["filter.authz.decision"] = [
                t for t in bucket if t[1] != "_charz_authz_seam"]
        bus.set_runtime(prev_loop, prev_handler)
        loop.close()
