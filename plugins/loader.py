"""Plugin loader and registry.

Discovers plugins under ``storages/plugins/<id>/``, parses each manifest,
runs pending SQL migrations for enabled plugins, then imports their Python
entry modules and collects tools, prompt fragments, FastAPI routers and
Pydantic settings classes.

The loader is called once at app startup (``server/app.py`` lifespan) after
``init_db()`` and before route registration.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import logging
import os
import re
import shutil
import sys
import traceback
from pathlib import Path
from types import ModuleType

from db.repositories import plugin_repo

from plugins import pkg_deps
# Bundled-plugin / GOWA bootstrap moved to plugins.bootstrap (plano 23 Fase C4).
# Re-exported here so callers importing them from ``plugins.loader`` keep working
# (server.app, tests/test_gowa_plugin).
from plugins.bootstrap import (  # noqa: F401
    bootstrap_gowa_upgrade,
    bootstrap_initial_plugins,
    _enable_bundled_gowa,
    _gowa_is_tombstoned,
)
from plugins.manifest import PluginManifest, find_manifest_file, load_manifest
from plugins.migrator import run_pending_migrations

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class LoadedPlugin:
    """A successfully loaded plugin and the artifacts it contributed."""

    manifest: PluginManifest
    plugin_dir: Path
    package_name: str
    tools: list[tuple[dict, callable]] = dataclasses.field(default_factory=list)
    # Procedência do módulo de ``entry.tools`` — de onde ``agent.ai_plugin_tools``
    # lê a fonte confiável para semear/reconciliar as rows editáveis em ai_tools.
    # ``(nome_do_submódulo, caminho_no_disco)``; None quando o plugin não tem tools.
    tools_module: tuple[str, str] | None = None
    prompt_fragments: list[callable] = dataclasses.field(default_factory=list)
    event_handlers: dict[str, callable] = dataclasses.field(default_factory=dict)
    # Each entry is either ``fn`` or ``(fn, priority:int)``.
    filters: dict[str, object] = dataclasses.field(default_factory=dict)
    router: object | None = None
    settings_cls: type | None = None
    static_dir: Path | None = None
    # Lifecycle hooks (plano 09 Fase 1): captured here, called+awaited by the host.
    setup_fn: callable | None = None
    teardown_fn: callable | None = None
    # Channel provider classes (plano 02 Fase 0): registered in the ChannelRegistry.
    channel_providers: list = dataclasses.field(default_factory=list)
    # Plugin→plugin service surface (in-process, never HTTP — plugins.services).
    services: dict[str, callable] = dataclasses.field(default_factory=dict)
    services_version: str = "1.0"
    services_allow: tuple = ()  # empty = any loaded plugin may call

    @property
    def id(self) -> str:
        return self.manifest.id


@dataclasses.dataclass
class DiscoveredPlugin:
    """A plugin found on disk but not necessarily loaded successfully."""

    manifest: PluginManifest
    plugin_dir: Path
    enabled: bool
    error: str | None = None


class PluginRegistry:
    """In-memory registry populated by ``discover_and_load``."""

    def __init__(self) -> None:
        self.loaded: dict[str, LoadedPlugin] = {}
        self.discovered: dict[str, DiscoveredPlugin] = {}

    def by_id(self, plugin_id: str) -> LoadedPlugin | None:
        return self.loaded.get(plugin_id)


_UPDATE_TMP_RE = re.compile(r"^\.(?P<pid>[a-z][a-z0-9_]{0,31})\.update-(?P<kind>staging|backup)$")


def _recover_interrupted_updates(plugins_dir: Path) -> None:
    """Heal a plugin folder swap that a hard crash interrupted (review finding #1).

    ``server.routes.plugins._swap_plugin_dir`` replaces a plugin in two renames:
    ``pid`` → ``.pid.update-backup``, then ``.pid.update-staging`` → ``pid``. A
    process kill (OOM, power loss, a concurrent ``os._exit`` from
    ``schedule_restart``) BETWEEN those two renames leaves ``pid`` gone with the
    code stranded in the dot-dirs — which discovery skips — so the plugin would
    silently vanish from disk with no auto-restore. On the next boot we recover:

    * ``pid`` missing + staging present → promote staging (update all but finished).
    * ``pid`` missing + only backup     → restore backup (revert to old code).
    * ``pid`` present                   → drop any leftover staging/backup.
    """
    if not plugins_dir.is_dir():
        return
    pids: set[str] = set()
    for child in plugins_dir.iterdir():
        m = _UPDATE_TMP_RE.match(child.name)
        if m and child.is_dir():
            pids.add(m.group("pid"))
    for pid in pids:
        target = plugins_dir / pid
        staging = plugins_dir / f".{pid}.update-staging"
        backup = plugins_dir / f".{pid}.update-backup"
        if target.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
            continue
        source = staging if staging.is_dir() else (backup if backup.is_dir() else None)
        if source is None:
            continue
        try:
            os.replace(source, target)
            logger.warning("Recovered interrupted plugin update: %s -> %s/%s",
                           source.name, plugins_dir.name, pid)
        except OSError as e:
            logger.error("Could not recover interrupted update for %s: %s", pid, e)
            continue
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def discover_and_load(plugins_dir: Path) -> PluginRegistry:
    """Scan ``plugins_dir``, sync DB, run migrations and import enabled plugins."""
    registry = PluginRegistry()
    if not plugins_dir.is_dir():
        plugins_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created plugins directory: %s", plugins_dir)

    # Before discovery: finish/revert any plugin update a crash left half-swapped.
    _recover_interrupted_updates(plugins_dir)

    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        if find_manifest_file(child) is None:
            continue
        _process_one(child, registry)

    return registry


def _process_one(plugin_dir: Path, registry: PluginRegistry) -> None:
    pid = plugin_dir.name
    try:
        manifest = load_manifest(plugin_dir)
    except Exception as e:
        logger.error("Plugin %s: manifest invalid: %s", pid, e)
        # We can't upsert without a valid id, but the folder name is likely
        # the id; record the error against it if a row already exists.
        existing = plugin_repo.get(pid)
        if existing:
            plugin_repo.set_load_error(pid, f"manifest invalid: {e}")
        return

    plugin_repo.upsert(manifest.id, manifest.version)
    db_row = plugin_repo.get(manifest.id) or {"enabled": 0}
    enabled = bool(db_row.get("enabled"))
    registry.discovered[manifest.id] = DiscoveredPlugin(
        manifest=manifest,
        plugin_dir=plugin_dir,
        enabled=enabled,
    )

    if not enabled:
        logger.info("Plugin %s: disabled, skipping load", manifest.id)
        return

    try:
        _ensure_plugin_deps(manifest, db_row)
        loaded = _load_plugin_module(manifest, plugin_dir)
        run_pending_migrations(manifest, plugin_dir)
        # Register the plugin's declared RBAC permissions (plano "RBAC para
        # Plugins"). Idempotent upsert; safe to run every boot.
        from plugins.rbac import sync_plugin_permissions
        sync_plugin_permissions(manifest)
        registry.loaded[manifest.id] = loaded
        plugin_repo.set_load_error(manifest.id, None)
        logger.info(
            "Plugin %s loaded (tools=%d prompts=%d events=%d filters=%d "
            "services=%d router=%s screens=%d)",
            manifest.id,
            len(loaded.tools),
            len(loaded.prompt_fragments),
            len(loaded.event_handlers),
            len(loaded.filters),
            len(loaded.services),
            "yes" if loaded.router else "no",
            len(manifest.screens),
        )
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error("Plugin %s failed to load:\n%s", manifest.id, traceback.format_exc())
        plugin_repo.set_load_error(manifest.id, err)
        registry.discovered[manifest.id].error = err


def _ensure_plugin_deps(manifest: PluginManifest, db_row: dict) -> None:
    """Install the plugin's declared pip ``dependencies`` before importing it.

    Mirrors the code-in-DB AI tool installer: pip only runs the first time the
    declared spec set changes (cache marker in ``plugins.installed_deps``). Any
    failure raises and bubbles to ``_process_one``'s handler → ``load_error``,
    so the plugin is skipped and the app still boots.
    """
    deps = manifest.dependencies or []
    if not deps:
        return
    ran = pkg_deps.ensure_pip_deps(
        deps,
        already=db_row.get("installed_deps") or [],
        label=f"plugin '{manifest.id}'",
    )
    if ran:
        plugin_repo.set_installed_deps(manifest.id, deps)


# ── Entry-point handlers (plano 23 Fase C4) ──────────────────────────────────
#
# Each plugin capability ("entry kind") used to be a near-identical block in
# ``_load_plugin_module``. They are now a data table ``_ENTRY_SPECS`` iterated
# once: for every ``(entry_key, mod)`` the loader imports the declared submodule
# (when present) and calls the kind's handler to collect into ``LoadedPlugin``.
# The ORDER of ``_ENTRY_SPECS`` is the load order and must match the legacy
# blocks (tools → prompts → events → filters → routes → settings → channels →
# lifecycle); each handler keeps the exact same validation + warning text.


def _entry_tools(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    tools_attr = getattr(mod, "CORE_TOOLS", None) or getattr(mod, "TOOLS", None)
    if not tools_attr:
        return
    mod_file = getattr(mod, "__file__", None)
    if mod_file:
        loaded.tools_module = ((manifest.entry or {}).get("tools") or "tools", mod_file)
    for entry in tools_attr:
        if not isinstance(entry, tuple) or len(entry) != 2:
            logger.warning(
                "Plugin %s: tools entry must be (schema, executor) tuples, got %r",
                manifest.id, entry,
            )
            continue
        loaded.tools.append(entry)


def _entry_prompts(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    frags = getattr(mod, "PROMPT_FRAGMENTS", None) or []
    for fn in frags:
        if callable(fn):
            loaded.prompt_fragments.append(fn)


def _entry_events(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    # Validation (dict shape + callable) lives in plugins.events — single source
    # (plano 23 Fase C4). The loader no longer re-implements the same checks.
    from plugins.events import validate_event_handlers
    loaded.event_handlers.update(
        validate_event_handlers(manifest.id, getattr(mod, "EVENT_HANDLERS", None)))


def _entry_filters(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    from plugins.events import validate_filters
    loaded.filters.update(
        validate_filters(manifest.id, getattr(mod, "FILTERS", None)))


def _entry_services(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    # Validation lives in plugins.services — single source, same pattern as
    # events/filters above. NEVER touches ``loaded.router``: the service surface
    # is in-process only and must stay invisible to HTTP.
    from plugins.services import validate_services
    loaded.services.update(
        validate_services(manifest.id, getattr(mod, "SERVICES", None)))
    loaded.services_version = str(getattr(mod, "SERVICES_VERSION", "1.0"))
    allow = getattr(mod, "SERVICES_ALLOW", None)
    loaded.services_allow = tuple(allow) if allow else ()


def _entry_routes(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    loaded.router = getattr(mod, "router", None)


def _entry_settings(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    loaded.settings_cls = getattr(mod, "Settings", None)


def _entry_channels(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    providers = getattr(mod, "CHANNEL_PROVIDERS", None) or []
    for cls in providers:
        if isinstance(cls, type):
            loaded.channel_providers.append(cls)
        else:
            logger.warning(
                "Plugin %s: CHANNEL_PROVIDERS entry %r is not a class, skipped",
                manifest.id, cls,
            )


def _entry_lifecycle(loaded: LoadedPlugin, manifest: PluginManifest, mod: ModuleType) -> None:
    # Captured here but NOT called (no event loop at import time); the host
    # lifespan calls and awaits them via plugins.lifecycle.manager.
    setup_fn = getattr(mod, "setup", None)
    teardown_fn = getattr(mod, "teardown", None)
    loaded.setup_fn = setup_fn if callable(setup_fn) else None
    loaded.teardown_fn = teardown_fn if callable(teardown_fn) else None


# (entry_key, handler). ORDER = load order; do not reorder (matches legacy blocks).
_ENTRY_SPECS: list[tuple[str, callable]] = [
    ("tools", _entry_tools),          # CORE_TOOLS=[(schema, executor), ...]
    ("prompts", _entry_prompts),      # PROMPT_FRAGMENTS=[callable, ...]
    ("events", _entry_events),        # EVENT_HANDLERS={"name": callable, ...}
    ("filters", _entry_filters),      # FILTERS={"filter.name": fn | (fn, priority)}
    ("routes", _entry_routes),        # router=APIRouter()
    ("settings", _entry_settings),    # Settings (Pydantic BaseModel) — phase 6
    ("channels", _entry_channels),    # CHANNEL_PROVIDERS=[ChannelClass, ...] — plano 02
    ("lifecycle", _entry_lifecycle),  # setup(ctx)/teardown(ctx) — plano 09 Fase 1
    # APPENDED at the end on purpose: the order above is the legacy load order and
    # every position must stay put. A core without this row simply never consults
    # ``entry.services`` — which is what keeps a provider loadable on an older core.
    ("services", _entry_services),    # SERVICES={"op": callable, ...}
]


def _load_plugin_module(
    manifest: PluginManifest, plugin_dir: Path
) -> LoadedPlugin:
    package_name = f"whatsbot_plugins.{manifest.id}"
    _ensure_parent_package()
    module = _import_package(package_name, plugin_dir)
    loaded = LoadedPlugin(
        manifest=manifest, plugin_dir=plugin_dir, package_name=package_name
    )

    for entry_key, handler in _ENTRY_SPECS:
        modname = manifest.entry.get(entry_key)
        if not modname:
            continue
        submod = _import_submodule(package_name, modname, plugin_dir)
        handler(loaded, manifest, submod)

    static_dir = plugin_dir / "static"
    if static_dir.is_dir():
        loaded.static_dir = static_dir

    # Keep module reference so it's not garbage-collected.
    _ = module
    return loaded


def _ensure_parent_package() -> None:
    """Make ``whatsbot_plugins`` a synthetic namespace package."""
    if "whatsbot_plugins" in sys.modules:
        return
    spec = importlib.util.spec_from_loader("whatsbot_plugins", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = []  # type: ignore[attr-defined]
    sys.modules["whatsbot_plugins"] = module


def _import_package(package_name: str, plugin_dir: Path) -> ModuleType:
    init_file = plugin_dir / "__init__.py"
    if not init_file.exists():
        # auto-create empty __init__.py for plugin authors who forget it
        init_file.write_text("", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_file,
        submodule_search_locations=[str(plugin_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build spec for {package_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


def _import_submodule(
    package_name: str, modname: str, plugin_dir: Path
) -> ModuleType:
    full = f"{package_name}.{modname}"
    if full in sys.modules:
        return sys.modules[full]
    file_path = plugin_dir / f"{modname}.py"
    if not file_path.exists():
        raise ImportError(f"{full}: file not found at {file_path}")
    spec = importlib.util.spec_from_file_location(full, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build spec for {full}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module
