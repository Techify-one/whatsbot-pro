"""Database bootstrap.

This module is the entry point used by the rest of the app (``main.py``,
``server/dev.py``, tests). It:

* resolves the Postgres URL from the ``DATABASE_URL`` env (plano 29 — fail-fast
  when missing or non-Postgres),
* creates the module-level engine via ``db.engine.init_engine``,
* runs every pending Alembic migration up to ``head``.

Direct ``sqlite3`` access is gone. Repositories use ``db.engine.get_engine()``
plus ``with engine.begin() as conn:`` for explicit transactions. The legacy
``get_db()`` symbol is preserved only as a temporary shim for third-party
plugins that have not migrated to the new API.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from db.engine import (
    get_engine,
    init_engine,
    resolve_database_url,
)

logger = logging.getLogger(__name__)


# ── Public bootstrap API ──────────────────────────────────────────────────

def init_db(*, storages_dir: Optional[Path] = None) -> None:
    """Initialize the engine and bring the schema up to date.

    ``storages_dir`` is kept for call-site compatibility (dados do GOWA e mídia
    continuam vivendo lá) — a URL do banco vem exclusivamente da env.
    """
    if storages_dir is None:
        storages_dir = Path("storages").resolve()
    storages_dir.mkdir(parents=True, exist_ok=True)
    url = resolve_database_url(storages_dir)

    init_engine(url)
    _run_alembic_upgrade()
    logger.info("Database ready (%s)", _describe_url(url))


def _describe_url(url: str) -> str:
    """Return a redacted URL for logs (strip user:password)."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host_part = rest.split("@", 1)
        return f"{scheme}://***@{host_part}"
    return url


def _run_alembic_upgrade() -> None:
    """Bring the bound database to the latest Alembic revision.

    For brand-new databases the baseline revision creates every table. O stamp
    automático de bancos SQLite pré-Alembic morreu junto com o caminho SQLite
    (plano 29 Eixo C) — todo banco Postgres nasce via Alembic.
    """
    from alembic import command
    from alembic.config import Config

    engine = get_engine()
    project_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "db" / "alembic"))
    # Pass the engine URL explicitly so the alembic env.py picks the same DB.
    cfg.set_main_option("sqlalchemy.url", str(engine.url).replace("%", "%%"))

    command.upgrade(cfg, "head")


# ── Legacy shim ───────────────────────────────────────────────────────────

def get_db():
    """Deprecated shim. Returns a DB-API connection from the engine pool.

    The historical contract was a thread-local ``sqlite3.Connection`` reused
    across calls. The new contract is "open one connection per logical unit of
    work using ``with engine.begin() as conn:``" — see the repositories for the
    canonical pattern.

    For Postgres the returned object is a ``psycopg.Connection``, which speaks
    enough DB-API for callers that only run plain SQL with ``?``-style params
    *will break* (psycopg uses ``%s``). New code MUST use the SA engine; this
    shim is kept only so importing it doesn't crash on app boot.
    """
    import warnings

    warnings.warn(
        "db.connection.get_db() is deprecated — use db.engine.get_engine() "
        "and `with engine.begin() as conn:` instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return get_engine().raw_connection()
