"""Times (Teams): agrupar atendentes, filtrar e transferir conversa por time.

Aditivo. Duas tabelas novas: ``teams`` (CRUD simples — nome + descrição) e
``team_members`` (N:N usuário↔time, cópia estrutural de ``inbox_members`` —
ver 20260620_0021_inbox_members.py). ``atendimentos.team_id`` já existia desde
o plano 01 (Integer puro, NULLABLE, sem FK, "por robustez de ordem de
migration") mas nunca tinha sido escrita — esta migration finalmente ancora a
FK nela, ``ON DELETE SET NULL`` (D4 do plano 153: deletar um time só limpa a
etiqueta das conversas, nunca quebra uma). Como a coluna nunca foi escrita,
não há linha órfã para migrar.

Revision ID: 0067_teams
Revises: 0066_ai_tools_plugin_id
Create Date: 2026-09-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0067_teams"
down_revision: Union[str, Sequence[str], None] = "0066_ai_tools_plugin_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "team_members",
        sa.Column("team_id", sa.Integer(),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("team_id", "user_id"),
    )
    op.create_index("idx_team_members_user", "team_members", ["user_id"])
    op.create_foreign_key(
        "fk_atend_team_id", "atendimentos", "teams",
        ["team_id"], ["id"], ondelete="SET NULL")
    op.create_index("idx_atend_team_status", "atendimentos", ["team_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_atend_team_status", table_name="atendimentos")
    op.drop_constraint("fk_atend_team_id", "atendimentos", type_="foreignkey")
    op.drop_index("idx_team_members_user", table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("teams")
