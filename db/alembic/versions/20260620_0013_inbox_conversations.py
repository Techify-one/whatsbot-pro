"""Inbox e Conversas: inboxes/contact_inboxes/conversations/conversation_counters (plano 01 Fase 1)

Cria o modelo de 3 níveis (Contact → ContactInbox → Conversation), adiciona
messages.conversation_id (aditivo, nullable) e faz o BACKFILL: uma conversa por
contato existente (inclui grupos), com display_id sequencial, vinculando as
mensagens. NÃO mexe no unique de contacts.phone (adiado — operação de maior risco).

Revision ID: 0013_inbox_conversations
Revises: 0012_rbac_users
Create Date: 2026-06-20

NOTE (P82): down_revision é o head real no momento. conversations.custom_attributes
é JSON/JSONB nativo (NUNCA TEXT — coord plano 05). Timestamps epoch float (P56).
"""
import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0013_inbox_conversations"
down_revision: Union[str, Sequence[str], None] = "0012_rbac_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    now = time.time()

    op.create_table(
        "inboxes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, server_default="WhatsApp"),
        sa.Column("channel_type", sa.Text(), nullable=False, server_default="whatsapp"),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("agent_bot_enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "contact_inboxes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("contact_id", sa.Integer(),
                  sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inbox_id", sa.Integer(),
                  sa.ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_jid", sa.Text(), nullable=True),
        sa.Column("source_lid", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("inbox_id", "source_id", name="uq_contact_inbox_inbox_source"),
    )
    op.create_index("idx_contact_inbox_contact", "contact_inboxes", ["contact_id"])
    op.create_index("idx_contact_inbox_lid", "contact_inboxes", ["inbox_id", "source_lid"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("display_id", sa.Integer(), nullable=False),
        sa.Column("inbox_id", sa.Integer(),
                  sa.ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.Integer(),
                  sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_inbox_id", sa.Integer(),
                  sa.ForeignKey("contact_inboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("is_archived", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assignee_user_id", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("ai_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("opened_at", sa.Float(), nullable=False),
        sa.Column("resolved_at", sa.Float(), nullable=True),
        sa.Column("waiting_since", sa.Float(), nullable=True),
        sa.Column("last_activity_at", sa.Float(), nullable=False),
        sa.Column("custom_attributes", _json(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("display_id", name="uq_conv_display_id"),
    )
    op.create_index("idx_conv_inbox_status", "conversations", ["inbox_id", "status"])
    op.create_index("idx_conv_assignee_status", "conversations", ["assignee_user_id", "status"])
    op.create_index("idx_conv_contact", "conversations", ["contact_id"])
    op.create_index("idx_conv_contact_inbox", "conversations", ["contact_inbox_id"])
    op.create_index("idx_conv_last_activity", "conversations", ["last_activity_at"])
    op.create_index("idx_conv_archived", "conversations", ["is_archived"])

    op.create_table(
        "conversation_counters",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False, server_default="1"),
    )

    # NOTE: plain column without inline FK — SQLite não suporta ADD COLUMN com
    # constraint de FK (ALTER). O modelo declarativo em db/tables.py mantém a FK;
    # a integridade de conversation_id é gerida em código (repos).
    op.add_column("messages", sa.Column("conversation_id", sa.Integer(), nullable=True))
    op.create_index("idx_msg_conversation_ts", "messages", ["conversation_id", "ts"])

    # ── Seed inbox default + counter ──────────────────────────────────
    inboxes_t = sa.table("inboxes", sa.column("id"), sa.column("name"),
                         sa.column("channel_type"), sa.column("channel_id"),
                         sa.column("agent_bot_enabled"), sa.column("created_at"),
                         sa.column("updated_at"))
    op.bulk_insert(inboxes_t, [{
        "id": 1, "name": "WhatsApp", "channel_type": "whatsapp", "channel_id": "default",
        "agent_bot_enabled": 1, "created_at": now, "updated_at": now,
    }])
    counters_t = sa.table("conversation_counters", sa.column("name"), sa.column("next_value"))
    op.bulk_insert(counters_t, [{"name": "conversation_display_id", "next_value": 1}])

    # ── Backfill: 1 conversa por contato (idempotente) ────────────────
    conn = op.get_bind()
    if (conn.execute(sa.text("SELECT COUNT(*) FROM conversations")).scalar() or 0) > 0:
        return
    contacts = conn.execute(sa.text(
        "SELECT id, phone, is_group, is_archived, updated_at FROM contacts ORDER BY id"
    )).mappings().all()

    display_id = 0
    for c in contacts:
        suffix = "@g.us" if c["is_group"] else "@s.whatsapp.net"
        jid = f"{c['phone']}{suffix}"
        conn.execute(sa.text(
            "INSERT INTO contact_inboxes (contact_id, inbox_id, source_id, source_jid, "
            "created_at, updated_at) VALUES (:cid, 1, :jid, :jid, :now, :now)"),
            {"cid": c["id"], "jid": jid, "now": now})
        ci_id = conn.execute(sa.text(
            "SELECT id FROM contact_inboxes WHERE inbox_id=1 AND source_id=:jid"),
            {"jid": jid}).scalar()
        last_ts = conn.execute(sa.text(
            "SELECT COALESCE(MAX(ts), :upd) FROM messages WHERE contact_id=:cid"),
            {"cid": c["id"], "upd": c["updated_at"]}).scalar() or c["updated_at"]
        display_id += 1
        conn.execute(sa.text(
            "INSERT INTO conversations (display_id, inbox_id, contact_id, contact_inbox_id, "
            "status, is_archived, ai_active, opened_at, last_activity_at, custom_attributes, "
            "created_at, updated_at) VALUES (:disp, 1, :cid, :ciid, 'open', :arch, 1, :lts, "
            ":lts, '{}', :now, :now)"),
            {"disp": display_id, "cid": c["id"], "ciid": ci_id,
             "arch": c["is_archived"], "lts": last_ts, "now": now})
        conv_id = conn.execute(sa.text(
            "SELECT id FROM conversations WHERE display_id=:disp"),
            {"disp": display_id}).scalar()
        conn.execute(sa.text(
            "UPDATE messages SET conversation_id=:conv WHERE contact_id=:cid"),
            {"conv": conv_id, "cid": c["id"]})

    conn.execute(sa.text(
        "UPDATE conversation_counters SET next_value=:nv WHERE name='conversation_display_id'"),
        {"nv": display_id + 1})


def downgrade() -> None:
    op.drop_index("idx_msg_conversation_ts", table_name="messages")
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("conversation_id")
    op.drop_index("idx_conv_archived", table_name="conversations")
    op.drop_index("idx_conv_last_activity", table_name="conversations")
    op.drop_index("idx_conv_contact_inbox", table_name="conversations")
    op.drop_index("idx_conv_contact", table_name="conversations")
    op.drop_index("idx_conv_assignee_status", table_name="conversations")
    op.drop_index("idx_conv_inbox_status", table_name="conversations")
    op.drop_table("conversations")
    op.drop_table("conversation_counters")
    op.drop_index("idx_contact_inbox_lid", table_name="contact_inboxes")
    op.drop_index("idx_contact_inbox_contact", table_name="contact_inboxes")
    op.drop_table("contact_inboxes")
    op.drop_table("inboxes")
