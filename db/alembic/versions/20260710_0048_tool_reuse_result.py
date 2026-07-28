"""tool_overrides.reuse_result — toggle por-tool "reaproveitar resultado" (plano 47)

Coluna GLOBAL por-tool: quando ligada, o ``tool_memory`` reinjeta o RESULTADO da
tool (não só nome+args) no bloco compacto do turno seguinte, para a IA responder
follow-ups sem re-consultar. Aditivo e NOT NULL com ``server_default='0'`` —
rows existentes nascem 0, então o comportamento é byte-idêntico ao legado.

Revision ID: 0048_tool_reuse_result
Revises: 0047_source_id_native
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0048_tool_reuse_result"
down_revision: Union[str, Sequence[str], None] = "0047_source_id_native"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tool_overrides",
        sa.Column("reuse_result", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("tool_overrides", "reuse_result")
