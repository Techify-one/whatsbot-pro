"""Shared pytest fixtures for the WhatsBot suite (Phase A0).

These fixtures reproduce the KNOWN-WORKING bootstrap that lives at the top of
``tests/test_endpoints.py`` (the long-standing script-style suite), so they are
green by construction. Later phases (A1, A2) consume them to convert the inline
``check()`` statements and other script-style suites into real pytest functions.

Key invariants copied verbatim from the legacy bootstrap:

* ``WHATSBOT_TEST=1`` is set *before* ``server.app`` is imported, so
  ``bootstrap_gowa_upgrade`` is a no-op and the suite never dirties
  ``storages/plugins``.
* The engine is initialized on a fresh temp SQLite DB, OR — when
  ``WHATSBOT_TEST_DB_URL`` is set — against that URL (Postgres in CI), running
  the Alembic upgrade exactly like ``db.connection._run_alembic_upgrade``.
* The GOWA client is a ``MagicMock`` whose lookup methods return *concrete*
  values (not bare Mocks) so contact persistence doesn't try to save a Mock.
* The app's lifespan is patched to a no-op to avoid background tasks.

NOTE on isolation: the engine is process-global (``db.engine``), so it is
initialized ONCE per session against a single temp DB. The ``seed`` fixture is
function-scoped and idempotent-ish: it (re)seeds the canonical dataset. Native
tests that only read the seed data are safe; tests that mutate should not assume
a pristine DB across the whole session. This mirrors the legacy script, which
seeds once and runs all assertions against that single DB.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is importable (mirrors test_endpoints.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load-bearing: must be set BEFORE server.app is imported anywhere.
os.environ.setdefault("WHATSBOT_TEST", "1")


# ── Legacy script-style files: never let pytest import them at collection ──
#
# Each of these runs work on import and calls sys.exit() at the end, which is
# incompatible with pytest collection. They are exercised as subprocesses by
# tests/test_legacy_suite.py instead. ``manual_cloud_api_test.py`` is a manual
# tool and is never run automatically.
collect_ignore = [
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
    "manual_cloud_api_test.py",
]
# NOTE: test_group_mentions.py was removed — its outgoing-mention coverage is a
# byte-exact subset of tests/characterization/test_group_mentions_characterization.py.


# ── Plugin test discovery (Phase G2) ───────────────────────────────────────
#
# Goal: let pytest collect tests that ship INSIDE a plugin
# (``storages/plugins/<id>/tests/test_*.py``) WHEN they exist, while staying a
# strict no-op in the common case (CI / fresh checkout) where
# ``storages/plugins/`` is empty (``WHATSBOT_TEST=1`` makes bootstrap a no-op).
#
# Mechanism: ``pytest_configure`` globs the plugin test dirs and appends each to
# ``config.args`` (the collection roots) — but ONLY when pytest is running off
# its ``testpaths`` (no explicit file/dir given on the CLI). That guard is the
# reason it never pollutes a focused run: ``./venv/bin/pytest tests/foo.py`` has
# ``args_source == ARGS`` and is left untouched; only the bare
# ``./venv/bin/pytest`` (``args_source == TESTPATHS``) gets the plugin dirs.
#
# Why it's a no-op when empty: ``_discover_plugin_test_dirs`` returns ``[]`` when
# ``storages/plugins`` is absent, has no plugin folders, or no plugin has a
# ``tests/`` dir containing a ``test_*.py`` — so nothing is appended and
# collection is byte-for-byte what it was before.
#
# Guards:
#   * skip dotfiles and ``__pycache__`` folders;
#   * require a plugin manifest (``plugin.yaml``/``plugin.yml``) so a stray dir
#     is never treated as a plugin (a broken/half-deleted folder is ignored, not
#     imported);
#   * require at least one ``test_*.py`` (an empty ``tests/`` or one holding only
#     ``__pycache__`` contributes nothing);
#   * name-collision safety: two plugins can both ship ``tests/test_routes.py``.
#     Under pytest's default ``prepend`` import mode that would raise
#     "import file mismatch". We give each discovered ``tests/`` dir a package
#     chain (auto-create ``<id>/__init__.py`` + ``<id>/tests/__init__.py`` when
#     missing) so the module name becomes ``<id>.tests.test_routes`` — unique per
#     plugin. The writes only ever touch a plugin that ALREADY ships a ``tests/``
#     dir with real tests (never the empty CI tree), and ``__init__.py`` is the
#     standard, expected marker for a Python test package.

_PLUGIN_MANIFEST_NAMES = ("plugin.yaml", "plugin.yml")


def _plugins_root() -> Path:
    """``storages/plugins`` under the project root (may not exist)."""
    return PROJECT_ROOT / "storages" / "plugins"


def _discover_plugin_test_dirs(plugins_root: Path) -> list[Path]:
    """Return ``storages/plugins/<id>/tests`` dirs that hold a ``test_*.py``.

    Pure + side-effect free; returns ``[]`` for a missing/empty tree so the
    caller is a guaranteed no-op in CI.
    """
    if not plugins_root.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(plugins_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        # Must look like a real plugin (has a manifest), else ignore the folder.
        if not any((child / name).is_file() for name in _PLUGIN_MANIFEST_NAMES):
            continue
        tests_dir = child / "tests"
        if not tests_dir.is_dir():
            continue
        if not any(tests_dir.glob("test_*.py")):
            continue
        found.append(tests_dir)
    return found


def _ensure_pkg_init(tests_dir: Path) -> None:
    """Give ``<id>/tests`` a package chain so module names are plugin-unique.

    Creates ``<id>/__init__.py`` and ``<id>/tests/__init__.py`` when absent so
    ``test_routes.py`` imports as ``<id>.tests.test_routes`` (no cross-plugin
    basename collision under ``prepend`` mode). Best-effort: a read-only tree is
    tolerated (collection still works for non-colliding basenames).
    """
    plugin_dir = tests_dir.parent
    for init_path in (plugin_dir / "__init__.py", tests_dir / "__init__.py"):
        if not init_path.exists():
            try:
                init_path.write_text("", encoding="utf-8")
            except OSError:
                pass


def pytest_configure(config) -> None:
    """Append discovered plugin test dirs to the collection roots (no-op when empty)."""
    from _pytest.config import Config

    # Only when running off ``testpaths`` (bare invocation). An explicit path on
    # the CLI (``args_source == ARGS``) is left exactly as the user asked.
    if getattr(config, "args_source", None) != Config.ArgsSource.TESTPATHS:
        return

    existing = {str(Path(a).resolve()) for a in config.args}
    for tests_dir in _discover_plugin_test_dirs(_plugins_root()):
        _ensure_pkg_init(tests_dir)
        resolved = str(tests_dir.resolve())
        if resolved not in existing:
            config.args.append(str(tests_dir))
            existing.add(resolved)


# ── Engine bootstrap (session-scoped, once per process) ────────────────────

@pytest.fixture(scope="session")
def _engine_ready() -> Path | None:
    """Initialize the global engine on a temp SQLite DB (or WHATSBOT_TEST_DB_URL).

    Returns the SQLite db path when used, else None (Postgres leg).
    """
    from db import init_db, init_engine

    test_url = os.environ.get("WHATSBOT_TEST_DB_URL", "").strip()
    if test_url:
        init_engine(test_url)
        from db.connection import _run_alembic_upgrade

        _run_alembic_upgrade()
        return None

    tmpdir = tempfile.mkdtemp(prefix="whatsbot_pytest_")
    db_path = Path(tmpdir) / "whatsbot.db"
    init_db(db_path)
    return db_path


# ── Canonical seed data (mirrors test_endpoints._seed_data) ────────────────

def _seed_data() -> None:
    """Insert the canonical test contacts/messages/tags/usage into the DB.

    Verbatim port of ``tests/test_endpoints.py`` ``_seed_data()`` so any test
    reusing this fixture sees identical data to the legacy script.
    """
    from db.repositories import contact_repo, message_repo, usage_repo, tag_repo

    now = time.time()

    c1 = contact_repo.get_or_create("5511999990001")
    contact_repo.update(
        c1["id"], name="Alice Test", email="alice@test.com",
        profession="Engineer", company="TestCo",
    )

    c2 = contact_repo.get_or_create("5511999990002")
    contact_repo.update(c2["id"], name="Bob Test", is_archived=True)

    message_repo.add(c1["id"], "user", "Olá, tudo bem?", ts=now - 100)
    message_repo.add(c1["id"], "assistant", "Tudo sim! Como posso ajudar?", ts=now - 90)
    message_repo.add(c1["id"], "user", "Qual o horário de funcionamento?", ts=now - 50)
    message_repo.add(c1["id"], "assistant", "Nosso horário é de 9h às 18h.", ts=now - 40)

    message_repo.add(c2["id"], "user", "Oi", ts=now - 200)

    contact_repo.add_observation(c1["id"], "Cliente VIP")

    tag_repo.create("vip", "#ff0000")
    tag_repo.create("lead", "#00ff00")
    tag_repo.add_contact_tag(c1["id"], "vip")

    usage_repo.add(c1["id"], "text", "openai/gpt-4o-mini", 100, 50, 150, 0.001)
    usage_repo.add(c1["id"], "text", "openai/gpt-4o-mini", 200, 80, 280, 0.002)

    contact_repo.increment_unread(c2["id"], "msg_001")


@pytest.fixture
def seed(_engine_ready):
    """Ensure the canonical dataset exists. Best-effort idempotent.

    ``tag_repo.create`` returns False if the tag already exists and
    ``get_or_create`` is naturally idempotent, so re-running across tests within
    a session is safe enough for read-oriented assertions.
    """
    _seed_data()
    yield


# ── Mocked GOWA client (concrete return values) ────────────────────────────

@pytest.fixture
def mock_gowa_client() -> MagicMock:
    """A MagicMock GOWA client matching the legacy bootstrap.

    Lookup methods return concrete strings/bools (not bare Mocks) so contact
    persistence in ``parse_gowa_inbound`` doesn't try to save a Mock.
    """
    client = MagicMock()
    for method in (
        "send_message", "send_image", "send_audio", "send_file",
        "send_chat_presence", "mark_as_read", "revoke_message",
        "delete_message", "react_to_message", "reconnect", "logout",
    ):
        setattr(client, method, MagicMock(return_value=None))
    client.get_own_number = MagicMock(return_value="5511999990001")
    client.get_group_name = MagicMock(return_value="Grupo Teste")
    client.can_bot_send_in_group = MagicMock(return_value=True)
    client.is_chat_archived = MagicMock(return_value=False)
    client.get_message_filename = MagicMock(return_value="")
    return client


@pytest.fixture
def mock_gowa_manager() -> MagicMock:
    return MagicMock()


# ── App + client ───────────────────────────────────────────────────────────

@pytest.fixture
def app(_engine_ready, seed, mock_gowa_manager, mock_gowa_client):
    """Build the FastAPI app via create_app, with a no-op lifespan."""
    from config.settings import Settings
    from agent.handler import AgentHandler
    from server.app import create_app

    settings = Settings()
    agent_handler = AgentHandler(api_key="test-key-fake", max_context_messages=10)

    application = create_app(
        settings=settings,
        gowa_manager=mock_gowa_manager,
        gowa_client=mock_gowa_client,
        agent_handler=agent_handler,
    )

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    application.router.lifespan_context = _noop_lifespan
    return application


@pytest.fixture
def client(app):
    """TestClient over the app.

    NEW pytest fixtures use ``raise_server_exceptions=True`` (per the plan) so
    server-side exceptions surface as test failures. The legacy script keeps
    ``False`` and is run separately as a subprocess.
    """
    from starlette.testclient import TestClient

    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


# ── Hermetic app builder (Phase G1-min) ─────────────────────────────────────

@pytest.fixture
def build_app(_engine_ready):
    """Factory fixture wrapping :func:`tests.support.build_test_app`.

    Boots a hermetic app with a CHOSEN set of bundled plugins (default
    ``("gowa",)``) — see ``tests/support.py`` for the shared-engine contract.
    Depends on ``_engine_ready`` so the process-global DB is initialized before
    the first build. Every app created via the factory is torn down (TestClient
    exited) at the end of the test.

    Usage::

        def test_x(build_app):
            built = build_app(["gowa"])
            r = built.client.post("/api/webhook/gowa/default", json=...)
    """
    from tests.support import build_test_app

    built_apps = []

    def _factory(plugins=("gowa",), **kwargs):
        built = build_test_app(plugins, **kwargs)
        built_apps.append(built)
        return built

    yield _factory

    for built in built_apps:
        try:
            built.client.__exit__(None, None, None)
        except Exception:
            pass
        try:
            if built._tmp is not None:
                built._tmp.cleanup()
        except Exception:
            pass


# ── Real-app-with-plugin fixture (Phase G2) ─────────────────────────────────

@pytest.fixture
def plugin_app(_engine_ready):
    """Factory: boot the REAL app with ONE plugin enabled, for plugin tests.

    Lets a plugin's own test (``storages/plugins/<id>/tests/test_*.py``, now
    collected — see ``pytest_configure`` above) hit ``/api/plugins/<id>/...`` and
    assert filter/event behavior against the live app, with no boilerplate::

        def test_my_route(plugin_app):
            built = plugin_app("telegram")
            r = built.client.get("/api/plugins/telegram/channels")
            assert r.json()["ok"] is True

    Wraps :func:`tests.support.build_test_app_with_plugin`. Depends on
    ``_engine_ready`` (process-global DB) like ``build_app``; every app it
    creates is torn down (TestClient exited, tmp dir cleaned) at test end.
    """
    from tests.support import build_test_app_with_plugin

    built_apps = []

    def _factory(plugin_id: str = "gowa", **kwargs):
        built = build_test_app_with_plugin(plugin_id, **kwargs)
        built_apps.append(built)
        return built

    yield _factory

    for built in built_apps:
        try:
            built.client.__exit__(None, None, None)
        except Exception:
            pass
        try:
            if built._tmp is not None:
                built._tmp.cleanup()
        except Exception:
            pass
