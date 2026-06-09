"""add reply_to_msg_id to messages

Revision ID: 0004_message_reply_to
Revises: 0003_message_reactions
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_message_reply_to"
down_revision: Union[str, Sequence[str], None] = "0003_message_reactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reply_to_msg_id", sa.Text))


def downgrade() -> None:
    op.drop_column("messages", "reply_to_msg_id")
