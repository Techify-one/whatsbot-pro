"""Saved conversation filters (filtros salvos por usuário, estilo Chatwoot).

Cada usuário pode nomear e persistir um ou mais presets de filtro da caixa de
entrada. ``user_id`` é NULL em sessões legadas single-password (sem identidade de
usuário) — nesse caso os presets são compartilhados naquela instalação. ``spec``
guarda o snapshot completo do filtro (status/atribuição/ordenação/tags/avançado).

Revision ID: 0025_saved_conversation_filters
Revises: 0024_plugin_permissions
Create Date: 2026-06-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0025_saved_conversation_filters"
down_revision: Union[str, Sequence[str], None] = "0024_plugin_permissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, table: str) -> bool:
    return sa.inspect(conn).has_table(table)


def _json_type():
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_table(conn, "saved_conversation_filters"):
        op.create_table(
            "saved_conversation_filters",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("spec", _json_type(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index(
            "idx_saved_filters_user",
            "saved_conversation_filters",
            ["user_id", "position"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_table(conn, "saved_conversation_filters"):
        op.drop_index("idx_saved_filters_user", table_name="saved_conversation_filters")
        op.drop_table("saved_conversation_filters")
