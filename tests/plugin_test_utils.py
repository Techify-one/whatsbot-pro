"""DB-free helpers for tests that consume plugin source trees.

During the repository split, plugin code may live in the sibling plugins
repository, in the legacy versioned workbench, or in an installed tree.  The
optional ``WHATSBOT_PLUGIN_SOURCE_ROOT`` points at the plugins repository's
``plugins/`` directory, whose entries use ``<id>/src``.  Test helpers resolve
that external source first and load modules as real packages so relative
imports keep working.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_EXAMPLES_ROOT = PROJECT_ROOT / "assets" / "plugin_examples"
INSTALLED_PLUGINS_ROOT = PROJECT_ROOT / "storages" / "plugins"
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class PluginSourceNotFound(ValueError):
    """Raised when a plugin is absent from every supported source tree."""

    def __init__(self, plugin_id: str, candidates: tuple[Path, ...]) -> None:
        self.plugin_id = plugin_id
        self.candidates = candidates
        super().__init__(
            f"plugin {plugin_id!r} not found; looked in "
            + ", ".join(str(candidate) for candidate in candidates)
        )


def external_plugins_root() -> Path | None:
    """Return the optional development-only external plugin source root."""
    value = os.environ.get("WHATSBOT_PLUGIN_SOURCE_ROOT", "").strip()
    return Path(value).expanduser() if value else None


def plugin_source_candidates(
    plugin_id: str,
    *,
    examples_root: Path = PLUGIN_EXAMPLES_ROOT,
    installed_root: Path = INSTALLED_PLUGINS_ROOT,
    external_root: Path | None = None,
) -> tuple[Path, ...]:
    """Return candidate directories in deterministic precedence order."""
    if not PLUGIN_ID_RE.fullmatch(plugin_id or ""):
        raise ValueError(f"invalid plugin id: {plugin_id!r}")
    root = external_plugins_root() if external_root is None else Path(external_root)
    candidates: list[Path] = []
    if root is not None:
        candidates.append(root / plugin_id / "src")
    candidates.extend((
        Path(examples_root) / plugin_id,
        Path(installed_root) / plugin_id,
    ))
    return tuple(candidates)


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
    external_root: Path | None = None,
) -> Path:
    """Find ``plugin_id`` in external, workbench, then installed sources."""
    candidates = plugin_source_candidates(
        plugin_id,
        examples_root=examples_root,
        installed_root=installed_root,
        external_root=external_root,
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
    external_root: Path | None = None,
) -> ModuleType:
    """Resolve and import a plugin directory as ``package_name``.

    The package is inserted into ``sys.modules`` before its ``__init__.py`` is
    executed.  Consequently imports such as ``from . import helpers`` behave as
    they do under the production plugin loader.  Plugins without an
    ``__init__.py`` are loaded as namespace packages without modifying their
    source tree.
    """
    try:
        plugin_dir = resolve_plugin_source(
            plugin_id,
            examples_root=examples_root,
            installed_root=installed_root,
            external_root=external_root,
        )
    except PluginSourceNotFound:
        # Same contract as ``tests.support.build_test_app``: the core ships only
        # ``gowa``, so a test that needs another plugin's source SKIPS instead of
        # failing. In the plugins repository the source is always present, so this
        # branch never fires there.
        if plugin_id == "gowa":
            raise
        import pytest

        pytest.skip(
            f"plugin {plugin_id!r} não é distribuído com o core "
            f"(defina WHATSBOT_PLUGIN_SOURCE_ROOT para rodar este teste)",
            allow_module_level=True,
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
    external_root: Path | None = None,
) -> ModuleType:
    """Resolve a plugin and import one submodule with relative imports enabled."""
    package = load_plugin_package(
        plugin_id,
        package_name=package_name,
        examples_root=examples_root,
        installed_root=installed_root,
        external_root=external_root,
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


def plugin_source_available(plugin_id: str) -> bool:
    """True when ``plugin_id`` can be resolved from any supported source tree.

    Only ``gowa`` ships with the core repository; every other plugin lives in the
    plugins repository and reaches this checkout through
    ``WHATSBOT_PLUGIN_SOURCE_ROOT`` or an installed copy. Core tests that assert
    PROVIDER-SPECIFIC behaviour must skip when the source is absent instead of
    failing — otherwise a clean clone is red for shipping exactly what it means
    to ship.
    """
    try:
        resolve_plugin_source(plugin_id)
    except (PluginSourceNotFound, ValueError):
        return False
    return True


def skip_without_plugin(*plugin_ids: str):
    """A ``pytest.mark.skipif`` that fires when any of ``plugin_ids`` is missing.

    Use as a module-level ``pytestmark`` in a core test that needs a plugin the
    core does not ship::

        pytestmark = skip_without_plugin("whatsapp_cloud")
    """
    import pytest

    missing = [pid for pid in plugin_ids if not plugin_source_available(pid)]
    return pytest.mark.skipif(
        bool(missing),
        reason=(
            "plugin não distribuído com o core: "
            + ", ".join(missing or plugin_ids)
            + " (defina WHATSBOT_PLUGIN_SOURCE_ROOT para rodar)"
        ),
    )
