"""Self-proof for Phase G2 plugin-test fixtures.

Two things are proven here:

1. **Discovery** — pytest, run off ``testpaths`` (bare invocation), collects and
   runs tests that live INSIDE a plugin at ``storages/plugins/<id>/tests/``. We
   prove it the honest way: drop a throwaway plugin (manifest + a trivial
   ``tests/test_*.py``) into the REAL ``storages/plugins/`` tree, run pytest as a
   subprocess using bare pytest discovery plus a unique ``-k`` selector, assert
   the embedded file is collected + passes, then
   remove the temp plugin. Nothing permanent is left behind.

   We also prove the discovery function is a strict no-op for a genuinely empty
   plugin root. The real local ``storages/plugins`` is not assumed empty: an
   operator may legitimately have plugins with embedded tests installed.

2. **Real-app-with-plugin** — the ``plugin_app`` fixture boots the live app with
   one selected source plugin enabled, so a plugin test can POST/GET
   ``/api/plugins/<id>/...`` against the real server. We hit the workbench
   ``telegram`` plugin's ``GET /channels`` route as the demonstration.

All of this is hermetic: the temp plugin lives in a uniquely-named folder, is
removed in a ``finally``, and the subprocess inherits ``WHATSBOT_TEST=1`` so
plugin bootstrap stays a no-op.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_ROOT = PROJECT_ROOT / "storages" / "plugins"


# ── 1) Discovery: a plugin's own tests get collected + run ──────────────────

_PLUGIN_MANIFEST = """\
id: {pid}
name: G2 Discovery Probe
version: 1.0.0
whatsbot_api_version: ">=1.0,<2.0"
description: Throwaway plugin used only by tests/test_plugin_test_discovery.py.
author: tests
"""

_PLUGIN_TEST = """\
def test_inside_plugin_is_collected():
    # Proves pytest reached a test shipped INSIDE storages/plugins/<id>/tests/.
    assert 1 + 1 == 2
"""


def _make_temp_plugin(plugins_root: Path) -> Path:
    """Create a minimal plugin (manifest + tests/test_*.py) under ``plugins_root``.

    Returns the plugin dir (caller is responsible for removing it).
    """
    pid = f"g2probe_{uuid.uuid4().hex[:8]}"
    plugin_dir = plugins_root / pid
    (plugin_dir / "tests").mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        _PLUGIN_MANIFEST.format(pid=pid), encoding="utf-8")
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "tests" / "test_g2_probe.py").write_text(
        _PLUGIN_TEST, encoding="utf-8")
    return plugin_dir


def _run_pytest(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["WHATSBOT_TEST"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def test_plugin_tests_dir_is_collected_and_run():
    """A throwaway plugin's ``tests/test_*.py`` is collected + passes, then gone."""
    PLUGINS_ROOT.mkdir(parents=True, exist_ok=True)
    plugin_dir = _make_temp_plugin(PLUGINS_ROOT)
    test_file = plugin_dir / "tests" / "test_g2_probe.py"
    try:
        # Bare invocation (testpaths mode) + a -k that ONLY matches the probe, so
        # we run the plugin test without re-running the whole suite. The conftest
        # hook only fires under args_source==TESTPATHS, which a bare invocation is.
        # ``-rN`` forces a one-line "N passed" summary even under the repo's
        # ``addopts = "-q -ra"`` (which otherwise collapses it).
        proc = _run_pytest("-rN", "-k", "test_inside_plugin_is_collected")
        combined = proc.stdout + "\n" + proc.stderr
        assert proc.returncode == 0, (
            "pytest did not exit 0 collecting the plugin test:\n" + combined)
        # The plugin test must have actually been selected + RUN to a pass — not
        # silently deselected to 0 items (that would also exit 0).
        assert "1 passed" in combined, (
            "plugin test was not collected/run:\n" + combined)
        assert "no tests ran" not in combined, (
            "plugin test was deselected, not collected:\n" + combined)

        # And prove the *path* under storages/plugins was the one collected.
        proc2 = _run_pytest("--collect-only", "-q",
                            "-k", "test_inside_plugin_is_collected")
        combined2 = proc2.stdout + "\n" + proc2.stderr
        assert proc2.returncode == 0, combined2
        assert "storages/plugins" in combined2 and "test_g2_probe.py" in combined2, (
            "collected node id did not come from storages/plugins:\n" + combined2)
    finally:
        import shutil
        shutil.rmtree(plugin_dir, ignore_errors=True)

    # Cleanup is real: the plugin dir is gone.
    assert not plugin_dir.exists()


def test_collection_is_noop_when_no_plugin_tests(tmp_path):
    """A missing or empty plugin root contributes no collection directories."""
    from tests.conftest import _discover_plugin_test_dirs

    # Sanity: no leftover probe plugin from the test above (or anywhere).
    assert not any(PLUGINS_ROOT.glob("g2probe_*")), (
        "a leftover g2probe_* plugin would taint this no-op assertion")
    missing_root = tmp_path / "missing"
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    assert _discover_plugin_test_dirs(missing_root) == []
    assert _discover_plugin_test_dirs(empty_root) == []


# ── 2) Real-app-with-plugin: hit a plugin route against the live app ────────

def test_plugin_app_fixture_boots_and_serves_plugin_route(
    plugin_app,
    authenticated_admin,
):
    """`plugin_app('telegram')` boots the real app + serves the plugin's route."""
    built = plugin_app("telegram")
    authenticated_admin(built.client)

    # The plugin's router is mounted under /api/plugins/<id>.
    r = built.client.get("/api/plugins/telegram/channels")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["data"], list)

    # And the production registry, not merely the requested-id tuple, reports it loaded.
    assert "telegram" in built.app.state.deps.plugins_registry.loaded
