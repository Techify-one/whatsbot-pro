"""conversation labels (etiquetas próprias da conversa, Onda 3)

Registro global ``conversation_labels`` + atribuição N:N ``conversation_label_links``.
Separadas das tags de contato (decisão: etiquetas pertencem à conversa).

Revision ID: 0020_conversation_labels
Revises: 0019_user_custom_permissions
Create Date: 2026-06-20

NOTE (P82): down_revision is the real head at implementation time —
0019_user_custom_permissions. Linear chain, never reuse an older slot.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_conversation_labels"
down_revision: Union[str, Sequence[str], None] = "0019_user_custom_permissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_labels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False, server_default="#6b7280"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("name", name="uq_conversation_labels_name"),
    )
    op.create_table(
        "conversation_label_links",
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("label_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["label_id"], ["conversation_labels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id", "label_id"),
    )
    op.create_index("idx_conv_label_links_label", "conversation_label_links", ["label_id"])


def downgrade() -> None:
    op.drop_index("idx_conv_label_links_label", table_name="conversation_label_links")
    op.drop_table("conversation_label_links")
    op.drop_table("conversation_labels")
