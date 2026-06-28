"""Hermetic app builder for characterization & plugin-lifecycle tests.

``build_test_app(plugins=[...])`` boots a real WhatsBot FastAPI app with a
CHOSEN set of bundled plugins, so a test can exercise, e.g., only ``gowa`` (the
default-channel webhook → ingest → batch → reply pipeline that A2 characterizes)
or ``gowa`` + ``telegram`` (multi-channel / C4 plugin lifecycle).

Why this exists (and how it differs from the ``app``/``client`` fixtures in
``conftest.py``): those fixtures build the app against the REAL project tree, so
``create_app``'s internal ``plugins_dir = settings.data_dir/storages/plugins``
discovers whatever happens to be checked out, and no bundled plugin is enabled
(``discover_and_load`` reads ``plugins.enabled`` from the DB, which is 0 for
every bundled plugin in the suite). ``build_test_app`` makes the PLUGINS surface
hermetic and deterministic:

* a tmp ``data_dir`` whose ``storages/plugins/`` contains EXACTLY the requested
  plugin folders (copied from the repo's real ``assets/plugin_examples/<id>/``),
  so discovery finds exactly those;
* a tmp ``assets/plugin_examples/`` holding ONLY the requested folders, so
  ``bootstrap_initial_plugins`` (which copies missing examples) can never
  repopulate an unexpected plugin;
* each requested plugin's ``plugins`` row marked ``enabled=1`` BEFORE
  ``create_app`` runs ``discover_and_load`` (which gates loading on that flag) —
  this is how the suite/``test_gowa_plugin`` would enable a plugin (via
  ``plugin_repo.upsert(id, ver, enabled=True)``);
* the app's lifespan replaced with a no-op (no GOWA subprocess, no background
  tasks) — same as ``conftest``.

SHARED DB / ENGINE — IMPORTANT
------------------------------
The DB engine is a PROCESS-GLOBAL singleton (``db.engine``), initialized ONCE
per session by the ``_engine_ready`` session fixture against a single temp
SQLite DB (or ``WHATSBOT_TEST_DB_URL``). ``build_test_app`` **reuses that already
-initialized global engine** — it does NOT create a per-app DB. The hermeticity
here is about the PLUGINS dir + ``data_dir``, NOT DB isolation: every built app
reads/writes the same shared DB the rest of the suite uses (the ``default``
channel seeded by migration, contacts, config, the ``plugins`` table, …). That
is deliberate — characterization needs the real schema + seeded ``default``
channel, and per-app DBs would re-pay Alembic on every build. Tests that mutate
config/contacts should pick distinct phone numbers, exactly like the rest of the
suite. ``build_test_app`` requires the engine to be ready; call it from a test
that depends (directly or transitively) on the ``_engine_ready`` fixture — the
``build_app`` fixture in ``conftest`` wires that dependency for you.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_PLUGIN_EXAMPLES = PROJECT_ROOT / "assets" / "plugin_examples"


@dataclass
class BuiltApp:
    """Everything a characterization test needs from a hermetic app build."""
    app: Any
    client: Any                 # starlette TestClient (raise_server_exceptions=True)
    gowa_client: Any            # FakeGowaClient (or the one passed in)
    agent_handler: Any
    settings: Any
    data_dir: Path
    plugins: tuple[str, ...]
    _tmp: Any = field(default=None, repr=False)  # TemporaryDirectory, kept alive


def _copy_plugin(plugin_id: str, dest_parent: Path) -> Path:
    """Copy ``assets/plugin_examples/<id>/`` into ``dest_parent/<id>`` (no caches)."""
    src = REAL_PLUGIN_EXAMPLES / plugin_id
    if not src.is_dir():
        raise ValueError(
            f"build_test_app: unknown bundled plugin {plugin_id!r} "
            f"(not found in {REAL_PLUGIN_EXAMPLES})")
    dest = dest_parent / plugin_id
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def build_test_app(
    plugins: Iterable[str] = ("gowa",),
    *,
    settings_overrides: Optional[dict] = None,
    gowa_client: Any = None,
) -> BuiltApp:
    """Build a hermetic app with exactly ``plugins`` loaded + enabled.

    Args:
        plugins: bundled plugin ids to copy + enable (default ``("gowa",)``).
            Each must exist under ``assets/plugin_examples/<id>/``.
        settings_overrides: optional ``{config_key: value}`` written via
            ``settings.set`` after construction (e.g. ``{"auto_reply": True}``).
            NOTE: config lives in the SHARED DB — overrides persist for the
            session unless the test resets them.
        gowa_client: a fake/mocked client to inject; defaults to a fresh
            :class:`tests.fakes.FakeGowaClient`.

    Returns:
        :class:`BuiltApp` — ``.app``, ``.client`` (TestClient with
        ``raise_server_exceptions=True``), ``.gowa_client``, ``.agent_handler``,
        ``.settings``, ``.data_dir``, ``.plugins``.

    The returned object keeps the backing ``TemporaryDirectory`` alive for its
    lifetime; let it be GC'd (or close ``built._tmp``) to clean up.
    """
    import tempfile
    from unittest.mock import MagicMock

    from config.settings import Settings
    from agent.handler import AgentHandler
    from db.repositories import plugin_repo
    from plugins.manifest import load_manifest
    from server.app import create_app
    from starlette.testclient import TestClient

    plugins = tuple(plugins)

    # 1) tmp data_dir scaffold: storages/plugins/<id> + assets/plugin_examples/<id>.
    tmp = tempfile.TemporaryDirectory(prefix="whatsbot_buildapp_")
    data_dir = Path(tmp.name)
    plugins_dir = data_dir / "storages" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    examples_dir = data_dir / "assets" / "plugin_examples"
    examples_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (data_dir / "statics").mkdir(parents=True, exist_ok=True)

    # 2) Copy ONLY the requested plugins into BOTH dirs, then enable each in the
    #    shared `plugins` table so discover_and_load (run inside create_app) loads
    #    them. The examples copy means bootstrap_initial_plugins can only ever
    #    re-add the requested set (never a surprise plugin from the real tree).
    for pid in plugins:
        _copy_plugin(pid, plugins_dir)
        _copy_plugin(pid, examples_dir)
        manifest = load_manifest(plugins_dir / pid)
        plugin_repo.upsert(manifest.id, manifest.version, enabled=True)

    # 3) Settings: construct (seeds DB defaults), then point its data_dir at the
    #    tmp scaffold. data_dir is a plain instance attribute used by create_app
    #    only for plugins_dir / examples_dir / statics — reassigning is safe and
    #    is what makes the plugins surface hermetic without a custom Settings API.
    settings = Settings()
    settings.data_dir = data_dir
    settings.logs_dir = data_dir / "logs"
    for key, value in (settings_overrides or {}).items():
        settings.set(key, value)

    # 4) Doubles: fake gowa client + a real AgentHandler (LLM never called unless
    #    a test opts in via fake_agent_reply or enables auto_reply with a stub).
    if gowa_client is None:
        from tests.fakes import FakeGowaClient
        gowa_client = FakeGowaClient()
    gowa_manager = MagicMock()
    agent_handler = AgentHandler(api_key="test-key-fake", max_context_messages=10)

    application = create_app(
        settings=settings,
        gowa_manager=gowa_manager,
        gowa_client=gowa_client,
        agent_handler=agent_handler,
    )

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    application.router.lifespan_context = _noop_lifespan

    client = TestClient(application, raise_server_exceptions=True)
    client.__enter__()

    return BuiltApp(
        app=application,
        client=client,
        gowa_client=gowa_client,
        agent_handler=agent_handler,
        settings=settings,
        data_dir=data_dir,
        plugins=plugins,
        _tmp=tmp,
    )
