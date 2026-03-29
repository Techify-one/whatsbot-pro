"""Repository for config key-value storage."""

import json

from db.connection import get_db


def get_all() -> dict:
    """Return all config key-value pairs as a dict (values JSON-decoded)."""
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    result = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            result[row["key"]] = row["value"]
    return result


def get(key: str, default=None):
    """Get a single config value by key."""
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set(key: str, value) -> None:
    """Set a single config value (JSON-encoded)."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def set_many(data: dict) -> None:
    """Set multiple config values at once."""
    conn = get_db()
    conn.executemany(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        [(k, json.dumps(v, ensure_ascii=False)) for k, v in data.items()],
    )
    conn.commit()
