"""add has_unread_mention to contacts

Revision ID: 0006_contact_mention
Revises: 0005_contact_pinned
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_contact_mention"
down_revision: Union[str, Sequence[str], None] = "0005_contact_pinned"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("has_unread_mention", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("contacts", "has_unread_mention")
