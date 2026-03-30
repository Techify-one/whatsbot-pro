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
    _run_migrations(conn)
    logger.info("Database initialized at %s", db_path)


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    # Migration: add archived_by_app column to contacts
    cols = {row[1] for row in conn.execute("PRAGMA table_info(contacts)").fetchall()}
    if "archived_by_app" not in cols:
        conn.execute("ALTER TABLE contacts ADD COLUMN archived_by_app INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        logger.info("Migration: added archived_by_app column to contacts")


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
