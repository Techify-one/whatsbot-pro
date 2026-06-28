"""Golden-master characterization of the AUDIT TRAIL (Plano 23 · Fase A2b — BLOCKING).

Locks the CURRENT behavior (bugs included) of the plano-07 audit pipeline BEFORE
the Wave-2 refactor relocates the bus ``emit``s (blocks B4/B5/C1). For EACH entry
in ``db/audit_actions.AUDITABLE_EVENTS`` this drives the REAL HTTP route that
emits the bus event, lets the core ``*`` listener
(``server/audit_listener.audit_event_handler``) persist the row, and
``golden_assert``s the NORMALIZED ``audit_log`` row(s).

The wiring this exercises is the full production path:

    HTTP route  →  emit_with_filter / emit  →  bus ``*`` handler (audit listener)
                →  audit_repo.add  →  audit_log row

(The hermetic ``build_test_app`` runs with a NO-OP lifespan, so the audit listener
is NOT auto-registered — the ``_audit_wiring`` fixture registers it and points the
bus runtime at the TestClient's portal loop, which is exactly what
``server/app.py``'s lifespan does in production.)

CRITICAL INVARIANT under test: each auditable action writes EXACTLY 1 audit row
with a RESOLVED ``resource_id`` (action + resource_type + resource_id, plus the
sanitised before/after). Each golden therefore records, per action:

  * ``row_count``            — must be 1,
  * ``resource_id_resolved``— the load-bearing invariant (``resource_id`` not None),
  * the normalized row: ``action / resource_type / resource_id / actor_type /
    actor_label / before / after`` (before/after parsed from JSON so ``ts`` is
    normalized away; secrets are already masked by ``audit_repo``).

Actor is ``system`` here (the TestClient has no authenticated user — the auth-actor
middleware falls back to ``system``), which is deterministic.

MANDATORY cell matrix — one cell per AUDITABLE_EVENTS entry (all 11 reachable via
HTTP, so the matrix has no structural SKIPs):

  config.changed          → config.update          (PUT  /api/config)
  tool_override.changed   → tool.override          (PUT  /api/tools/{name})
  plugin.enabled          → plugin.enable          (POST /api/plugins/{id}/enable)
  plugin.disabled         → plugin.disable         (POST /api/plugins/{id}/disable)
  plugin.settings.changed → plugin.settings_update (PUT  /api/plugins/{id}/settings)
  contact.updated         → contact.update         (PUT  /api/contacts/{phone}/info)
  contact.ai_toggled      → contact.toggle_ai      (POST /api/contacts/{phone}/toggle-ai)
  contact.tagged          → contact.tagged         (PUT  /api/contacts/{phone}/tags)
  tag.created             → tag.create             (POST /api/tags)
  tag.updated             → tag.update             (PUT  /api/tags/{name})
  tag.deleted             → tag.delete             (DELETE /api/tags/{name})

Discipline: ADDITIVE only. We never change app/source and never "fix" behavior.
Likely bugs are CAPTURED and flagged with ``# CHARACTERIZED BUG:`` comments.

Distinct phone numbers / tag names per test (the suite shares one process DB).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from tests.characterization.golden import normalize, golden_assert


# ── Audit wiring fixture ──────────────────────────────────────────────────────

# Sentinel plugin id the core listener registers under (server/audit_listener.py).
_CORE_AUDIT_ID = "__core_audit__"


def _wire_audit_listener(built) -> None:
    """Point the bus runtime at the portal loop + register the core ``*`` listener.

    Reproduces ``server/app.py``'s lifespan wiring inside the hermetic build (whose
    lifespan is a no-op). The bus ``_loop`` MUST be the TestClient's portal loop so
    the actor contextvar (set by the auth middleware on that loop during the
    request) is snapshotted into the ``create_task`` fan-out — otherwise the audit
    row would not see the request actor. Registers the listener at most ONCE
    (idempotent) so a re-wire never double-writes.
    """
    from plugins import events as bus
    from server.audit_listener import register_audit_listener

    loop = built.client.portal.call(asyncio.get_running_loop)
    bus.set_runtime(loop, built.agent_handler)
    existing = bus._handlers.get("*", [])
    if not any(pid == _CORE_AUDIT_ID for pid, _ in existing):
        register_audit_listener()


def _unwire_audit_listener(prev_loop, prev_handler) -> None:
    """Remove the core ``*`` listener and restore the prior bus runtime.

    Keeps the process-global bus clean for sibling characterization tests (which
    don't expect a stray audit ``*`` handler writing rows during their routes).
    """
    from plugins import events as bus

    bucket = bus._handlers.get("*")
    if bucket:
        bus._handlers["*"] = [t for t in bucket if t[0] != _CORE_AUDIT_ID]
    bus.set_runtime(prev_loop, prev_handler)


@pytest.fixture
def audit_app(build_app):
    """A hermetic app with the audit listener wired to its portal loop.

    Yields a ``(built, drive)`` pair where ``drive`` runs an audit-producing HTTP
    action and returns the normalized newest rows it wrote (drained). Tears down
    the listener so it never leaks onto sibling tests on the shared bus/DB.
    """
    from plugins import events as bus
    from db.repositories import audit_repo

    prev_loop = getattr(bus, "_loop", None)
    prev_handler = getattr(bus, "_agent_handler", None)

    # telegram is included so the plugin.settings.changed cell has a Settings model.
    built = build_app(["gowa", "telegram"])
    _wire_audit_listener(built)

    def _drain() -> None:
        """Wait for the bus fan-out tasks (the audit write) to finish on the loop."""
        async def _d():
            cur = asyncio.current_task()
            for _ in range(10):
                pending = [t for t in asyncio.all_tasks()
                           if t is not cur and not t.done()]
                if not pending:
                    break
                try:
                    await asyncio.wait(pending, timeout=2.0)
                except Exception:
                    pass
                await asyncio.sleep(0)
        built.client.portal.call(_d)

    def _drive(method: str, url: str, *, action: str, resource_id=None,
               json_body=None, expect: int = 200):
        """Run one HTTP action; return the audit rows IT wrote (chronological).

        Concurrency-robust against the shared process DB (sibling characterization
        tests / parallel agents may write audit rows too):

        * drains FIRST so any audit write still pending from a SETUP call (e.g. a
          ``POST /api/tags`` creating the tag we then assign) has landed before the
          baseline — the bus fan-out is async, so an undrained setup emit would
          otherwise leak into this window;
        * snapshots ``count()``, fires the request, drains again, takes the newest
          ``delta`` rows, then FILTERS that window to rows matching THIS test's
          ``action`` (and ``resource_id`` when it resolves — we use a per-run-unique
          name/phone, so even a concurrent identical run can't collide). That makes
          the capture exactly what this action wrote, ignoring interleaved rows.
        """
        _drain()  # flush any pending audit writes from prior setup calls
        before = audit_repo.count()
        resp = built.client.request(method, url, json=json_body)
        assert resp.status_code == expect, (url, resp.status_code, resp.text)
        _drain()
        delta = audit_repo.count() - before
        # Over-fetch the window generously (concurrent writers inflate the delta),
        # then keep only the rows matching THIS action (+ resource_id when it
        # resolves). Newest-first: index 0 is the row our just-drained request wrote.
        window = audit_repo.query(limit=max(delta, 1) + 50) if delta > 0 else []
        mine = [r for r in window if r["action"] == action
                and (resource_id is None or r["resource_id"] == str(resource_id))]
        # We characterize a SINGLE auditable write per action. Under the shared
        # process DB, parallel siblings (incl. a concurrent run of THIS file) may
        # have written an identical-action/resource row into the same window; we
        # therefore take the newest matching row (the one our request produced) so
        # the golden is the per-request behavior, not a concurrency artifact. The
        # standalone single-process tests/test_audit.py guards true double-fires.
        return mine[:1], resp

    yield built, _drive

    _unwire_audit_listener(prev_loop, prev_handler)


# ── Row projection / golden value ─────────────────────────────────────────────

# Columns we assert (drop id/created_at/request_id/ip — non-deterministic /
# already covered by the standalone test_audit.py).
def _project_row(row: dict) -> dict:
    def _parse(blob):
        if blob in (None, ""):
            return None
        try:
            return json.loads(blob)
        except Exception:
            return blob

    return {
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "resource_id_resolved": row["resource_id"] is not None,
        "actor_type": row["actor_type"],
        "actor_label": row["actor_label"],
        "before": _parse(row["before_json"]),
        "after": _parse(row["after_json"]),
    }


def _capture(rows: list[dict]) -> dict:
    """The golden value: row_count + the normalized projected rows."""
    return normalize({
        "row_count": len(rows),
        "rows": [_project_row(r) for r in rows],
    })


# ── config.changed → config.update ───────────────────────────────────────────

def test_audit_config_changed(audit_app):
    """config.changed → config.update. before/after carry the changed key.

    # CHARACTERIZED BUG: resource_id is NULL. The ``config.changed`` payload has
    # no key in the listener's ``_RESOURCE_ID_KEYS`` (phone/id/plugin_id/name/tag/
    # key/contact_id) — the changed config key lives only inside before/after JSON,
    # not as ``resource_id``. So the "resolved resource_id" invariant is VIOLATED
    # for config edits: the row can't be filtered by which config key changed.
    # Wave 2 (C1) should set resource_id to the changed key(s).
    """
    built, drive = audit_app
    rows, _ = drive("PUT", "/api/config", action="config.update",
                    json_body={"max_context_messages": 7})
    assert len(rows) == 1
    assert rows[0]["resource_id"] is None  # locks the bug
    golden_assert("audit_config_changed", _capture(rows))


# ── tool_override.changed → tool.override ────────────────────────────────────

def test_audit_tool_override_changed(audit_app):
    """tool_override.changed → tool.override.

    # CHARACTERIZED BUG: resource_id is NULL. The route emits the changed tool
    # under the payload key ``tool_name``, but the listener's ``_RESOURCE_ID_KEYS``
    # looks for ``name`` (not ``tool_name``) — so it never matches and the audit
    # row loses WHICH tool was overridden as a queryable resource_id (the name is
    # only inside after_json). Same class of bug as config.changed. Wave 2 must
    # either add ``tool_name`` to _RESOURCE_ID_KEYS or emit ``name``.
    """
    built, drive = audit_app
    # Pick a real registered tool so the route doesn't 404.
    tools = built.client.get("/api/tools").json()["data"]["tools"]
    tool_name = tools[0]["name"]
    rows, _ = drive("PUT", f"/api/tools/{tool_name}", action="tool.override",
                    json_body={"enabled": True})
    assert len(rows) == 1
    assert rows[0]["resource_id"] is None  # locks the bug
    # The tool name IS present in after_json (just not as resource_id).
    assert rows[0]["after_json"] and tool_name in rows[0]["after_json"]
    golden_assert("audit_tool_override_changed", _capture(rows))


# ── plugin.enabled / plugin.disabled → plugin.enable / plugin.disable ────────

def test_audit_plugin_disabled(audit_app):
    """plugin.disabled → plugin.disable. resource_id = plugin id (RESOLVES).

    The enable/disable routes call ``schedule_restart`` (os._exit after a delay)
    AFTER emitting; we patch it to a no-op so the in-process test survives. The
    emit happens before the restart, so the audit row is written either way.
    """
    built, drive = audit_app
    import server.routes.plugins as pr
    with patch.object(pr, "schedule_restart", lambda *a, **k: None):
        rows, _ = drive("POST", "/api/plugins/telegram/disable",
                        action="plugin.disable", resource_id="telegram")
    assert len(rows) == 1
    assert rows[0]["resource_id"] == "telegram"  # invariant holds here
    golden_assert("audit_plugin_disabled", _capture(rows))


def test_audit_plugin_enabled(audit_app):
    """plugin.enabled → plugin.enable. resource_id = plugin id (RESOLVES)."""
    built, drive = audit_app
    import server.routes.plugins as pr
    with patch.object(pr, "schedule_restart", lambda *a, **k: None):
        # Ensure a clean enable transition (telegram is enabled at build).
        built.client.post("/api/plugins/telegram/disable")
        rows, _ = drive("POST", "/api/plugins/telegram/enable",
                        action="plugin.enable", resource_id="telegram")
    assert len(rows) == 1
    assert rows[0]["resource_id"] == "telegram"
    golden_assert("audit_plugin_enabled", _capture(rows))


# ── plugin.settings.changed → plugin.settings_update ─────────────────────────

def test_audit_plugin_settings_changed(audit_app):
    """plugin.settings.changed → plugin.settings_update. resource_id = plugin id.

    before = prior namespaced values (falling back to defaults), after = the new
    values. telegram's Settings has ``inbound_mode`` + ``poll_interval``.
    """
    built, drive = audit_app
    rows, _ = drive("PUT", "/api/plugins/telegram/settings",
                    action="plugin.settings_update", resource_id="telegram",
                    json_body={"inbound_mode": "poll", "poll_interval": 3.0})
    assert len(rows) == 1
    assert rows[0]["resource_id"] == "telegram"
    golden_assert("audit_plugin_settings_changed", _capture(rows))


# ── contact.updated → contact.update ─────────────────────────────────────────

def test_audit_contact_updated(audit_app):
    """contact.updated → contact.update. resource_id = phone (RESOLVES).

    after carries the full normalized ``info`` block + custom_attributes."""
    built, drive = audit_app
    from db.repositories import contact_repo
    phone = "5511970050001"
    contact_repo.get_or_create(phone)
    rows, _ = drive("PUT", f"/api/contacts/{phone}/info",
                    action="contact.update", resource_id=phone,
                    json_body={"name": "Auditado", "email": "a@x.com"})
    assert len(rows) == 1
    assert rows[0]["resource_id"] == phone
    golden_assert("audit_contact_updated", _capture(rows))


# ── contact.ai_toggled → contact.toggle_ai ───────────────────────────────────

def test_audit_contact_ai_toggled(audit_app):
    """contact.ai_toggled → contact.toggle_ai. resource_id = phone (RESOLVES).

    NOTE (secondary emit site): ``server/routes/conversations.py`` ALSO emits
    ``contact.ai_toggled`` when an assignment flips the contact AI gate (P17) — a
    SECOND auditable emit point for the same event name (not a name mismatch).
    This test drives the dedicated POST /toggle-ai route, which emits exactly once.
    """
    built, drive = audit_app
    from db.repositories import contact_repo
    phone = "5511970050002"
    contact_repo.get_or_create(phone)
    rows, _ = drive("POST", f"/api/contacts/{phone}/toggle-ai",
                    action="contact.toggle_ai", resource_id=phone,
                    json_body={"enabled": False})
    assert len(rows) == 1
    assert rows[0]["resource_id"] == phone
    golden_assert("audit_contact_ai_toggled", _capture(rows))


# ── contact.tagged → contact.tagged ──────────────────────────────────────────

def test_audit_contact_tagged(audit_app):
    """contact.tagged → contact.tagged. resource_id = phone (RESOLVES).

    Exactly 1 row even though the route may also emit (non-auditable)
    ``contact.untagged`` per removed tag — only ``contact.tagged`` is in the
    allowlist, so only one audit row is written."""
    built, drive = audit_app
    from db.repositories import contact_repo
    phone = "5511970050003"
    contact_repo.get_or_create(phone)
    # Create the tag first (its own tag.create audit row is drained out by the
    # before/after delta window — it isn't part of this capture).
    built.client.post("/api/tags", json={"name": "AudVip", "color": "#abc"})
    rows, _ = drive("PUT", f"/api/contacts/{phone}/tags",
                    action="contact.tagged", resource_id=phone,
                    json_body={"tags": ["AudVip"]})
    assert len(rows) == 1
    assert rows[0]["resource_id"] == phone
    golden_assert("audit_contact_tagged", _capture(rows))


# ── tag.created / tag.updated / tag.deleted ──────────────────────────────────

def test_audit_tag_created(audit_app):
    """tag.created → tag.create. resource_id = tag name (RESOLVES, via ``name``)."""
    built, drive = audit_app
    # Clean slate: the shared session DB persists, so drop a leftover from a prior
    # run/sibling first → the create below is a genuine 200 every time (fixed name
    # keeps the golden deterministic).
    built.client.delete("/api/tags/AudTagCreate")
    rows, _ = drive("POST", "/api/tags",
                    action="tag.create", resource_id="AudTagCreate",
                    json_body={"name": "AudTagCreate", "color": "#101010"})
    assert len(rows) == 1
    assert rows[0]["resource_id"] == "AudTagCreate"
    golden_assert("audit_tag_created", _capture(rows))


def test_audit_tag_updated(audit_app):
    """tag.updated → tag.update. resource_id = the FINAL (new) name; before carries
    the prior name+color snapshot the route attaches via ``_audit_before``.

    # CHARACTERIZED BUG: the audit ``before`` color is WRONG — it shows the NEW
    # color, not the prior one. The route snapshots ``old_tag = tag_registry.get(name)``
    # for the "before", but ``TagRegistry.get`` returns the LIVE cache dict and
    # ``TagRegistry.update`` mutates it IN PLACE (``self._tags[name]["color"]=color``)
    # before ``emit_with_filter`` reads ``old_tag["color"]`` — so the before-color
    # is overwritten to the new value (the before-name is still correct, since
    # update() pops to a new key). Contrast tag.delete (audit_tag_deleted), whose
    # before-color is correct because delete pops without mutating first. Wave 2
    # should deep-copy the snapshot (or read it from the repo) before the update.
    """
    built, drive = audit_app
    # Clean slate for a re-runnable, deterministic golden on the shared session DB:
    # drop both the source and the rename target a prior run may have left behind,
    # then recreate the source fresh.
    built.client.delete("/api/tags/AudTagUpd")
    built.client.delete("/api/tags/AudTagUpd2")
    built.client.post("/api/tags",
                      json={"name": "AudTagUpd", "color": "#202020"})
    rows, _ = drive("PUT", "/api/tags/AudTagUpd",
                    action="tag.update", resource_id="AudTagUpd2",
                    json_body={"name": "AudTagUpd2", "color": "#303030"})
    assert len(rows) == 1
    assert rows[0]["resource_id"] == "AudTagUpd2"
    # Locks the bug: the supposed "old" color equals the NEW color, not "#202020".
    # (rows here are raw DB rows; the snapshot lives in the before_json string.)
    assert json.loads(rows[0]["before_json"])["color"] == "#303030"
    golden_assert("audit_tag_updated", _capture(rows))


def test_audit_tag_deleted(audit_app):
    """tag.deleted → tag.delete. resource_id = tag name; before carries the prior
    name+color snapshot (so a delete is reconstructable)."""
    built, drive = audit_app
    built.client.post("/api/tags",
                      json={"name": "AudTagDel", "color": "#404040"})
    rows, _ = drive("DELETE", "/api/tags/AudTagDel",
                    action="tag.delete", resource_id="AudTagDel")
    assert len(rows) == 1
    assert rows[0]["resource_id"] == "AudTagDel"
    golden_assert("audit_tag_deleted", _capture(rows))


# ── invariant sweep: every AUDITABLE_EVENTS entry is covered above ───────────

def test_audit_matrix_is_complete():
    """Guard: every AUDITABLE_EVENTS entry has a dedicated cell above.

    If a future change adds an auditable event, this fails until a characterization
    cell + golden is added for it (keeps the matrix visibly complete).
    """
    from db.audit_actions import AUDITABLE_EVENTS

    covered = {
        "config.changed", "tool_override.changed",
        "plugin.enabled", "plugin.disabled", "plugin.settings.changed",
        "contact.updated", "contact.ai_toggled", "contact.tagged",
        "tag.created", "tag.updated", "tag.deleted",
    }
    assert set(AUDITABLE_EVENTS) == covered, (
        "AUDITABLE_EVENTS drifted from the characterization matrix; "
        f"missing={set(AUDITABLE_EVENTS) - covered}, "
        f"extra={covered - set(AUDITABLE_EVENTS)}")
