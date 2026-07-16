"""ai_variables versionadas — history + rollback (plano 51 · 01 F3)

ai_variables era a ÚNICA entidade de IA sem versionamento (upsert puro) — a
melhoria agêntica precisa reverter qualquer coisa que edite (paridade D4 com
ai_agents/ai_tools). Coluna version + tabela ai_variables_history no molde
exato de ai_tools_history.

Revision ID: 0054_ai_variables_versioning
Revises: 0053_message_execution_id
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0054_ai_variables_versioning"
down_revision: Union[str, Sequence[str], None] = "0053_message_execution_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_variables",
                  sa.Column("version", sa.Integer(), nullable=False,
                            server_default="1"))
    op.create_table(
        "ai_variables_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_ai_variables_hist", "ai_variables_history",
                    ["name", "version"])


def downgrade() -> None:
    op.drop_index("idx_ai_variables_hist", table_name="ai_variables_history")
    op.drop_table("ai_variables_history")
    op.drop_column("ai_variables", "version")
