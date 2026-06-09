"""add reactions JSON to messages

Revision ID: 0003_message_reactions
Revises: 0002_message_revoked
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_message_reactions"
down_revision: Union[str, Sequence[str], None] = "0002_message_revoked"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reactions", sa.Text))


def downgrade() -> None:
    op.drop_column("messages", "reactions")
