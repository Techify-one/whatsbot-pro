"""Run each legacy script-style suite as a subprocess and assert exit code 0.

The legacy files run their assertions on import and ``sys.exit(1)`` on failure,
which is incompatible with pytest in-process collection (so conftest.py ignores
them). Here we run each one as ``python <file>`` in a subprocess and assert it
self-reports green (exit 0). This gives an immediate PR gate over the ENTIRE
existing behavior while later phases convert them to native tests.

Some files (test_plugin_events, test_runtime, test_subprocess, test_tool_runner)
expose BOTH ``def test_*`` functions AND a ``__main__``/``sys.exit`` runner;
running them as a subprocess exercises their ``__main__`` block, which is fine
and uniform with the rest.

``manual_cloud_api_test.py`` is a manual tool and is intentionally excluded.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

# Same list as conftest.collect_ignore, MINUS manual_cloud_api_test.py.
LEGACY_FILES = [
    "test_endpoints.py",
    "test_audit.py",
    "test_agent_routing.py",
    "test_events_filters.py",
    "test_gowa_plugin.py",
    "test_hooks.py",
    "test_model_factory.py",
    "test_plugin_events.py",
    "test_quick_replies_edge.py",
    "test_routing_engine.py",
    "test_runtime.py",
    "test_subprocess.py",
    "test_tool_runner.py",
    "test_dynamic_registry.py",
    "test_media_caption.py",
]


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


@pytest.mark.legacy
@pytest.mark.parametrize("filename", LEGACY_FILES)
def test_legacy_script(filename: str):
    path = TESTS_DIR / filename
    assert path.exists(), f"legacy test file missing: {path}"

    proc = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(REPO_ROOT),
    )

    if proc.returncode != 0:
        pytest.fail(
            f"legacy suite {filename} exited {proc.returncode}\n"
            f"--- stdout (tail) ---\n{_tail(proc.stdout)}\n"
            f"--- stderr (tail) ---\n{_tail(proc.stderr)}"
        )
