"""Add ``kind`` to ai_tools (builtin vs code-in-DB).

``kind='code'`` (default) keeps the existing behaviour: user-authored Python
that runs ISOLATED in a subprocess. ``kind='builtin'`` flags the seeded core
tools (save_contact_info, transfer_to_human, ...) which run IN-PROCESS with the
live ToolContext and are shown in the unified Tools UI with version/edit/history.

The builtin rows themselves are seeded idempotently at boot (reading the current
on-disk source), not here — this migration only adds the column.

Revision ID: 0026_ai_tools_kind
Revises: 0025_saved_conversation_filters
Create Date: 2026-06-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026_ai_tools_kind"
down_revision: Union[str, Sequence[str], None] = "0025_saved_conversation_filters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "ai_tools", "kind"):
        op.add_column(
            "ai_tools",
            sa.Column("kind", sa.Text(), nullable=False, server_default="code"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "ai_tools", "kind"):
        op.drop_column("ai_tools", "kind")
