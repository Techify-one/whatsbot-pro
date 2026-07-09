"""add mentions table (private-note @mention of users)

Revision ID: 0045_mentions
Revises: 0044_merge_perms_is_default
Create Date: 2026-07-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0045_mentions"
down_revision: Union[str, Sequence[str], None] = "0044_merge_perms_is_default"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mentions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.Integer,
                  sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.Integer,
                  sa.ForeignKey("atendimentos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.Integer,
                  sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mentioned_user_id", sa.Integer, nullable=False),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("actor_name", sa.Text),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("read_at", sa.Float),
    )
    op.create_index("idx_mentions_user_unread", "mentions",
                    ["mentioned_user_id", "read_at"])
    op.create_index("idx_mentions_conversation", "mentions", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("idx_mentions_conversation", table_name="mentions")
    op.drop_index("idx_mentions_user_unread", table_name="mentions")
    op.drop_table("mentions")
