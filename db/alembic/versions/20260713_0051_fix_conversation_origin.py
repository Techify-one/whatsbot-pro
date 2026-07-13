"""Fix ``atendimentos.origin`` for threads mis-backfilled by 0034.

Plano 28 shipped ``atendimentos.origin`` as a sidebar VISIBILITY signal, and its
backfill (migration ``0034_conversation_origin``) stamped EVERY conversation that
already had a message as ``inbound`` — regardless of who actually started it. That
was fine for visibility (a thread with a message is visible anyway), but ``origin``
is now ALSO read as "who started the conversation" (the "Início de conversa" filter:
``inbound`` = customer, else = operator). So an operator-started thread created
before 2026-07-02 wrongly shows up under the "Cliente" filter.

This migration re-derives ``origin`` from the role of the FIRST real message
(``user``/``assistant``, ignoring panel-only roles) of each conversation, in both
directions, but ONLY for conversations that HAVE such a message:

  - first real role == ``assistant`` (operator/AI started) → ``outbound``
  - first real role == ``user``      (customer started)    → ``inbound``

Conversations with no real message yet (t=0 inbound ghosts, empty ``manual``
drafts) are left untouched, so the sidebar visibility gate and the ghost sweep
(both of which only care about origin for message-less rows) are unaffected. New
conversations are already stamped correctly at runtime (agent/memory.py), so this
is a one-shot historical data fix.

Guarded + idempotent. Downgrade is a no-op: the pre-fix (wrong) value cannot be
reconstructed, and re-running upgrade is safe.

Revision ID: 0051_fix_conversation_origin
Revises: 0050_contact_type
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0051_fix_conversation_origin"
down_revision: Union[str, Sequence[str], None] = "0050_contact_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "atendimentos"

# First real (user/assistant) message role of a conversation, ignoring panel-only
# roles. Correlated on the outer row's id.
_FIRST_REAL_ROLE = (
    "SELECT m.role FROM messages m "
    f"WHERE m.conversation_id = {_TABLE}.id "
    "AND m.role IN ('user', 'assistant') "
    "ORDER BY m.ts ASC LIMIT 1"
)


def _has_column(conn, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, _TABLE, "origin"):
        return
    if not sa.inspect(conn).has_table("messages"):
        return
    # Operator/AI actually started the thread → outbound (fixes the 0034 over-stamp).
    op.execute(sa.text(
        f"UPDATE {_TABLE} SET origin='outbound' "
        f"WHERE origin='inbound' AND ({_FIRST_REAL_ROLE}) = 'assistant'"
    ))
    # Customer actually started the thread → inbound (symmetry; no-op on clean data).
    op.execute(sa.text(
        f"UPDATE {_TABLE} SET origin='inbound' "
        f"WHERE origin IN ('outbound', 'manual') AND ({_FIRST_REAL_ROLE}) = 'user'"
    ))


def downgrade() -> None:
    # One-way data fix — the original (wrong) origin cannot be reconstructed.
    pass
