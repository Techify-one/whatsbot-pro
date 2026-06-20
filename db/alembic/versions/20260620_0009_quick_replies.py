"""quick_replies table (plano 04 — respostas rápidas)

Global single list of canned responses (P42: no scope; P47: plain text).
``short_code`` is UNIQUE global (P41), stored without the leading slash.

Revision ID: 0009_quick_replies
Revises: 0008_plugin_installed_deps
Create Date: 2026-06-20

NOTE (P82): down_revision is the real head at implementation time. Slots 0007/0008
were consumed by ai_engine_tables + plugin_installed_deps, so this chains linearly
from 0008_plugin_installed_deps — never reuse 0006/0007/0008 as a new slot.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_quick_replies"
down_revision: Union[str, Sequence[str], None] = "0008_plugin_installed_deps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quick_replies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("short_code", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("short_code", name="uq_quick_replies_short_code"),
    )


def downgrade() -> None:
    op.drop_table("quick_replies")
