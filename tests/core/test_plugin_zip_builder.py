"""DB-free tests for the deterministic plugin ZIP builder."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

from scripts.build_plugin_zips import (
    BuildError,
    FIXED_ZIP_TIMESTAMP,
    build_archive_bytes,
    build_plugin,
    discover_plugins,
    main,
    select_plugins,
    validate_plugin_dir,
)


def _plugin(root: Path, plugin_id: str, *, version: str = "1.0.0") -> Path:
    directory = root / plugin_id
    directory.mkdir(parents=True)
    (directory / "plugin.yaml").write_text(
        f"id: {plugin_id}\n"
        f"name: Demo {plugin_id}\n"
        f"version: {version}\n"
        'whatsbot_api_version: ">=1.0,<2.0"\n',
        encoding="utf-8",
    )
    (directory / "__init__.py").write_text("# plugin\n", encoding="utf-8")
    return directory


def test_archive_is_deterministic_sorted_and_excludes_cache_and_databases(tmp_path):
    source_root = tmp_path / "sources"
    plugin = _plugin(source_root, "alpha")
    (plugin / "z_last.py").write_text("LAST = True\n", encoding="utf-8")
    (plugin / "nested").mkdir()
    (plugin / "nested" / "a_first.txt").write_text("first\n", encoding="utf-8")
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "module.pyc").write_bytes(b"cache")
    (plugin / ".pytest_cache").mkdir()
    (plugin / ".pytest_cache" / "state").write_text("cache", encoding="utf-8")
    (plugin / ".git").write_text("gitdir: /private/worktree/path\n", encoding="utf-8")
    (plugin / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (plugin / "nested" / ".env.production").write_text(
        "TOKEN=also-secret\n", encoding="utf-8")
    (plugin / ".envrc").write_text("TOKEN=still-secret\n", encoding="utf-8")
    (plugin / ".env-local").write_text("TOKEN=secret-too\n", encoding="utf-8")
    for name in ("state.db", "state.db-wal", "state.sqlite3", "module.pyo", ".DS_Store"):
        (plugin / name).write_bytes(b"excluded")

    validated = validate_plugin_dir(plugin)
    first, count = build_archive_bytes(validated)
    for path in plugin.rglob("*"):
        if path.is_file():
            os.utime(path, (2_000_000_000, 2_000_000_000))
    second, second_count = build_archive_bytes(validated)

    assert first == second
    assert count == second_count == 4
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        names = archive.namelist()
        assert names == sorted(names) == [
            "__init__.py",
            "nested/a_first.txt",
            "plugin.yaml",
            "z_last.py",
        ]
        assert all(info.date_time == FIXED_ZIP_TIMESTAMP for info in archive.infolist())
        assert all((info.external_attr >> 16) & 0o777 == 0o644
                   for info in archive.infolist())


def test_discovery_uses_direct_valid_manifests_and_explicit_ids_are_safe(tmp_path):
    source_root = tmp_path / "sources"
    _plugin(source_root, "beta")
    _plugin(source_root, "alpha")
    (source_root / "notes").mkdir()
    (source_root / "notes" / "README.md").write_text("not a plugin", encoding="utf-8")
    outside = _plugin(tmp_path / "outside", "linked")
    try:
        (source_root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not allow creating directory symlinks")

    assert [plugin.plugin_id for plugin in discover_plugins(source_root)] == ["alpha", "beta"]
    assert [plugin.plugin_id for plugin in select_plugins(source_root, ["beta", "alpha", "beta"])] == [
        "alpha", "beta",
    ]
    with pytest.raises(BuildError, match="invalid plugin id"):
        select_plugins(source_root, ["../outside/linked"])
    with pytest.raises(BuildError, match="may not be a symlink"):
        select_plugins(source_root, ["linked"])
    with pytest.raises(BuildError, match="does not exist"):
        select_plugins(source_root, ["missing"])


def test_manifest_must_be_unique_at_root_and_match_directory(tmp_path):
    source_root = tmp_path / "sources"
    nested_only = source_root / "nested_only"
    (nested_only / "metadata").mkdir(parents=True)
    (nested_only / "metadata" / "plugin.yaml").write_text(
        "id: nested_only\nversion: 1.0.0\n", encoding="utf-8")
    with pytest.raises(BuildError, match="no manifest at its root"):
        validate_plugin_dir(nested_only)

    ambiguous = _plugin(source_root, "ambiguous")
    (ambiguous / "plugin.json").write_text(
        '{"id":"ambiguous","version":"1.0.0"}', encoding="utf-8")
    with pytest.raises(BuildError, match="ambiguous root manifests"):
        validate_plugin_dir(ambiguous)

    mismatch = _plugin(source_root, "folder_name")
    (mismatch / "plugin.yaml").write_text(
        "id: other_name\nversion: 1.0.0\n", encoding="utf-8")
    with pytest.raises(BuildError, match="must match plugin folder"):
        validate_plugin_dir(mismatch)


def test_symlink_inside_plugin_is_rejected(tmp_path):
    source_root = tmp_path / "sources"
    plugin = _plugin(source_root, "alpha")
    target = tmp_path / "secret.txt"
    target.write_text("do not package", encoding="utf-8")
    try:
        (plugin / "linked.txt").symlink_to(target)
    except OSError:
        pytest.skip("host does not allow creating file symlinks")

    with pytest.raises(BuildError, match="contains a symlink"):
        build_archive_bytes(validate_plugin_dir(plugin))


@pytest.mark.skipif(os.name == "nt", reason="backslash is not a literal filename on Windows")
def test_windows_separator_filename_is_rejected_as_unsafe(tmp_path):
    source_root = tmp_path / "sources"
    plugin = _plugin(source_root, "alpha")
    (plugin / r"nested\..\escape.py").write_text("ESCAPE = True\n", encoding="utf-8")

    with pytest.raises(BuildError, match="unsafe archive path"):
        build_archive_bytes(validate_plugin_dir(plugin))


def test_build_is_atomic_and_check_mode_detects_drift_without_writing(tmp_path):
    source_root = tmp_path / "sources"
    output_root = tmp_path / "archives"
    plugin = _plugin(source_root, "alpha")
    source = validate_plugin_dir(plugin)

    built = build_plugin(source, output_root)
    assert built.status == "built"
    original = built.output_path.read_bytes()
    if os.name != "nt":
        assert built.output_path.stat().st_mode & 0o777 == 0o644
    assert not list(output_root.glob("*.tmp"))

    unchanged = build_plugin(source, output_root)
    assert unchanged.status == "unchanged"
    assert unchanged.output_path.read_bytes() == original
    assert build_plugin(source, output_root, check=True).status == "current"

    (plugin / "new.py").write_text("NEW = True\n", encoding="utf-8")
    drifted = build_plugin(source, output_root, check=True)
    assert drifted.status == "outdated"
    assert drifted.output_path.read_bytes() == original

    missing = build_plugin(source, tmp_path / "missing-output", check=True)
    assert missing.status == "missing"
    assert not missing.output_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission convergence")
def test_existing_correct_bytes_with_private_mode_are_repaired(tmp_path):
    source_root = tmp_path / "sources"
    output_root = tmp_path / "archives"
    source = validate_plugin_dir(_plugin(source_root, "alpha"))

    built = build_plugin(source, output_root)
    built.output_path.chmod(0o600)
    assert build_plugin(source, output_root, check=True).status == "outdated"
    assert built.output_path.stat().st_mode & 0o777 == 0o600

    repaired = build_plugin(source, output_root)
    assert repaired.status == "repaired"
    assert built.output_path.stat().st_mode & 0o777 == 0o644


def test_cli_build_list_and_check_are_db_free(tmp_path, capsys):
    source_root = tmp_path / "sources"
    output_root = tmp_path / "archives"
    _plugin(source_root, "beta")
    _plugin(source_root, "alpha")

    assert main(["--list", "--source-dir", str(source_root)]) == 0
    assert capsys.readouterr().out.splitlines() == ["alpha", "beta"]

    assert main([
        "alpha",
        "--source-dir", str(source_root),
        "--output-dir", str(output_root),
    ]) == 0
    assert (output_root / "alpha-plugin.zip").is_file()

    assert main([
        "--check", "alpha",
        "--source-dir", str(source_root),
        "--output-dir", str(output_root),
    ]) == 0
    (_plugin(source_root, "gamma") / "extra.txt").write_text("x", encoding="utf-8")
    assert main([
        "--check", "gamma",
        "--source-dir", str(source_root),
        "--output-dir", str(output_root),
    ]) == 1


def test_output_directory_inside_plugin_is_rejected(tmp_path):
    source_root = tmp_path / "sources"
    plugin = _plugin(source_root, "alpha")
    with pytest.raises(BuildError, match="may not live inside plugin"):
        build_plugin(validate_plugin_dir(plugin), plugin / "dist")
