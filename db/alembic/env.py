"""Alembic runtime environment.

The engine is taken from ``db.engine`` so the same URL resolution (env
``DATABASE_URL``, Postgres-only — plano 29) applies whether migrations run on
app boot or via the ``alembic`` CLI. Batch mode (``render_as_batch``) morreu
junto com o SQLite: Postgres tem ALTER TABLE completo.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from db import engine as engine_module
from db.tables import metadata as target_metadata

config = context.config
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        # Logging config is optional — never fail boot because of it.
        pass


def _resolve_connectable():
    """Reuse the app engine if it was already initialized, else build one."""
    try:
        return engine_module.get_engine()
    except RuntimeError:
        pass
    url = config.get_main_option("sqlalchemy.url") or engine_module.resolve_database_url()
    return engine_module.init_engine(url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection, emits SQL)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a live engine."""
    connectable = _resolve_connectable()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
