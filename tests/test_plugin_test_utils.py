"""DB-free coverage for the shared plugin-source test helpers (plan 83 P2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests import support
from tests.plugin_test_utils import (
    PluginSourceNotFound,
    load_plugin_module,
    load_plugin_package,
    loaded_plugin_module,
    purge_loaded_plugin_modules,
    resolve_plugin_source,
)


def _plugin_dir(root: Path, plugin_id: str) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    return plugin_dir


def _unload(package_name: str) -> None:
    for name in list(sys.modules):
        if name == package_name or name.startswith(f"{package_name}."):
            sys.modules.pop(name, None)


def test_resolve_plugin_source_prefers_examples(tmp_path):
    examples = tmp_path / "assets" / "plugin_examples"
    installed = tmp_path / "storages" / "plugins"
    expected = _plugin_dir(examples, "demo")
    _plugin_dir(installed, "demo")

    assert resolve_plugin_source(
        "demo", examples_root=examples, installed_root=installed
    ) == expected


def test_resolve_plugin_source_falls_back_to_installed(tmp_path):
    examples = tmp_path / "assets" / "plugin_examples"
    installed = tmp_path / "storages" / "plugins"
    expected = _plugin_dir(installed, "demo")

    assert resolve_plugin_source(
        "demo", examples_root=examples, installed_root=installed
    ) == expected


def test_missing_plugin_error_lists_both_candidates(tmp_path):
    examples = tmp_path / "assets" / "plugin_examples"
    installed = tmp_path / "storages" / "plugins"

    with pytest.raises(PluginSourceNotFound) as raised:
        resolve_plugin_source(
            "missing", examples_root=examples, installed_root=installed
        )

    message = str(raised.value)
    assert str(examples / "missing") in message
    assert str(installed / "missing") in message

    with pytest.raises(ValueError, match="invalid plugin id"):
        resolve_plugin_source(
            "../escape", examples_root=examples, installed_root=installed
        )


def test_load_package_and_module_support_relative_imports_from_fallback(tmp_path):
    examples = tmp_path / "assets" / "plugin_examples"
    installed = tmp_path / "storages" / "plugins"
    plugin_dir = _plugin_dir(installed, "demo")
    (plugin_dir / "__init__.py").write_text(
        "from .shared import VALUE as PACKAGE_VALUE\n", encoding="utf-8"
    )
    (plugin_dir / "shared.py").write_text("VALUE = 41\n", encoding="utf-8")
    (plugin_dir / "feature.py").write_text(
        "from .shared import VALUE\nRESULT = VALUE + 1\n", encoding="utf-8"
    )
    package_name = "plan83_test_plugins.demo"

    try:
        package = load_plugin_package(
            "demo",
            package_name=package_name,
            examples_root=examples,
            installed_root=installed,
        )
        module = load_plugin_module(
            "demo",
            "feature",
            package_name=package_name,
            examples_root=examples,
            installed_root=installed,
        )

        assert package.PACKAGE_VALUE == 41
        assert module.RESULT == 42
        assert module.__package__ == package_name
    finally:
        _unload(package_name)
        _unload("plan83_test_plugins")


def test_copy_plugin_uses_installed_fallback_and_reports_both_roots(
    tmp_path, monkeypatch
):
    examples = tmp_path / "assets" / "plugin_examples"
    installed = tmp_path / "storages" / "plugins"
    plugin_dir = _plugin_dir(installed, "demo")
    (plugin_dir / "plugin.yaml").write_text("id: demo\n", encoding="utf-8")
    (plugin_dir / "__pycache__").mkdir()
    (plugin_dir / "__pycache__" / "cached.pyc").write_bytes(b"cache")
    monkeypatch.setattr(support, "REAL_PLUGIN_EXAMPLES", examples)
    monkeypatch.setattr(support, "REAL_INSTALLED_PLUGINS", installed)

    copied = support._copy_plugin("demo", tmp_path / "destination")

    assert copied == tmp_path / "destination" / "demo"
    assert (copied / "plugin.yaml").is_file()
    assert not (copied / "__pycache__").exists()

    with pytest.raises(ValueError) as raised:
        support._copy_plugin("missing", tmp_path / "other-destination")
    message = str(raised.value)
    assert str(examples / "missing") in message
    assert str(installed / "missing") in message

    with pytest.raises(ValueError, match="invalid plugin id"):
        support._copy_plugin(
            "../escape", tmp_path / "unsafe-destination", source=plugin_dir,
        )


def test_loaded_plugin_module_only_returns_the_real_loader_namespace():
    package_name = "whatsbot_plugins.plan83_runtime_demo"
    module_name = f"{package_name}.filters"
    package = type(sys)(package_name)
    module = type(sys)(module_name)
    sys.modules[package_name] = package
    sys.modules[module_name] = module
    try:
        assert loaded_plugin_module("plan83_runtime_demo", "filters") is module
        with pytest.raises(LookupError, match="build the app"):
            loaded_plugin_module("plan83_runtime_demo", "routes")
    finally:
        _unload(package_name)


def test_purge_loaded_plugin_modules_is_scoped_to_one_canonical_package():
    parent_name = "whatsbot_plugins"
    package_name = f"{parent_name}.purge_demo"
    submodule_name = f"{package_name}.filters"
    sibling_name = f"{parent_name}.keep_demo"
    parent = sys.modules.setdefault(parent_name, type(sys)(parent_name))
    package = type(sys)(package_name)
    submodule = type(sys)(submodule_name)
    sibling = type(sys)(sibling_name)
    sys.modules[package_name] = package
    sys.modules[submodule_name] = submodule
    sys.modules[sibling_name] = sibling
    setattr(parent, "purge_demo", package)
    try:
        removed = purge_loaded_plugin_modules("purge_demo")

        assert removed == (package_name, submodule_name)
        assert package_name not in sys.modules
        assert submodule_name not in sys.modules
        assert sys.modules[sibling_name] is sibling
        assert not hasattr(parent, "purge_demo")
    finally:
        _unload(package_name)
        sys.modules.pop(sibling_name, None)
