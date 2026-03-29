"""SQLite connection management with thread-local storage."""

import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_db_path: Path | None = None
_local = threading.local()
_SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def init_db(db_path: Path) -> None:
    """Initialize the database: set path and create tables."""
    global _db_path
    _db_path = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()
    logger.info("Database initialized at %s", db_path)


def get_db() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (created on first access)."""
    if _db_path is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(_db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn
