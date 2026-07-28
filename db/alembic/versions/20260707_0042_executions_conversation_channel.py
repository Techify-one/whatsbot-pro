"""Execuções: conversation_id + canal desnormalizado (plano 36 F1).

A tela de Execuções filtrava só por telefone, que **colide entre canais** (o mesmo
número pode existir em WhatsApp, Telegram, etc.). Este passo adiciona a `executions`:

- ``conversation_id`` — coluna SOLTA (sem FK): execução é log histórico e não deve
  travar/cascatear ao apagar ou arquivar a conversa. É o filtro principal (D1).
- ``channel_id`` — slug do canal (``gowa``/``telegram_…``/``default``).
- ``channel_label`` — desnormalizado (``display_name``/``provider``/``"Sandbox"``),
  para a listagem exibir o provider sem join.

Todas nullable; execuções antigas ficam NULL (aceitável, D1 — sem backfill).
Índice ``idx_exec_conversation`` acelera o filtro por conversa. Guardado e
idempotente (espelha 0038): safe re-run.

Revision ID: 0042_exec_conversation_channel
Revises: 0041_seed_audit_manage
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0042_exec_conversation_channel"
down_revision: Union[str, Sequence[str], None] = "0041_seed_audit_manage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "idx_exec_conversation"


def _columns(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("executions"):
        return set()
    return {c["name"] for c in insp.get_columns("executions")}


def _index_names(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("executions"):
        return set()
    return {ix["name"] for ix in insp.get_indexes("executions")}


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("executions"):
        return
    cols = _columns(conn)
    if "conversation_id" not in cols:
        op.add_column("executions", sa.Column("conversation_id", sa.Integer(), nullable=True))
    if "channel_id" not in cols:
        op.add_column("executions", sa.Column("channel_id", sa.Text(), nullable=True))
    if "channel_label" not in cols:
        op.add_column("executions", sa.Column("channel_label", sa.Text(), nullable=True))
    if _INDEX not in _index_names(conn):
        op.create_index(_INDEX, "executions", ["conversation_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("executions"):
        return
    if _INDEX in _index_names(conn):
        op.drop_index(_INDEX, table_name="executions")
    cols = _columns(conn)
    if "channel_label" in cols:
        op.drop_column("executions", "channel_label")
    if "channel_id" in cols:
        op.drop_column("executions", "channel_id")
    if "conversation_id" in cols:
        op.drop_column("executions", "conversation_id")
