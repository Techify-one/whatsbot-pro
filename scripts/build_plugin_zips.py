#!/usr/bin/env python3
"""Build deterministic, import-ready WhatsBot plugin archives.

Examples::

    # Build selected plugins into assets/channel_plugins/.
    ./venv/bin/python scripts/build_plugin_zips.py telegram whatsapp_cloud

    # Discover and build every valid plugin under assets/plugin_examples/.
    ./venv/bin/python scripts/build_plugin_zips.py --all

    # Verify that existing archives match their sources without writing anything.
    ./venv/bin/python scripts/build_plugin_zips.py --check telegram whatsapp_cloud

The archive always has its manifest at the ZIP root. File order, timestamps and
permissions are normalized so identical source bytes produce identical archives,
independent of checkout mtimes. Cache artifacts, Python bytecode, dotenv secrets and
local database files are never included.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import os
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from plugins.manifest import load_manifest  # noqa: E402


DEFAULT_SOURCE_DIR = REPO_ROOT / "assets" / "plugin_examples"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "assets" / "channel_plugins"

PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MANIFEST_NAMES = ("plugin.yaml", "plugin.yml", "plugin.json")
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

EXCLUDED_DIR_NAMES = frozenset({
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
})
EXCLUDED_FILE_NAMES = frozenset({".DS_Store"})
EXCLUDED_FILE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".db",
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite-journal",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-journal",
    ".sqlite3-shm",
    ".sqlite3-wal",
)


class BuildError(ValueError):
    """An actionable plugin archive validation/build error."""


@dataclasses.dataclass(frozen=True)
class PluginSource:
    plugin_id: str
    directory: Path
    manifest_name: str


@dataclasses.dataclass(frozen=True)
class BuildResult:
    plugin_id: str
    output_path: Path
    file_count: int
    size: int
    sha256: str
    status: str  # built | repaired | unchanged | current | missing | outdated


def _root_manifests(plugin_dir: Path) -> list[Path]:
    return [plugin_dir / name for name in MANIFEST_NAMES
            if (plugin_dir / name).is_file()]


def validate_plugin_dir(plugin_dir: Path) -> PluginSource:
    """Validate one direct plugin source directory and its root manifest."""
    if plugin_dir.is_symlink():
        raise BuildError(f"plugin directory may not be a symlink: {plugin_dir}")
    plugin_dir = plugin_dir.resolve()
    if not plugin_dir.is_dir():
        raise BuildError(f"plugin directory does not exist: {plugin_dir}")

    manifests = _root_manifests(plugin_dir)
    if not manifests:
        expected = ", ".join(MANIFEST_NAMES)
        raise BuildError(
            f"plugin {plugin_dir.name!r} has no manifest at its root "
            f"(expected one of: {expected})")
    if len(manifests) != 1:
        names = ", ".join(path.name for path in manifests)
        raise BuildError(
            f"plugin {plugin_dir.name!r} has ambiguous root manifests: {names}")

    try:
        manifest = load_manifest(plugin_dir)
    except Exception as exc:
        raise BuildError(f"invalid manifest for plugin {plugin_dir.name!r}: {exc}") from exc

    return PluginSource(
        plugin_id=manifest.id,
        directory=plugin_dir,
        manifest_name=manifests[0].name,
    )


def discover_plugins(source_dir: Path) -> list[PluginSource]:
    """Discover valid direct children of ``source_dir`` in stable id order.

    Directories without a root manifest are unrelated assets and are ignored.
    A directory that does declare a manifest but has an invalid one fails loudly.
    Symlinked children are ignored so ``--all`` can never walk outside the source.
    """
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise BuildError(f"plugin source directory does not exist: {source_dir}")

    found: list[PluginSource] = []
    for child in sorted(source_dir.iterdir(), key=lambda path: path.name):
        if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
            continue
        if not _root_manifests(child):
            continue
        found.append(validate_plugin_dir(child))
    return sorted(found, key=lambda plugin: plugin.plugin_id)


def select_plugins(source_dir: Path, plugin_ids: Iterable[str]) -> list[PluginSource]:
    """Resolve explicit, traversal-safe plugin ids under ``source_dir``."""
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise BuildError(f"plugin source directory does not exist: {source_dir}")

    selected: list[PluginSource] = []
    for plugin_id in sorted(set(plugin_ids)):
        if not PLUGIN_ID_RE.fullmatch(plugin_id or ""):
            raise BuildError(f"invalid plugin id: {plugin_id!r}")
        selected.append(validate_plugin_dir(source_dir / plugin_id))
    return selected


def _is_excluded(relative_path: Path) -> bool:
    # A Git worktree/submodule commonly stores ``.git`` as a FILE containing an
    # internal checkout path. Exclude cache/control names in every position,
    # including the basename, so that metadata never leaks into the archive.
    if any(part in EXCLUDED_DIR_NAMES for part in relative_path.parts):
        return True
    if relative_path.name in EXCLUDED_FILE_NAMES:
        return True
    lower_name = relative_path.name.lower()
    # Be deliberately broad: dotenv variants such as .env.production, .envrc
    # and .env-local can all carry credentials and never belong in a plugin zip.
    if lower_name.startswith(".env"):
        return True
    return lower_name.endswith(EXCLUDED_FILE_SUFFIXES)


def plugin_files(source: PluginSource) -> list[tuple[str, Path]]:
    """Return included ``(archive_name, path)`` pairs in deterministic order."""
    files: list[tuple[str, Path]] = []
    for path in source.directory.rglob("*"):
        relative = path.relative_to(source.directory)
        if path.is_symlink():
            raise BuildError(
                f"plugin {source.plugin_id!r} contains a symlink, which is unsafe "
                f"to package: {relative.as_posix()}")
        if path.is_dir() or _is_excluded(relative):
            continue
        if not path.is_file():
            raise BuildError(
                f"plugin {source.plugin_id!r} contains a non-regular file: "
                f"{relative.as_posix()}")
        archive_name = relative.as_posix()
        # The import endpoint normalizes Windows separators before validating
        # traversal.  A literal backslash is legal in a Linux filename, but
        # packaging it would create an archive that this app itself refuses to
        # import (and may be interpreted as a separator by other ZIP tools).
        normalized_parts = archive_name.replace("\\", "/").split("/")
        if (
            "\\" in archive_name
            or archive_name.startswith("/")
            or ".." in normalized_parts
        ):
            raise BuildError(f"unsafe archive path: {archive_name}")
        files.append((archive_name, path))

    files.sort(key=lambda item: item[0])
    names = [name for name, _ in files]
    if source.manifest_name not in names:
        raise BuildError(
            f"root manifest {source.manifest_name!r} was unexpectedly excluded")
    return files


def build_archive_bytes(source: PluginSource) -> tuple[bytes, int]:
    """Build deterministic ZIP bytes for a validated plugin source."""
    files = plugin_files(source)
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for archive_name, path in files:
            info = zipfile.ZipInfo(archive_name, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix; keeps external_attr interpretation stable.
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)

    payload = buffer.getvalue()
    _validate_archive(payload, source)
    return payload, len(files)


def _validate_archive(payload: bytes, source: PluginSource) -> None:
    """Assert the generated payload is readable and has one root manifest."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            bad_member = archive.testzip()
    except zipfile.BadZipFile as exc:  # pragma: no cover - defensive backstop
        raise BuildError(f"generated invalid ZIP for {source.plugin_id!r}") from exc

    if bad_member is not None:  # pragma: no cover - writestr should make this impossible
        raise BuildError(
            f"generated corrupt ZIP for {source.plugin_id!r}: {bad_member}")
    if names != sorted(names) or len(names) != len(set(names)):
        raise BuildError(
            f"generated ZIP for {source.plugin_id!r} has unstable or duplicate members")
    root_manifests = [name for name in names
                      if "/" not in name and name in MANIFEST_NAMES]
    if root_manifests != [source.manifest_name]:
        raise BuildError(
            f"generated ZIP for {source.plugin_id!r} must contain exactly one "
            f"manifest at its root")


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def build_plugin(source: PluginSource, output_dir: Path, *,
                 check: bool = False) -> BuildResult:
    """Build or verify one plugin archive without ever leaving a partial ZIP."""
    output_dir = output_dir.resolve()
    if _inside(output_dir, source.directory):
        raise BuildError(
            f"output directory may not live inside plugin {source.plugin_id!r}: "
            f"{output_dir}")

    payload, file_count = build_archive_bytes(source)
    digest = hashlib.sha256(payload).hexdigest()
    output_path = output_dir / f"{source.plugin_id}-plugin.zip"
    existing = output_path.read_bytes() if output_path.is_file() else None
    mode_current = (
        os.name == "nt"
        or existing is None
        or stat.S_IMODE(output_path.stat().st_mode) == 0o644
    )

    if check:
        status = "current" if existing == payload and mode_current else (
            "missing" if existing is None else "outdated")
    elif existing == payload:
        if mode_current:
            status = "unchanged"
        else:
            output_path.chmod(0o644)
            status = "repaired"
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                dir=output_dir,
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                # NamedTemporaryFile defaults to 0600.  The finished artifact
                # may be collected/served by a different process user, so make
                # its stable public-artifact mode explicit before the rename.
                if os.name != "nt" and hasattr(os, "fchmod"):
                    os.fchmod(temporary.fileno(), 0o644)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, output_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        status = "built"

    return BuildResult(
        plugin_id=source.plugin_id,
        output_path=output_path,
        file_count=file_count,
        size=len(payload),
        sha256=digest,
        status=status,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "plugin_ids",
        nargs="*",
        metavar="PLUGIN_ID",
        help="plugin ids under the source directory",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="discover and select every plugin with a valid root manifest",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list discoverable plugin ids without creating archives",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify existing archives; do not create or update files",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help=f"plugin source root (default: {DEFAULT_SOURCE_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"archive output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        discovered = discover_plugins(args.source_dir) if (args.all or args.list) else None
        if args.list:
            for plugin in discovered or []:
                print(plugin.plugin_id)
            return 0
        if args.all and args.plugin_ids:
            parser.error("use either explicit PLUGIN_IDs or --all, not both")
        if not args.all and not args.plugin_ids:
            parser.error("provide at least one PLUGIN_ID or use --all")

        selected = discovered if args.all else select_plugins(
            args.source_dir, args.plugin_ids)
        if not selected:
            raise BuildError(f"no plugins found under {args.source_dir.resolve()}")

        failed_check = False
        for source in selected:
            result = build_plugin(source, args.output_dir, check=args.check)
            print(
                f"{result.status:9} {result.plugin_id:<24} "
                f"{result.file_count:>4} files  {result.size:>8} bytes  "
                f"sha256:{result.sha256[:12]}  {result.output_path}")
            failed_check = failed_check or result.status in {"missing", "outdated"}
        return 1 if failed_check else 0
    except (BuildError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
