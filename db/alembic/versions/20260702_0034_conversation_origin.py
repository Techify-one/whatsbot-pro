"""Add ``origin`` to atendimentos (real-time list-sync visibility signal).

Plano 28. Adds ``atendimentos.origin`` (``inbound``|``outbound``|``imported``|
``manual``, nullable) — the EXPLICIT visibility signal that replaces the racy
``last_message_ts>0`` proxy in the sidebar gate. A conversation the customer
started (``inbound``) shows on the list at t=0, even before its first message is
persisted by the batch. Backfill: existing conversations WITH a message →
``inbound`` (already visible); empty ones → ``manual`` (stay hidden, preserving
current behaviour). NULL is treated as non-inbound by the gate.

The brand-new-contact double-create race is closed at the APPLICATION level
(``conversation_repo.resolve_for_contact_ex`` re-checks for an open thread inside
the write transaction), NOT via a unique index — the atendimento model
intentionally allows multiple conversations per (contact, inbox), so a DB
constraint would be wrong here.

Guarded + idempotent + reversible.

Revision ID: 0034_conversation_origin
Revises: 0033_core_atendimento
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_conversation_origin"
down_revision: Union[str, Sequence[str], None] = "0033_core_atendimento"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The core conversation table was renamed conversations -> atendimentos in 0033.
_TABLE = "atendimentos"


def _has_table(conn, table: str) -> bool:
    return sa.inspect(conn).has_table(table)


def _has_column(conn, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_table(conn, _TABLE):
        return  # nothing to migrate (should not happen — 0033 guarantees it)
    if not _has_column(conn, _TABLE, "origin"):
        op.add_column(_TABLE, sa.Column("origin", sa.Text(), nullable=True))
        # Backfill: conversations with a visible history came from (or behave like)
        # an inbound; empty ones → 'manual' so the new gate keeps hiding them.
        op.execute(sa.text(
            f"UPDATE {_TABLE} SET origin='inbound' "
            f"WHERE origin IS NULL AND EXISTS "
            f"(SELECT 1 FROM messages WHERE messages.conversation_id = {_TABLE}.id)"
        ))
        op.execute(sa.text(
            f"UPDATE {_TABLE} SET origin='manual' WHERE origin IS NULL"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, _TABLE, "origin"):
        op.drop_column(_TABLE, "origin")
