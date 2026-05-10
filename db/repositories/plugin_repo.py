"""Repository for the ``plugins`` and ``plugin_migrations`` tables."""

import time

from db.connection import get_db


def list_all() -> list[dict]:
    """Return all known plugins (one row per id, including disabled)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, version, enabled, installed_at, updated_at, load_error "
        "FROM plugins ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get(plugin_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT id, version, enabled, installed_at, updated_at, load_error "
        "FROM plugins WHERE id = ?",
        (plugin_id,),
    ).fetchone()
    return dict(row) if row else None


def upsert(plugin_id: str, version: str, *, enabled: bool | None = None) -> None:
    """Insert or update a plugin row, preserving ``enabled`` if not provided."""
    conn = get_db()
    now = time.time()
    existing = get(plugin_id)
    if existing is None:
        enabled_int = 1 if enabled else 0
        conn.execute(
            "INSERT INTO plugins (id, version, enabled, installed_at, updated_at, load_error) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (plugin_id, version, enabled_int, now, now),
        )
    else:
        if enabled is None:
            enabled_int = existing["enabled"]
        else:
            enabled_int = 1 if enabled else 0
        conn.execute(
            "UPDATE plugins SET version = ?, enabled = ?, updated_at = ?, load_error = NULL "
            "WHERE id = ?",
            (version, enabled_int, now, plugin_id),
        )
    conn.commit()


def set_enabled(plugin_id: str, enabled: bool) -> bool:
    """Toggle ``enabled``. Returns False if the plugin is unknown."""
    conn = get_db()
    if get(plugin_id) is None:
        return False
    conn.execute(
        "UPDATE plugins SET enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, time.time(), plugin_id),
    )
    conn.commit()
    return True


def set_load_error(plugin_id: str, error: str | None) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE plugins SET load_error = ?, updated_at = ? WHERE id = ?",
        (error, time.time(), plugin_id),
    )
    conn.commit()


def delete(plugin_id: str) -> None:
    """Delete plugin row and migration history. Does NOT drop the plugin's tables."""
    conn = get_db()
    conn.execute("DELETE FROM plugin_migrations WHERE plugin_id = ?", (plugin_id,))
    conn.execute("DELETE FROM plugins WHERE id = ?", (plugin_id,))
    conn.commit()


def applied_migrations(plugin_id: str) -> set[int]:
    conn = get_db()
    rows = conn.execute(
        "SELECT version FROM plugin_migrations WHERE plugin_id = ?",
        (plugin_id,),
    ).fetchall()
    return {r["version"] for r in rows}


def record_migration(plugin_id: str, version: int) -> None:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO plugin_migrations (plugin_id, version, applied_at) "
        "VALUES (?, ?, ?)",
        (plugin_id, version, time.time()),
    )
    conn.commit()


def drop_plugin_tables(plugin_id: str) -> list[str]:
    """Drop every table whose name starts with ``plugin_<id>_``. Returns dropped names."""
    conn = get_db()
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name LIKE ? ESCAPE '\\'",
        (f"plugin_{plugin_id}\\_%",),
    ).fetchall()
    dropped = []
    for row in rows:
        name = row["name"]
        # safety: only drop names that actually start with the expected prefix
        if name.startswith(f"plugin_{plugin_id}_"):
            conn.execute(f"DROP TABLE IF EXISTS {name}")
            dropped.append(name)
    conn.commit()
    return dropped
