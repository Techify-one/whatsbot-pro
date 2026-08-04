"""Shared pytest fixtures for the WhatsBot core and plugin contract suites.

These fixtures reproduce the KNOWN-WORKING bootstrap that lives at the top of
``tests/core/legacy/legacy_endpoints.py`` (the long-standing script-style suite),
so they preserve the same bootstrap while script-style suites are converted to
regular pytest functions.

Key invariants copied verbatim from the legacy bootstrap:

* ``WHATSBOT_TEST=1`` is set *before* ``server.app`` is imported, so
  ``bootstrap_gowa_upgrade`` is a no-op and the suite never dirties
  ``storages/plugins``.
* The engine is initialized against the Postgres TEST database
  (``WHATSBOT_TEST_DB_URL``, resolved by ``tests.pg`` — schema reset once per
  session + Alembic head). Postgres-only (plano 29 C3): no SQLite leg.
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
import uuid
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


# Legacy script-style suites use ``legacy_*.py`` under ``tests/core/legacy`` and
# are executed as subprocesses by ``tests/core/test_legacy_scripts.py``. Their
# names keep them out of normal pytest discovery without a duplicated ignore
# list.
# NOTE: the former duplicate group-mentions suite was removed; its outgoing
# coverage is a byte-exact subset of
# tests/core/characterization/test_group_mentions.py.


# Plugin tests intentionally are not discovered from ``storages/plugins``.
# They are development assets in the sibling ``whatsbot-pro-plugins``
# repository and run only through that repository's explicit test command.
# Installing or updating a production ZIP therefore never writes test package
# markers and never changes the core pytest collection.


# ── Engine bootstrap (session-scoped, once per process) ────────────────────

@pytest.fixture(scope="session")
def _engine_ready() -> None:
    """Initialize the global engine on the Postgres TEST database (plano 29 C3).

    Requires ``WHATSBOT_TEST_DB_URL`` (env, or a line in the repo-root ``.env``).
    The schema is DROPPED and recreated once per session, then migrated to head —
    the Postgres equivalent of the old per-process temp SQLite. No SQLite leg.
    """
    from tests.pg import init_test_engine

    init_test_engine(reset=True)
    return None


# ── Canonical seed data (mirrors test_endpoints._seed_data) ────────────────

def _seed_data() -> None:
    """Insert the canonical test contacts/messages/tags/usage into the DB.

    Verbatim port of ``tests/core/legacy/legacy_endpoints.py`` ``_seed_data()``
    so any test
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
    agent_handler = AgentHandler(
        api_key="test-key-fake", max_context_messages=10,
        default_ai_enabled=settings.get("default_ai_enabled", True))

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


@pytest.fixture
def authenticated_admin(_engine_ready):
    """Authenticate a test client as an isolated admin and tear it down.

    Plugin integration tests share one process-global database. Creating a user
    closes the formerly-open API for every later test, so ad-hoc auth helpers that
    leave users/sessions behind make results depend on collection order. This
    factory owns every session it creates and removes a user only when it created
    that row itself.

    Usage::

        admin = authenticated_admin(built.client)
        assert admin["is_admin"] is True
    """
    from db.repositories import session_repo, user_repo
    from server.auth import generate_session_token

    cleanup: list[tuple[str, int, bool, object, str | None]] = []

    def _authenticate(client, *, email: str | None = None, name: str = "Test Admin"):
        address = email or f"plugin-admin-{uuid.uuid4().hex}@test.local"
        user = user_repo.get_by_email(address)
        created = user is None
        if user is None:
            user = user_repo.create(
                email=address,
                name=name,
                password_hash="test-only",
                role_keys=["admin"],
            )
        elif not user.get("is_admin"):
            raise ValueError(
                f"authenticated_admin: existing user {address!r} is not an admin"
            )
        token = generate_session_token()
        session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
        previous = client.headers.get("Authorization")
        client.headers["Authorization"] = f"Bearer {token}"
        cleanup.append((token, user["id"], created, client, previous))
        return user

    yield _authenticate

    for token, user_id, created, client_obj, previous in reversed(cleanup):
        session_repo.delete(token)
        if previous is None:
            client_obj.headers.pop("Authorization", None)
        else:
            client_obj.headers["Authorization"] = previous
        if created:
            assert user_repo.delete(user_id), (
                f"authenticated_admin: could not remove test user {user_id}"
            )


# ── Hermetic app builder (Phase G1-min) ─────────────────────────────────────

@pytest.fixture
def build_app(_engine_ready):
    """Factory fixture wrapping :func:`tests.support.build_test_app`.

    Boots a hermetic app with a CHOSEN set of source/installed/explicit plugins
    (default ``("gowa",)``) — see ``tests/support.py`` for the shared-engine contract.
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

    for built in reversed(built_apps):
        built.close()


# ── Real-app-with-plugin fixture (Phase G2) ─────────────────────────────────

@pytest.fixture
def plugin_app(_engine_ready):
    """Factory: boot the REAL app with ONE plugin enabled, for plugin tests.

    Lets a plugin test from the sibling ``whatsbot-pro-plugins`` repository hit
    ``/api/plugins/<id>/...`` and assert filter/event behavior against the live
    app, with no boilerplate::

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

    for built in reversed(built_apps):
        built.close()
