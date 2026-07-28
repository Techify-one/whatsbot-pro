"""messages.execution_id — link preciso resposta → execução (plano 51 · 01 F1)

Dado um msg_id de resposta da IA, recuperar a execução que a produziu em O(1)
e sem ambiguidade (o match legado é fuzzy por telefone+janela de ts). FK LÓGICA
(sem constraint, igual a sent_by_user_id) — execução é log histórico e não deve
cascatear/travar o INSERT da mensagem. Aditivo e nullable: linhas legadas ficam
NULL e o consumidor cai no fuzzy (DL2).

Revision ID: 0053_message_execution_id
Revises: 0052_drop_web_password
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0053_message_execution_id"
down_revision: Union[str, Sequence[str], None] = "0052_drop_web_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("execution_id", sa.Integer(), nullable=True))
    op.create_index("idx_msg_execution", "messages", ["execution_id"])


def downgrade() -> None:
    op.drop_index("idx_msg_execution", table_name="messages")
    op.drop_column("messages", "execution_id")
