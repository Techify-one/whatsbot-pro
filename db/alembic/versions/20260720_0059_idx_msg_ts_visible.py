"""Partial index on messages(ts DESC) for the content-search scan (plano 62 F2).

``contact_ids_matching_message`` (db/search/contact_search.py) runs
``SELECT … FROM messages WHERE role NOT IN (…) ORDER BY ts DESC LIMIT 5000``
(MESSAGE_SCAN_CAP). Without a matching index Postgres sorts the whole table on
every sidebar search; with this partial index the ORDER BY ts DESC LIMIT turns
into a reverse index scan with early termination (137 ms → a few ms).

The WHERE predicate lists the SAME 4 roles hardcoded in the scan
(``contact_ids_matching_message``): ``tool_call``, ``system_notice``,
``conversation_event``, ``system``. Note this is deliberately a SUBSET of
``LIST_PANEL_ONLY_ROLES`` (db/repositories/_mapping.py) — the scan keeps
``transcription``/``private_note``/``error`` searchable, so those rows stay in
the index. Excluding the 4 internal roles drops ~21.6% of rows from the index.

Idempotent (IF NOT EXISTS / IF EXISTS) and reversible. Mirrored in
``db/tables.py`` (Index "idx_msg_ts_visible") so autogenerate does not diverge.

Revision ID: 0059_idx_msg_ts_visible
Revises: 0058_merge_p50_p57
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0059_idx_msg_ts_visible"
down_revision: Union[str, Sequence[str], None] = "0058_merge_p50_p57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_ts_visible ON messages (ts DESC) "
        "WHERE role NOT IN "
        "('tool_call', 'system_notice', 'conversation_event', 'system')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_msg_ts_visible")
