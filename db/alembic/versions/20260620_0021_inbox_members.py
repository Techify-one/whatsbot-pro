"""Membros de inbox (agentes que veem/recebem a caixa de um canal)

Aditivo. Nova tabela ``inbox_members`` — relação N:N entre ``inboxes`` e
``users``. Define quais agentes enxergam as conversas de uma inbox (uma por
canal). Um usuário não-admin sem ``conversation.read_all`` só vê as conversas
das inboxes em que é membro; admin e quem tiver ``conversation.read_all``
ignoram a membership e veem tudo.

Editada pela tela de Canais (botão "Editar" → seleção de agentes), gated por
``channel.manage``.

Revision ID: 0021_inbox_members
Revises: 0020_conversation_labels
Create Date: 2026-06-20

NOTE (P82): down_revision is the real head at rebase time —
0020_conversation_labels. Linear chain, never reuse an older slot.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_inbox_members"
down_revision: Union[str, Sequence[str], None] = "0020_conversation_labels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inbox_members",
        sa.Column("inbox_id", sa.Integer(),
                  sa.ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("inbox_id", "user_id"),
    )
    op.create_index("idx_inbox_members_user", "inbox_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_inbox_members_user", table_name="inbox_members")
    op.drop_table("inbox_members")
