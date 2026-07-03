"""SQLAlchemy engine factory.

Postgres-only (plano 29 Eixo C): the DATABASE_URL comes EXCLUSIVELY from the
``DATABASE_URL`` environment variable (``.env`` via launcher, Docker/Coolify
envs). There is NO SQLite fallback and NO ``storages/database.json`` override
anymore — a missing/non-Postgres URL fails the boot with an actionable error.
Only the engine is exposed; everything else (connection pooling, dialect setup)
is internal.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


# ── Module-level state ────────────────────────────────────────────────────

_engine: Optional[Engine] = None
_db_url: Optional[str] = None


# ── URL resolution ────────────────────────────────────────────────────────

def _redact(url: str) -> str:
    """Strip credentials from a URL for logs/errors (never leak the password)."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        _creds, host_part = rest.split("@", 1)
        return f"{scheme}://***@{host_part}"
    return url


_MISSING_URL_MSG = (
    "DATABASE_URL não configurada. O WhatsBot é Postgres-only (plano 29): "
    "configure a variável de ambiente DATABASE_URL="
    "postgresql+psycopg://usuario:senha@host:5432/whatsbot "
    "(via .env no dev/Docker, ou nas envs do Coolify) e reinicie."
)


def resolve_database_url(storages_dir: Path | None = None) -> str:
    """Resolve the Postgres URL from the environment — fail-fast otherwise.

    Plano 29 C0/C4: no SQLite default and no ``database.json`` override. A
    missing URL or a non-Postgres dialect raises ``RuntimeError`` with an
    actionable message instead of silently booting on a local file DB.
    ``storages_dir`` is accepted for call-site compatibility and ignored.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(_MISSING_URL_MSG)
    if not url.startswith("postgresql"):
        raise RuntimeError(
            f"DATABASE_URL inválida ({_redact(url)}): o WhatsBot é Postgres-only "
            f"(plano 29). Use postgresql+psycopg://usuario:senha@host:5432/whatsbot."
        )
    logger.info("Using DATABASE_URL from environment")
    return url


# ── Engine lifecycle ──────────────────────────────────────────────────────

def init_engine(url: str) -> Engine:
    """Create the module-level engine. Idempotent if called with the same URL."""
    global _engine, _db_url

    if _engine is not None and _db_url == url:
        return _engine

    if _engine is not None:
        _engine.dispose()
        _engine = None

    connect_args: dict = {}
    if url.startswith("postgresql+psycopg"):
        # Disable client-side prepared statements. Managed Postgres services
        # like Neon and Supabase expose a PgBouncer pooler in transaction
        # mode by default, which assigns a different backend per
        # transaction. psycopg3's prepared statement cache (default
        # ``prepare_threshold=5``) breaks under that routing: a name
        # registered on backend A is referenced from backend B and either
        # "does not exist" or collides. The cost of disabling prepared
        # statements is a small per-query overhead (re-parsing on the
        # server) — irrelevant at WhatsBot's scale and worth the
        # plug-and-play compatibility with any pooled endpoint.
        connect_args["prepare_threshold"] = None

    _engine = create_engine(
        url,
        future=True,
        connect_args=connect_args,
        # pool_pre_ping helps survive Postgres connection drops (idle timeouts).
        pool_pre_ping=True,
    )

    _db_url = url
    logger.info("Database engine initialized (dialect=%s)", _engine.dialect.name)
    return _engine


def get_engine() -> Engine:
    """Return the initialized engine. Raises if ``init_engine`` was never called."""
    if _engine is None:
        raise RuntimeError("Engine not initialized. Call init_engine() first.")
    return _engine


def get_database_url() -> str:
    """Return the URL the engine is bound to."""
    if _db_url is None:
        raise RuntimeError("Engine not initialized.")
    return _db_url


def is_postgres() -> bool:
    return _engine is not None and _engine.dialect.name == "postgresql"


def dispose_engine() -> None:
    """Close the pool (tests and shutdown paths)."""
    global _engine, _db_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _db_url = None
