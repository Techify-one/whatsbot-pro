"""Extensions unaccent + pg_trgm, f_unaccent() and trigram search indexes (plano 62 F4).

Enables accent/case-insensitive matching IN SQL, indexable, so the search
``q`` moves from Python post-filtering into the query itself (F5):

- ``unaccent`` + ``pg_trgm`` extensions (both trusted — the DB owner can
  ``CREATE EXTENSION`` without superuser, P5 resolved).
- ``f_unaccent(text)``: IMMUTABLE wrapper over ``public.unaccent`` (the native
  function is STABLE and cannot be used in index expressions).
- GIN trigram indexes on ``contacts.name`` and ``messages.content`` (the
  latter partial, excluding the 4 internal roles the content scan skips —
  same predicate as ``idx_msg_ts_visible``, migration 0059).

Production rollout (P2 decision): the objects are pre-created MANUALLY by the
orchestrator with ``CREATE INDEX CONCURRENTLY`` outside the boot (the GIN
build on ``messages`` is too slow to hold a write lock during deploy). Every
statement here is idempotent (``IF NOT EXISTS`` / ``CREATE OR REPLACE``), so
the boot's ``alembic upgrade head`` becomes a no-op in prod. On fresh installs
(small/empty DB) the boot creates everything itself.

The statements below are the CONTRACT with the DDL applied in production —
do not alter them.

Downgrade drops the indexes and the function but NOT the extensions: they may
be shared with other consumers of the database.

Revision ID: 0060_trgm_unaccent_search
Revises: 0059_idx_msg_ts_visible
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0060_trgm_unaccent_search"
down_revision: Union[str, Sequence[str], None] = "0059_idx_msg_ts_visible"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text\n"
        "  AS $$ SELECT public.unaccent('public.unaccent', $1) $$\n"
        "  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_contacts_name_trgm ON contacts "
        "USING gin (f_unaccent(lower(name)) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_content_trgm ON messages "
        "USING gin (f_unaccent(lower(content)) gin_trgm_ops) "
        "WHERE role NOT IN "
        "('tool_call','system_notice','conversation_event','system')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_msg_content_trgm")
    op.execute("DROP INDEX IF EXISTS idx_contacts_name_trgm")
    op.execute("DROP FUNCTION IF EXISTS f_unaccent(text)")
    # Extensions unaccent/pg_trgm are intentionally NOT dropped: they may be
    # shared with other functions/indexes outside this migration's scope.
