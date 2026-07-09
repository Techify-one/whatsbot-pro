"""re-anchor contact_inboxes.source_id/source_jid for non-gowa inboxes

Plano 42 A2. Strips the WhatsApp JID suffix (@s.whatsapp.net / @g.us / @lid)
from ``contact_inboxes.source_id`` and ``source_jid`` for inboxes whose channel
provider is NOT 'gowa' (telegram, whatsapp_cloud, test and plugin providers use
bare native ids — plano 42 A1). GOWA keeps the full JID (that IS its native id).

Collisions — two contact_inboxes in the same inbox that strip to the same bare
source_id — are consolidated: conversations (``atendimentos``) of the duplicates
are re-pointed to the canonical (lowest id) row, the duplicates are deleted, then
the survivors are bulk-stripped. Order matters: re-point BEFORE delete (the FK is
ON DELETE CASCADE, so deleting first would take the conversations + messages with
it); delete BEFORE the bulk strip (so the strip can never violate the unique
index ``uq_contact_inbox_inbox_source`` on ``(inbox_id, source_id)``).

Revision ID: 0046_source_id_native
Revises: 0045_mentions
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0046_source_id_native"
down_revision: Union[str, Sequence[str], None] = "0045_mentions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Anchored WhatsApp JID suffixes that a NON-GOWA source_id may carry from the old
# blind ``ContactMemory._jid`` (pre-plano-42). Bare / native ids pass through.
_SUFFIX_RE = r"@(s\.whatsapp\.net|g\.us|lid)$"


def _strip_nongowa_source_ids(conn) -> None:
    """Re-anchor + consolidate on the given connection (testable in isolation)."""
    # 1a. Re-point conversations of each duplicate contact_inbox to the canonical
    #     (MIN(id)) of its (inbox_id, stripped source_id) group. Only changes
    #     contact_inbox_id -> cannot violate uq_atend_open_contact_inbox (that
    #     partial unique keys on contact_id+inbox_id, both unchanged). MUST run
    #     before the delete, else ON DELETE CASCADE takes the conversations with it.
    conn.execute(sa.text(rf"""
        WITH nongowa AS (
            SELECT ci.id, ci.inbox_id,
                   regexp_replace(ci.source_id, '{_SUFFIX_RE}', '') AS bare
            FROM contact_inboxes ci
            JOIN inboxes  i ON i.id = ci.inbox_id
            JOIN channels c ON c.id = i.channel_id
            WHERE c.provider <> 'gowa'
        ),
        grp AS (
            SELECT inbox_id, bare, MIN(id) AS canonical_id
            FROM nongowa
            GROUP BY inbox_id, bare
            HAVING COUNT(*) > 1
        ),
        dup AS (
            SELECT n.id AS dup_id, g.canonical_id
            FROM nongowa n
            JOIN grp g ON g.inbox_id = n.inbox_id AND g.bare = n.bare
            WHERE n.id <> g.canonical_id
        )
        UPDATE atendimentos a
        SET contact_inbox_id = dup.canonical_id,
            updated_at = EXTRACT(EPOCH FROM now())
        FROM dup
        WHERE a.contact_inbox_id = dup.dup_id
    """))

    # 1b. Delete the (now conversation-free) duplicate contact_inboxes so their
    #     future stripped source_id no longer collides with the canonical's.
    conn.execute(sa.text(rf"""
        WITH nongowa AS (
            SELECT ci.id, ci.inbox_id,
                   regexp_replace(ci.source_id, '{_SUFFIX_RE}', '') AS bare
            FROM contact_inboxes ci
            JOIN inboxes  i ON i.id = ci.inbox_id
            JOIN channels c ON c.id = i.channel_id
            WHERE c.provider <> 'gowa'
        ),
        grp AS (
            SELECT inbox_id, bare, MIN(id) AS canonical_id
            FROM nongowa
            GROUP BY inbox_id, bare
            HAVING COUNT(*) > 1
        ),
        dup AS (
            SELECT n.id AS dup_id
            FROM nongowa n
            JOIN grp g ON g.inbox_id = n.inbox_id AND g.bare = n.bare
            WHERE n.id <> g.canonical_id
        )
        DELETE FROM contact_inboxes
        WHERE id IN (SELECT dup_id FROM dup)
    """))

    # 2. Bulk-strip survivors' source_id AND source_jid. After 1a/1b no two
    #    survivors in one inbox strip to the same value, so
    #    uq_contact_inbox_inbox_source holds throughout the single UPDATE.
    conn.execute(sa.text(rf"""
        UPDATE contact_inboxes ci
        SET source_id  = regexp_replace(ci.source_id, '{_SUFFIX_RE}', ''),
            source_jid = regexp_replace(COALESCE(ci.source_jid, ci.source_id),
                                        '{_SUFFIX_RE}', ''),
            updated_at = EXTRACT(EPOCH FROM now())
        FROM inboxes i
        JOIN channels c ON c.id = i.channel_id
        WHERE ci.inbox_id = i.id
          AND c.provider <> 'gowa'
          AND (ci.source_id  ~ '{_SUFFIX_RE}'
            OR ci.source_jid ~ '{_SUFFIX_RE}')
    """))


def upgrade() -> None:
    _strip_nongowa_source_ids(op.get_bind())


def downgrade() -> None:
    # No-op: the per-row original suffix (@s.whatsapp.net vs @lid vs @g.us) is not
    # recoverable and consolidated duplicate rows were deleted. Data-only forward
    # migration; the new bare source_id IS the provider's true native id.
    pass
