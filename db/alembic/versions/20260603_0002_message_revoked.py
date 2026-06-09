"""add revoked flag to messages (delete-for-everyone tombstone)

Revision ID: 0002_message_revoked
Revises: 0001_baseline
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_message_revoked"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("revoked", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("messages", "revoked")
