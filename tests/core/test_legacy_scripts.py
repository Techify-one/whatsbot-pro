"""Run each legacy script-style suite as a subprocess and assert exit code 0.

The legacy files run their assertions on import and ``sys.exit(1)`` on failure,
which is incompatible with pytest in-process collection. Their ``legacy_``
names keep them outside normal discovery; here each runs as a Python module in
a subprocess and must self-report green (exit 0).

Some files (test_plugin_events, test_runtime, test_subprocess, test_tool_runner)
expose BOTH ``def test_*`` functions AND a ``__main__``/``sys.exit`` runner;
running them as a subprocess exercises their ``__main__`` block, which is fine
and uniform with the rest.

Manual tools are intentionally excluded.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from tests.paths import PROJECT_ROOT

LEGACY_MODULES = [
    "tests.core.legacy.legacy_endpoints",
    "tests.core.legacy.legacy_agent_json_hardening",
    "tests.core.legacy.legacy_ai_agents_jsonb",
    "tests.core.legacy.legacy_audit",
    "tests.core.legacy.legacy_agent_routing",
    "tests.core.legacy.legacy_json_coercion",
    "tests.core.legacy.legacy_event_filters",
    "tests.core.legacy.legacy_gowa_alert_chat_migration",
    "tests.core.legacy.legacy_gowa_plugin_lifecycle",
    "tests.core.legacy.legacy_hooks",
    "tests.core.legacy.legacy_model_factory",
    "tests.core.legacy.legacy_plugin_events",
    "tests.core.legacy.legacy_quick_replies_edge",
    "tests.core.legacy.legacy_routing_engine",
    "tests.core.legacy.legacy_runtime",
    "tests.core.legacy.legacy_subprocess_service",
    "tests.core.legacy.legacy_tool_runner",
    "tests.core.legacy.legacy_dynamic_registry",
    "tests.core.legacy.legacy_media_caption",
]


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


@pytest.mark.legacy
@pytest.mark.parametrize("module_name", LEGACY_MODULES)
def test_legacy_script(module_name: str):
    proc = subprocess.run(
        [sys.executable, "-m", module_name],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(PROJECT_ROOT),
    )

    if proc.returncode != 0:
        pytest.fail(
            f"legacy suite {module_name} exited {proc.returncode}\n"
            f"--- stdout (tail) ---\n{_tail(proc.stdout)}\n"
            f"--- stderr (tail) ---\n{_tail(proc.stderr)}"
        )
