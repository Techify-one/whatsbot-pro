"""add installed_deps to plugins

Revision ID: 0008_plugin_installed_deps
Revises: 0007_ai_engine_tables
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_plugin_installed_deps"
down_revision: Union[str, Sequence[str], None] = "0007_ai_engine_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plugins",
        sa.Column("installed_deps", sa.Text, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("plugins", "installed_deps")
