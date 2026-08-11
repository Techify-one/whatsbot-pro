"""Hermetic app builder for characterization & plugin-lifecycle tests.

``build_test_app(plugins=[...])`` boots a real WhatsBot FastAPI app with a
CHOSEN set of source/installed/explicit plugins, so a test can exercise, e.g., only ``gowa`` (the
default-channel webhook → ingest → batch → reply pipeline that A2 characterizes)
or ``gowa`` + ``telegram`` (multi-channel / C4 plugin lifecycle). Tests may pass
``plugin_sources={"id": path}`` for synthetic/versioned fixtures that should go
through the exact same manifest discovery and loader path.

Why this exists (and how it differs from the ``app``/``client`` fixtures in
``conftest.py``): those fixtures build the app against the REAL project tree, so
``create_app``'s internal ``plugins_dir = settings.data_dir/storages/plugins``
discovers whatever happens to be checked out, and no selected plugin is enabled
(``discover_and_load`` reads ``plugins.enabled`` from the DB, which is normally 0
for source plugins in the suite). ``build_test_app`` makes the PLUGINS surface
hermetic and deterministic:

* a tmp ``data_dir`` whose ``storages/plugins/`` contains EXACTLY the requested
  plugin folders (resolved first from ``assets/plugin_examples/<id>/``, then
  from the installed ``storages/plugins/<id>/`` fallback), so discovery finds
  exactly those;
* a tmp ``assets/plugin_examples/`` holding ONLY the requested folders, so the
  GOWA-only bootstrap can never seed an unexpected plugin;
* each requested plugin's ``plugins`` row marked ``enabled=1`` BEFORE
  ``create_app`` runs ``discover_and_load`` (which gates loading on that flag) —
  this is how the suite/``test_gowa_plugin`` would enable a plugin (via
  ``plugin_repo.upsert(id, ver, enabled=True)``);
* the app's lifespan replaced with a no-op (no GOWA subprocess, no background
  tasks) — same as ``conftest``.

SHARED DB / ENGINE — IMPORTANT
------------------------------
The DB engine is a PROCESS-GLOBAL singleton (``db.engine``), initialized ONCE
per session by the ``_engine_ready`` session fixture against the Postgres TEST
database (``WHATSBOT_TEST_DB_URL`` via ``tests.pg`` — schema reset + Alembic
head; plano 29 C3). ``build_test_app`` **reuses that already-initialized global
engine** — it does NOT create a per-app DB. The hermeticity
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
import sys
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from tests.plugin_test_utils import (
    PluginSourceNotFound,
    plugin_source_candidates,
    purge_loaded_plugin_modules,
    resolve_plugin_source,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_PLUGIN_EXAMPLES = PROJECT_ROOT / "assets" / "plugin_examples"
REAL_INSTALLED_PLUGINS = PROJECT_ROOT / "storages" / "plugins"
_MISSING = object()


@dataclass(frozen=True)
class _PluginBusState:
    """Shallow snapshot of the process-global plugin bus (test harness only)."""

    handlers: dict[str, list]
    filters: dict[str, list]
    core_sync_listeners: list
    loop: Any
    agent_handler: Any


@dataclass(frozen=True)
class _PluginServicesState:
    """Shallow snapshot of the process-global plugin SERVICE registry.

    Mandatory for the same reason as the bus snapshot: ``build_test_app`` boots
    several apps in one process and the registry is process-global.
    """

    providers: dict[str, Any]
    uses: dict[str, dict]


@dataclass(frozen=True)
class _PluginModulesState:
    modules: dict[str, Any]
    parent_attributes: dict[str, Any]


@dataclass(frozen=True)
class _GroupMentionsState:
    client: Any
    bot_phone: str
    bot_name: str
    members_cache: dict
    store_cache: Any
    pushname_cache: dict
    pushname_attempted: set


def _snapshot_plugin_bus() -> _PluginBusState:
    from plugins import events as bus

    return _PluginBusState(
        handlers={name: list(items) for name, items in bus._handlers.items()},
        filters={name: list(items) for name, items in bus._filters.items()},
        core_sync_listeners=list(bus._core_sync_listeners),
        loop=bus._loop,
        agent_handler=bus._agent_handler,
    )


def _restore_plugin_bus(state: _PluginBusState) -> None:
    """Restore one LIFO harness snapshot without replacing shared containers."""
    from plugins import events as bus

    bus._handlers.clear()
    bus._handlers.update({name: list(items) for name, items in state.handlers.items()})
    bus._filters.clear()
    bus._filters.update({name: list(items) for name, items in state.filters.items()})
    bus._core_sync_listeners[:] = state.core_sync_listeners
    bus._loop = state.loop
    bus._agent_handler = state.agent_handler


def _snapshot_plugin_services() -> _PluginServicesState:
    from plugins import services as svc

    return _PluginServicesState(
        providers=dict(svc._providers),
        uses={pid: dict(ranges) for pid, ranges in svc._uses.items()},
    )


def _restore_plugin_services(state: _PluginServicesState) -> None:
    """Restore one LIFO harness snapshot without replacing shared containers."""
    from plugins import services as svc

    svc._providers.clear()
    svc._providers.update(state.providers)
    svc._uses.clear()
    svc._uses.update({pid: dict(ranges) for pid, ranges in state.uses.items()})


def _snapshot_plugin_modules(plugin_ids: Iterable[str]) -> _PluginModulesState:
    ids = tuple(plugin_ids)
    prefixes = tuple(f"whatsbot_plugins.{plugin_id}" for plugin_id in ids)
    modules = {
        name: module for name, module in sys.modules.items()
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)
    }
    parent = sys.modules.get("whatsbot_plugins")
    attrs = {
        plugin_id: getattr(parent, plugin_id, _MISSING) if parent is not None else _MISSING
        for plugin_id in ids
    }
    return _PluginModulesState(modules=modules, parent_attributes=attrs)


def _restore_plugin_modules(
    plugin_ids: Iterable[str], state: _PluginModulesState,
) -> None:
    ids = tuple(plugin_ids)
    for plugin_id in ids:
        purge_loaded_plugin_modules(plugin_id)
    sys.modules.update(state.modules)
    parent = sys.modules.get("whatsbot_plugins")
    if parent is not None:
        for plugin_id, old_value in state.parent_attributes.items():
            if old_value is _MISSING:
                try:
                    delattr(parent, plugin_id)
                except AttributeError:
                    pass
            else:
                setattr(parent, plugin_id, old_value)


def _snapshot_group_mentions() -> _GroupMentionsState:
    from agent import group_mentions

    return _GroupMentionsState(
        client=group_mentions._client,
        bot_phone=group_mentions._bot_phone,
        bot_name=group_mentions._bot_name,
        # These caches contain nested mutable dict/list values. A shallow copy
        # lets an inner build mutate the outer build's saved snapshot when both
        # apps intentionally reuse the same GOWA client.
        members_cache=deepcopy(group_mentions._members_cache),
        store_cache=deepcopy(group_mentions._store_cache),
        pushname_cache=dict(group_mentions._pushname_cache),
        pushname_attempted=set(group_mentions._pushname_attempted),
    )


def _restore_group_mentions(state: _GroupMentionsState) -> None:
    from agent import group_mentions

    group_mentions._client = state.client
    group_mentions._bot_phone = state.bot_phone
    group_mentions._bot_name = state.bot_name
    group_mentions._members_cache.clear()
    group_mentions._members_cache.update(state.members_cache)
    group_mentions._store_cache = state.store_cache
    group_mentions._pushname_cache.clear()
    group_mentions._pushname_cache.update(state.pushname_cache)
    group_mentions._pushname_attempted.clear()
    group_mentions._pushname_attempted.update(state.pushname_attempted)


def _snapshot_tool_overrides() -> list[dict]:
    """Snapshot the whole shared table; app boot performs global orphan cleanup."""
    from db.repositories import tool_override_repo

    return tool_override_repo.list_all()


def _restore_tool_overrides(before: list[dict]) -> None:
    """Restore rows removed/created/updated by an isolated app boot (LIFO)."""
    from db.engine import get_engine
    from db.tables import tool_overrides
    from sqlalchemy import delete, insert

    with get_engine().begin() as connection:
        connection.execute(delete(tool_overrides))
        if before:
            connection.execute(insert(tool_overrides), before)


def _snapshot_plugin_permissions(plugin_ids: Iterable[str]) -> dict[str, list[dict]]:
    from db.repositories import rbac_repo

    wanted = set(plugin_ids)
    snapshot = {plugin_id: [] for plugin_id in wanted}
    for row in rbac_repo.list_plugin_permissions():
        plugin_id = row.get("plugin_id")
        if plugin_id in wanted:
            snapshot[plugin_id].append(row)
    return snapshot


def _restore_plugin_permissions(before: Mapping[str, list[dict]]) -> None:
    """Remove permissions introduced by a build and restore prior metadata.

    Existing rows are updated in place so their ids and role/user grants survive.
    Synthetic permissions that did not exist before are deleted with their
    synthetic grants through the schema's normal FK cascade.
    """
    from db.engine import get_engine
    from db.tables import permissions
    from sqlalchemy import delete, select, update

    with get_engine().begin() as connection:
        for plugin_id, old_rows in before.items():
            old_by_key = {row["key"]: row for row in old_rows}
            current = connection.execute(
                select(permissions.c.key)
                .where(permissions.c.plugin_id == plugin_id)
            ).scalars().all()
            new_keys = [key for key in current if key not in old_by_key]
            if new_keys:
                connection.execute(
                    delete(permissions).where(permissions.c.key.in_(new_keys))
                )
            for key, row in old_by_key.items():
                connection.execute(
                    update(permissions)
                    .where(permissions.c.key == key)
                    .values(
                        description=row.get("description") or "",
                        plugin_id=row.get("plugin_id"),
                        group_label=row.get("group_label"),
                    )
                )


def _restore_plugin_rows(before: Mapping[str, dict | None]) -> None:
    """Restore plugin catalogue rows, retaining migration history when required."""
    from db.repositories import plugin_repo

    for plugin_id, old_row in before.items():
        if old_row is None:
            # Deleting a migrated row also cascades plugin_migrations but leaves
            # physical tables behind; a later build could replay non-idempotent
            # ALTERs. Remove only a truly schema-free row. Otherwise retain the
            # history but neutralize the source by disabling it.
            from db.engine import get_engine
            from sqlalchemy import inspect

            table_prefix = f"plugin_{plugin_id}_"
            has_tables = any(
                name.startswith(table_prefix)
                for name in inspect(get_engine()).get_table_names()
            )
            if plugin_repo.applied_migrations(plugin_id) or has_tables:
                plugin_repo.set_enabled(plugin_id, False)
            else:
                plugin_repo.delete(plugin_id)
            continue
        plugin_repo.upsert(
            plugin_id,
            old_row["version"],
            enabled=bool(old_row.get("enabled")),
        )
        plugin_repo.set_installed_deps(
            plugin_id, list(old_row.get("installed_deps") or []),
        )
        plugin_repo.set_load_error(plugin_id, old_row.get("load_error"))


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
    _plugin_rows_before: dict[str, dict | None] = field(
        default_factory=dict, repr=False,
    )
    _plugin_permissions_before: dict[str, list[dict]] = field(
        default_factory=dict, repr=False,
    )
    _plugin_bus_before: _PluginBusState | None = field(default=None, repr=False)
    _plugin_services_before: _PluginServicesState | None = field(
        default=None, repr=False,
    )
    _plugin_modules_before: _PluginModulesState | None = field(default=None, repr=False)
    _group_mentions_before: _GroupMentionsState | None = field(default=None, repr=False)
    _tool_overrides_before: list[dict] = field(default_factory=list, repr=False)
    _closed: bool = field(default=False, repr=False)

    def close(self) -> None:
        """Close resources and restore/neutralize all requested plugin state."""
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self.client.__exit__(None, None, None)
            finally:
                if self._tmp is not None:
                    self._tmp.cleanup()
        finally:
            if self._plugin_bus_before is not None:
                _restore_plugin_bus(self._plugin_bus_before)
            if self._plugin_services_before is not None:
                _restore_plugin_services(self._plugin_services_before)
            _restore_tool_overrides(self._tool_overrides_before)
            _restore_plugin_permissions(self._plugin_permissions_before)
            _restore_plugin_rows(self._plugin_rows_before)
            if self._plugin_modules_before is not None:
                _restore_plugin_modules(self.plugins, self._plugin_modules_before)
            if self._group_mentions_before is not None:
                _restore_group_mentions(self._group_mentions_before)


def _copy_plugin(
    plugin_id: str,
    dest_parent: Path,
    *,
    source: str | Path | None = None,
) -> Path:
    """Copy one plugin into ``dest_parent``, optionally from an explicit source."""
    try:
        plugin_source_candidates(
            plugin_id,
            examples_root=REAL_PLUGIN_EXAMPLES,
            installed_root=REAL_INSTALLED_PLUGINS,
        )
    except ValueError as exc:
        raise ValueError(f"build_test_app: {exc}") from None
    if source is not None:
        src = Path(source)
        if not src.is_dir():
            raise ValueError(
                f"build_test_app: explicit source for plugin {plugin_id!r} "
                f"is not a directory: {src}"
            )
    else:
        try:
            src = resolve_plugin_source(
                plugin_id,
                examples_root=REAL_PLUGIN_EXAMPLES,
                installed_root=REAL_INSTALLED_PLUGINS,
            )
        except PluginSourceNotFound as exc:
            raise ValueError(
                f"build_test_app: unknown plugin {plugin_id!r} "
                f"(not found in {', '.join(str(path) for path in exc.candidates)})"
            ) from None
    dest = dest_parent / plugin_id
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def build_test_app(
    plugins: Iterable[str] = ("gowa",),
    *,
    plugin_sources: Optional[Mapping[str, str | Path]] = None,
    settings_overrides: Optional[dict] = None,
    gowa_client: Any = None,
) -> BuiltApp:
    """Build a hermetic app with exactly ``plugins`` loaded + enabled.

    Args:
        plugins: plugin ids to copy + enable (default ``("gowa",)``). Each must
            exist in the external source root, under
            ``assets/plugin_examples/<id>/``, or under
            ``storages/plugins/<id>/``.
        plugin_sources: optional mapping from a requested plugin id to an explicit
            source directory. Useful for versioned fixtures; ids not present keep
            the normal examples-then-installed resolution. Existing callers do
            not need to pass it.
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
    lifetime. Call ``built.close()`` when not using the managed fixtures; this
    restores prior catalogue/RBAC/bus state. A newly migrated synthetic plugin
    retains its migration history but is disabled, preventing a non-idempotent
    migration from replaying against physical tables left in the shared schema.
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
    if len(set(plugins)) != len(plugins):
        raise ValueError("build_test_app: duplicate plugin ids are not allowed")
    for plugin_id in plugins:
        try:
            plugin_source_candidates(
                plugin_id,
                examples_root=REAL_PLUGIN_EXAMPLES,
                installed_root=REAL_INSTALLED_PLUGINS,
            )
        except ValueError as exc:
            raise ValueError(f"build_test_app: {exc}") from None
    explicit_sources = dict(plugin_sources or {})
    unexpected_sources = sorted(set(explicit_sources) - set(plugins))
    if unexpected_sources:
        raise ValueError(
            "build_test_app: plugin_sources contains ids not requested in plugins: "
            + ", ".join(unexpected_sources)
        )

    # Snapshot every process-global/shared-DB surface before the first mutation.
    # Managed fixtures close apps in reverse order, so nested builds restore LIFO.
    plugin_rows_before = {pid: plugin_repo.get(pid) for pid in plugins}
    plugin_permissions_before = _snapshot_plugin_permissions(plugins)
    tool_overrides_before = _snapshot_tool_overrides()
    plugin_bus_before = _snapshot_plugin_bus()
    plugin_services_before = _snapshot_plugin_services()
    plugin_modules_before = _snapshot_plugin_modules(plugins)
    group_mentions_before = _snapshot_group_mentions()

    # 1) tmp data_dir scaffold: storages/plugins/<id> + assets/plugin_examples/<id>.
    tmp = tempfile.TemporaryDirectory(prefix="whatsbot_buildapp_")
    data_dir = Path(tmp.name)
    client = None
    client_entered = False
    try:
        plugins_dir = data_dir / "storages" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        examples_dir = data_dir / "assets" / "plugin_examples"
        examples_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "logs").mkdir(parents=True, exist_ok=True)
        (data_dir / "statics").mkdir(parents=True, exist_ok=True)

        # 2) Copy ONLY the requested plugins into BOTH dirs, then enable each in
        #    the shared `plugins` table so create_app's loader sees exactly them.
        for pid in plugins:
            source = explicit_sources.get(pid)
            _copy_plugin(pid, plugins_dir, source=source)
            _copy_plugin(pid, examples_dir, source=source)
            manifest = load_manifest(plugins_dir / pid)
            plugin_repo.upsert(manifest.id, manifest.version, enabled=True)

        # The production loader caches canonical submodules for one process boot.
        # Tests may boot the same id from another tree, so evict that namespace.
        for pid in plugins:
            purge_loaded_plugin_modules(pid)

        # 3) Settings point at the temporary plugin/statics scaffold.
        settings = Settings()
        settings.data_dir = data_dir
        settings.logs_dir = data_dir / "logs"
        for key, value in (settings_overrides or {}).items():
            settings.set(key, value)

        # 4) Doubles + real AgentHandler. The LLM is never called unless a test
        #    explicitly stubs/enables that path.
        if gowa_client is None:
            from tests.fakes import FakeGowaClient
            gowa_client = FakeGowaClient()
        gowa_manager = MagicMock()
        agent_handler = AgentHandler(
            api_key="test-key-fake", max_context_messages=10,
            default_ai_enabled=settings.get("default_ai_enabled", True))

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
        client_entered = True

        return BuiltApp(
            app=application,
            client=client,
            gowa_client=gowa_client,
            agent_handler=agent_handler,
            settings=settings,
            data_dir=data_dir,
            plugins=plugins,
            _tmp=tmp,
            _plugin_rows_before=plugin_rows_before,
            _plugin_permissions_before=plugin_permissions_before,
            _plugin_bus_before=plugin_bus_before,
            _plugin_services_before=plugin_services_before,
            _plugin_modules_before=plugin_modules_before,
            _group_mentions_before=group_mentions_before,
            _tool_overrides_before=tool_overrides_before,
        )
    except BaseException:
        try:
            if client is not None and client_entered:
                client.__exit__(None, None, None)
        finally:
            tmp.cleanup()
            _restore_plugin_bus(plugin_bus_before)
            _restore_plugin_services(plugin_services_before)
            _restore_tool_overrides(tool_overrides_before)
            _restore_plugin_permissions(plugin_permissions_before)
            _restore_plugin_rows(plugin_rows_before)
            _restore_plugin_modules(plugins, plugin_modules_before)
            _restore_group_mentions(group_mentions_before)
        raise


def build_test_app_with_plugin(plugin_id: str, **kwargs) -> BuiltApp:
    """Boot the real app with a SINGLE plugin enabled (Phase G2 convenience).

    Thin wrapper over :func:`build_test_app` for the common case of a plugin's
    own test exercising its routes/filters against a live app: a plugin test can
    do ::

        built = build_test_app_with_plugin("telegram")
        r = built.client.get("/api/plugins/telegram/channels")
        assert r.json()["ok"] is True

    ``plugin_id`` is resolved from ``assets/plugin_examples/<id>/`` first and
    ``storages/plugins/<id>/`` second. Extra keyword args
    (``settings_overrides``, ``gowa_client``) pass straight through. Shares the
    process-global engine just like
    ``build_test_app`` — see the module docstring. Remember to tear the returned
    app down with ``built.close()``; the ``plugin_app`` fixture in
    ``tests/conftest.py`` does that for you.
    """
    return build_test_app([plugin_id], **kwargs)
