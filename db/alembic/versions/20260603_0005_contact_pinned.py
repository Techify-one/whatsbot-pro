"""add is_pinned to contacts

Revision ID: 0005_contact_pinned
Revises: 0004_message_reply_to
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_contact_pinned"
down_revision: Union[str, Sequence[str], None] = "0004_message_reply_to"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("is_pinned", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("contacts", "is_pinned")
