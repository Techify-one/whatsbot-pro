"""Remove a coluna morta ``ai_variables.category`` (plano 31 F7 / A2).

A coluna nasceu com a tabela mas nunca ganhou UI nem leitura: ``VariableForm``
só edita name/value, ``as_map()``/``render_template`` não a consultam e o
endpoint apenas repassava ``""``. Decisão explícita do plano 31 (D4): remover
por completo — endpoint, repo, schema e banco (sem camada de compat, D8).

Guardada + idempotente + reversível (o downgrade re-adiciona a coluna com o
mesmo default; os valores — sempre vazios — não são preserváveis nem precisam).

Revision ID: 0037_drop_ai_variables_category
Revises: 0036_atend_open_unique
Create Date: 2026-07-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0037_drop_ai_variables_category"
down_revision: Union[str, Sequence[str], None] = "0036_atend_open_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_category(conn) -> bool:
    insp = sa.inspect(conn)
    if not insp.has_table("ai_variables"):
        return False
    return any(c["name"] == "category" for c in insp.get_columns("ai_variables"))


def upgrade() -> None:
    conn = op.get_bind()
    if _has_category(conn):
        op.drop_column("ai_variables", "category")


def downgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table("ai_variables") and not _has_category(conn):
        op.add_column(
            "ai_variables",
            sa.Column("category", sa.Text(), nullable=False, server_default=""),
        )
