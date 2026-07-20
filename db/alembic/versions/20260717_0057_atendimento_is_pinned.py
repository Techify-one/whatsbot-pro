"""Fixar por CONVERSA — adiciona ``atendimentos.is_pinned`` (plano 54).

O "Fixar conversa" do menu passa a ser por atendimento (não mais por contato,
``contacts.is_pinned``), igual ao Arquivar. A coluna espelha ``is_archived``:
``INTEGER NOT NULL DEFAULT 0``, ortogonal ao status. Rows existentes recebem 0
(nenhuma fixada). ``contacts.is_pinned`` fica intacta (usada por outras telas).

Revision ID: 0057_atend_is_pinned
Revises: 0056_unbind_ai_off_agent
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057_atend_is_pinned"
down_revision: Union[str, Sequence[str], None] = "0056_unbind_ai_off_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "atendimentos",
        sa.Column("is_pinned", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("atendimentos", "is_pinned")
