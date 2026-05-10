"""SQL migration runner for plugins.

Each plugin can ship a ``migrations/`` folder with files named
``NNN_description.sql`` (e.g. ``001_initial.sql``). The runner:

1. Loads the set of versions already applied from ``plugin_migrations``.
2. Reads remaining files in numeric order.
3. Validates that every ``CREATE TABLE`` / ``ALTER TABLE`` references a
   table whose name starts with ``plugin_<id>_`` — guards against accidental
   collisions with core tables.
4. Executes the SQL inside a transaction and records the migration.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from db.connection import get_db
from db.repositories import plugin_repo

from plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


_MIG_FILE_RE = re.compile(r"^(\d+)_.+\.sql$", re.IGNORECASE)
_TABLE_OP_RE = re.compile(
    r"\b(?:CREATE\s+(?:TEMP\s+|TEMPORARY\s+)?TABLE|ALTER\s+TABLE|DROP\s+TABLE|"
    r"CREATE\s+(?:UNIQUE\s+)?INDEX|DROP\s+INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+|IF\s+EXISTS\s+)?"
    r"[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?",
    re.IGNORECASE,
)


def run_pending_migrations(manifest: PluginManifest, plugin_dir: Path) -> list[int]:
    """Apply every pending migration. Returns the list of versions applied."""
    if not manifest.migrations:
        return []
    mig_dir = plugin_dir / manifest.migrations
    if not mig_dir.is_dir():
        return []

    pid = manifest.id
    table_prefix = f"plugin_{pid}_"
    applied = plugin_repo.applied_migrations(pid)
    pending: list[tuple[int, Path]] = []
    for path in sorted(mig_dir.iterdir()):
        if not path.is_file():
            continue
        m = _MIG_FILE_RE.match(path.name)
        if not m:
            logger.warning("Plugin %s: ignoring migration file %s (bad name)", pid, path.name)
            continue
        version = int(m.group(1))
        if version in applied:
            continue
        pending.append((version, path))
    pending.sort()

    applied_now: list[int] = []
    conn = get_db()
    for version, path in pending:
        sql = path.read_text(encoding="utf-8")
        _validate_sql_prefix(sql, pid, table_prefix, path.name)
        try:
            conn.executescript(sql)
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise RuntimeError(
                f"Plugin {pid} migration {path.name} failed: {e}"
            ) from e
        plugin_repo.record_migration(pid, version)
        applied_now.append(version)
        logger.info("Plugin %s: applied migration %s", pid, path.name)

    return applied_now


def _validate_sql_prefix(sql: str, plugin_id: str, prefix: str, filename: str) -> None:
    """Ensure every CREATE/ALTER/DROP TABLE in ``sql`` uses ``prefix``."""
    # Strip line comments so they don't trip the regex.
    cleaned = re.sub(r"--[^\n]*", "", sql)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    for match in _TABLE_OP_RE.finditer(cleaned):
        target = match.group(1)
        if not target.startswith(prefix):
            raise ValueError(
                f"Plugin {plugin_id} migration {filename}: "
                f"object name '{target}' must start with '{prefix}' "
                f"(use 'plugin_{plugin_id}_<your_table>')"
            )
