"""add sent_by_user_id / sent_by_name to messages

Nomenclatura de atendimento: uma mensagem MANUAL (role='assistant' +
status='operator') passa a carregar QUEM a enviou. ``sent_by_name`` é um snapshot
do nome do operador logado no momento do envio (mesmo padrão de
``audit_log.actor_user_id`` + ``actor_label``); ``sent_by_user_id`` é uma FK LÓGICA
para ``users.id`` (sem constraint). Ambas nullable: NULL = remetente desconhecido
(linhas legadas, echo do próprio celular, mensagens da IA/cliente) → o painel cai no
rótulo "Manual".

Aditivo, dialect-agnóstico e idempotente: colunas nullable via ``op.add_column``
(funciona em SQLite E Postgres sem rebuild — não há FK real). Guardado pelo inspector
para ser seguro em re-run.

Revision ID: 0034_message_sent_by
Revises: 0033_core_atendimento
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_message_sent_by"
down_revision: Union[str, Sequence[str], None] = "0033_core_atendimento"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns("messages")}


def upgrade() -> None:
    conn = op.get_bind()
    existing = _cols(conn)
    if "sent_by_user_id" not in existing:
        op.add_column("messages", sa.Column("sent_by_user_id", sa.Integer, nullable=True))
    if "sent_by_name" not in existing:
        op.add_column("messages", sa.Column("sent_by_name", sa.Text, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    existing = _cols(conn)
    if "sent_by_name" in existing:
        op.drop_column("messages", "sent_by_name")
    if "sent_by_user_id" in existing:
        op.drop_column("messages", "sent_by_user_id")
