"""Mensagens: coluna ``edited_ts`` (timestamp da última edição).

Uma mensagem de saída (operador ou IA) pode ser editada pelo painel (GOWA via
``/message/{id}/update``, WhatsApp Cloud via o messages endpoint com
``message_id``). ``edited_ts`` guarda o epoch da última edição; NULL = nunca
editada. O painel mostra o rótulo "editada" quando setado. Aditivo e nullable.

Revision ID: 0052_message_edited_ts
Revises: 0051_fix_conversation_origin
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0052_message_edited_ts"
down_revision: Union[str, Sequence[str], None] = "0051_fix_conversation_origin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("messages"):
        return set()
    return {c["name"] for c in insp.get_columns("messages")}


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("messages"):
        return
    if "edited_ts" not in _columns(conn):
        op.add_column("messages", sa.Column("edited_ts", sa.Float(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("messages"):
        return
    if "edited_ts" in _columns(conn):
        op.drop_column("messages", "edited_ts")
