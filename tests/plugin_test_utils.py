"""DB-free helpers for tests that consume plugin source trees.

Plugin code lives in one of two places while plugins are being extracted from
the core: the versioned workbench under ``assets/plugin_examples`` or the
installed tree under ``storages/plugins``.  Tests should resolve those trees in
that order and load modules as real packages so plugin-relative imports keep
working.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_EXAMPLES_ROOT = PROJECT_ROOT / "assets" / "plugin_examples"
INSTALLED_PLUGINS_ROOT = PROJECT_ROOT / "storages" / "plugins"
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class PluginSourceNotFound(ValueError):
    """Raised when a plugin is absent from both supported source trees."""

    def __init__(self, plugin_id: str, candidates: tuple[Path, Path]) -> None:
        self.plugin_id = plugin_id
        self.candidates = candidates
        super().__init__(
            f"plugin {plugin_id!r} not found; looked in "
            f"{candidates[0]} and {candidates[1]}"
        )


def plugin_source_candidates(
    plugin_id: str,
    *,
    examples_root: Path = PLUGIN_EXAMPLES_ROOT,
    installed_root: Path = INSTALLED_PLUGINS_ROOT,
) -> tuple[Path, Path]:
    """Return the two candidate directories in precedence order."""
    if not PLUGIN_ID_RE.fullmatch(plugin_id or ""):
        raise ValueError(f"invalid plugin id: {plugin_id!r}")
    return Path(examples_root) / plugin_id, Path(installed_root) / plugin_id


def purge_loaded_plugin_modules(plugin_id: str) -> tuple[str, ...]:
    """Remove one plugin's canonical production-loader namespace.

    ``build_test_app`` may boot the same plugin id from different source trees in
    one pytest process. The production loader legitimately assumes one boot per
    process and reuses an existing submodule; without this test-only purge, the
    second app can advertise the new manifest while executing code from the first
    (already deleted) temp directory.

    Existing app objects keep their direct callable/class references. Tests must
    nevertheless avoid concurrently exercising two live apps for the same plugin
    id, because Python has only one canonical ``whatsbot_plugins.<id>`` namespace.
    """
    # Reuse the same traversal-safe validation as source resolution.
    plugin_source_candidates(plugin_id)
    package_name = f"whatsbot_plugins.{plugin_id}"
    removed = tuple(sorted(
        name for name in sys.modules
        if name == package_name or name.startswith(f"{package_name}.")
    ))
    for name in removed:
        sys.modules.pop(name, None)

    parent = sys.modules.get("whatsbot_plugins")
    if parent is not None and hasattr(parent, plugin_id):
        try:
            delattr(parent, plugin_id)
        except AttributeError:  # pragma: no cover - concurrent defensive guard
            pass
    importlib.invalidate_caches()
    return removed


def resolve_plugin_source(
    plugin_id: str,
    *,
    examples_root: Path = PLUGIN_EXAMPLES_ROOT,
    installed_root: Path = INSTALLED_PLUGINS_ROOT,
) -> Path:
    """Find ``plugin_id`` in the workbench first, then the installed tree."""
    candidates = plugin_source_candidates(
        plugin_id,
        examples_root=examples_root,
        installed_root=installed_root,
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise PluginSourceNotFound(plugin_id, candidates)


def _ensure_parent_packages(package_name: str) -> None:
    """Create synthetic parents for an explicitly namespaced test package."""
    parts = package_name.split(".")
    for index in range(1, len(parts)):
        parent_name = ".".join(parts[:index])
        if parent_name in sys.modules:
            continue
        spec = importlib.util.spec_from_loader(parent_name, loader=None, is_package=True)
        if spec is None:
            raise ImportError(f"could not build namespace package {parent_name}")
        parent = importlib.util.module_from_spec(spec)
        parent.__path__ = []  # type: ignore[attr-defined]
        sys.modules[parent_name] = parent


def _existing_package_matches(module: ModuleType, plugin_dir: Path) -> bool:
    search_path = getattr(module, "__path__", None)
    if search_path is None:
        return False
    expected = plugin_dir.resolve()
    return any(Path(entry).resolve() == expected for entry in search_path)


def load_plugin_package(
    plugin_id: str,
    *,
    package_name: str,
    examples_root: Path = PLUGIN_EXAMPLES_ROOT,
    installed_root: Path = INSTALLED_PLUGINS_ROOT,
) -> ModuleType:
    """Resolve and import a plugin directory as ``package_name``.

    The package is inserted into ``sys.modules`` before its ``__init__.py`` is
    executed.  Consequently imports such as ``from . import helpers`` behave as
    they do under the production plugin loader.  Plugins without an
    ``__init__.py`` are loaded as namespace packages without modifying their
    source tree.
    """
    plugin_dir = resolve_plugin_source(
        plugin_id,
        examples_root=examples_root,
        installed_root=installed_root,
    )
    existing = sys.modules.get(package_name)
    if existing is not None:
        if not _existing_package_matches(existing, plugin_dir):
            raise ImportError(
                f"package {package_name!r} is already loaded from a different path"
            )
        return existing

    _ensure_parent_packages(package_name)
    init_file = plugin_dir / "__init__.py"
    if init_file.is_file():
        spec = importlib.util.spec_from_file_location(
            package_name,
            init_file,
            submodule_search_locations=[str(plugin_dir)],
        )
    else:
        spec = importlib.util.spec_from_loader(package_name, loader=None, is_package=True)
    if spec is None or (init_file.is_file() and spec.loader is None):
        raise ImportError(f"could not build package spec for {package_name!r}")

    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
    before = {
        name for name in sys.modules
        if name == package_name or name.startswith(f"{package_name}.")
    }
    sys.modules[package_name] = module
    try:
        if spec.loader is not None:
            spec.loader.exec_module(module)
    except Exception:
        for name in list(sys.modules):
            if (
                name not in before
                and (name == package_name or name.startswith(f"{package_name}."))
            ):
                sys.modules.pop(name, None)
        raise
    return module


def load_plugin_module(
    plugin_id: str,
    module_name: str,
    *,
    package_name: str,
    examples_root: Path = PLUGIN_EXAMPLES_ROOT,
    installed_root: Path = INSTALLED_PLUGINS_ROOT,
) -> ModuleType:
    """Resolve a plugin and import one submodule with relative imports enabled."""
    package = load_plugin_package(
        plugin_id,
        package_name=package_name,
        examples_root=examples_root,
        installed_root=installed_root,
    )
    if not module_name or module_name == "__init__":
        return package
    return importlib.import_module(f"{package_name}.{module_name}")


def loaded_plugin_module(plugin_id: str, module_name: str) -> ModuleType:
    """Return a module imported by the real WhatsBot plugin loader.

    Source-loaded modules intentionally use a synthetic package namespace. They
    are suitable for pure unit tests, but monkeypatching one does not affect the
    object wired into a live app. Integration tests must call this helper *after*
    ``build_test_app`` and patch the canonical loader namespace instead.
    """
    suffix = f".{module_name}" if module_name and module_name != "__init__" else ""
    qualified_name = f"whatsbot_plugins.{plugin_id}{suffix}"
    module = sys.modules.get(qualified_name)
    if module is None:
        raise LookupError(
            f"plugin module {qualified_name!r} is not loaded; build the app with "
            f"plugin {plugin_id!r} before patching its runtime module"
        )
    return module
