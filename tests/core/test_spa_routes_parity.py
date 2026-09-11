"""Parity guard: every client-side route must be served on a hard reload.

The panel is a client-side-routed SPA (``history.pushState``) and the backend has
NO catch-all: a page URL only survives a direct hit / F5 if the server registered
it explicitly. That registration used to live in TWO hand-maintained lists in
``server/app.py`` (the auth-exemption set and a stack of ``@app.get`` decorators),
and they drifted: ``/api-keys`` was exempt but never registered and ``/sounds``
was in neither, so both answered ``{"detail":"Not Found"}`` on reload while
working fine through in-app navigation.

Both now derive from the single ``_CORE_SPA_PATHS`` tuple; this test asserts that
tuple keeps up with the frontend route table in ``routing.js``.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROUTING_JS = PROJECT_ROOT / "web" / "static" / "js" / "components" / "shell" / "routing.js"

_BLOCK_RE = r"export const %s = \{(.*?)\n\};"
# Keys of CORE_ROUTES are quoted paths; values of CORE_TAB_PATHS are quoted paths.
_KEY_RE = re.compile(r"^\s*'(/[^']*)'\s*:", re.MULTILINE)
_VALUE_RE = re.compile(r":\s*'(/[^']*)'\s*,", re.MULTILINE)


def _block(source: str, name: str) -> str:
    match = re.search(_BLOCK_RE % name, source, re.DOTALL)
    assert match, f"{name} not found in {ROUTING_JS} — did the router move?"
    return match.group(1)


def frontend_paths() -> set[str]:
    """Every fixed pathname the client router can be sitting on."""
    source = ROUTING_JS.read_text(encoding="utf-8")
    routes = _block(source, "CORE_ROUTES")
    tab_paths = _block(source, "CORE_TAB_PATHS")
    return set(_KEY_RE.findall(routes)) | set(_VALUE_RE.findall(tab_paths))


def test_routing_js_is_parsable():
    # Guards the guard: a silently-empty parse would make the test below vacuous.
    paths = frontend_paths()
    assert "/api-keys" in paths and "/sounds" in paths
    assert len(paths) >= 15


def test_every_client_route_is_served_on_reload(app):
    registered = {getattr(route, "path", None) for route in app.routes}
    missing = sorted(p for p in frontend_paths() if p not in registered)
    assert not missing, (
        f"These SPA paths 404 on direct access / F5: {missing}. "
        "Add them to _CORE_SPA_PATHS in server/app.py."
    )


def test_every_client_route_is_auth_exempt(app):
    exempt = app.state.spa_paths
    missing = sorted(p for p in frontend_paths() if p not in exempt)
    assert not missing, (
        f"These SPA paths are registered but not auth-exempt: {missing}. "
        "Add them to _CORE_SPA_PATHS in server/app.py."
    )
