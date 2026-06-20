"""Inbox por canal: garante 1 inbox para cada channel (plano 11 Fase 1)

Destrava o roteamento de conversa por canal. A inbox default (id=1) já aponta
para o canal ``default`` (gowa) desde a 0013; esta migration cria uma inbox para
todo OUTRO canal existente que ainda não tem uma (ex.: o canal ``whatsapp_cloud``
"teste"), de forma idempotente. Conversas passam a ser separadas por canal porque
``conversation_repo.resolve_for_contact`` resolve a inbox do canal de origem.

Revision ID: 0018_inbox_per_channel
Revises: 0017_audit_log
Create Date: 2026-06-20

NOTE (P82): down_revision é o head real no momento. Aditivo e idempotente — só
insere inbox para canal sem inbox; não duplica conversas nem mexe nas existentes.
"""
import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_inbox_per_channel"
down_revision: Union[str, Sequence[str], None] = "0017_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    now = time.time()
    conn = op.get_bind()

    # Channels that already own an inbox (NULL channel_id is ignored).
    have_inbox = {
        r[0] for r in conn.execute(sa.text(
            "SELECT DISTINCT channel_id FROM inboxes WHERE channel_id IS NOT NULL"
        )).all()
    }

    channels = conn.execute(sa.text(
        "SELECT id, provider, display_name FROM channels ORDER BY id"
    )).mappings().all()

    inboxes_t = sa.table(
        "inboxes",
        sa.column("name"), sa.column("channel_type"), sa.column("channel_id"),
        sa.column("agent_bot_enabled"), sa.column("created_at"), sa.column("updated_at"),
    )

    rows = []
    for ch in channels:
        cid = ch["id"]
        if cid in have_inbox:
            continue
        rows.append({
            "name": ch["display_name"] or cid,
            "channel_type": "whatsapp",
            "channel_id": cid,
            "agent_bot_enabled": 1,
            "created_at": now,
            "updated_at": now,
        })
    if rows:
        op.bulk_insert(inboxes_t, rows)


def downgrade() -> None:
    # Remove only the inboxes this migration could have created: those bound to a
    # non-"default" channel that have no conversations attached (safe to drop).
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM inboxes WHERE channel_id IS NOT NULL AND channel_id <> 'default' "
        "AND id NOT IN (SELECT DISTINCT inbox_id FROM conversations)"
    ))
