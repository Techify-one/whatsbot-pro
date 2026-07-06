"""Canais: colunas de identidade de conta + índice único de dedup (plano 32 F3).

Storage genérico do contrato de identidade de conta (plano 32): cada canal ganha
``account_identity`` (o valor canônico da conta, ex.: número BR, ``phone_number_id``,
``bot_id``) + ``account_identity_kind`` (o namespace do valor). O índice único
parcial ``ux_channels_account_identity`` é o cinto de segurança de banco: dois
canais **habilitados e não-arquivados** do mesmo ``provider`` não podem apontar
para a mesma ``account_identity`` (a segunda escrita levanta ``IntegrityError``).

Espelha o padrão de ``0035_single_router`` (guardado + idempotente + reversível).
Todas as rows nascem com ``account_identity`` NULL, então o índice é criado limpo
(nenhuma violação pré-existente). ``kind`` fora do índice: a coluna guarda o
namespace, mas a unicidade é sobre ``(provider, account_identity)`` — dois valores
canônicos iguais no mesmo provider já implicam o mesmo ``kind`` na prática.

Revision ID: 0038_channels_account_identity
Revises: 0037_drop_ai_variables_category
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0038_channels_account_identity"
down_revision: Union[str, Sequence[str], None] = "0037_drop_ai_variables_category"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ux_channels_account_identity"
_PARTIAL = ("enabled = 1 AND archived = 0 "
            "AND account_identity IS NOT NULL AND account_identity <> ''")


def _columns(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("channels"):
        return set()
    return {c["name"] for c in insp.get_columns("channels")}


def _index_names(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("channels"):
        return set()
    return {ix["name"] for ix in insp.get_indexes("channels")}


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("channels"):
        return
    cols = _columns(conn)
    if "account_identity" not in cols:
        op.add_column("channels", sa.Column("account_identity", sa.Text(), nullable=True))
    if "account_identity_kind" not in cols:
        op.add_column("channels", sa.Column("account_identity_kind", sa.Text(), nullable=True))
    if _INDEX not in _index_names(conn):
        op.create_index(
            _INDEX, "channels", ["provider", "account_identity"], unique=True,
            postgresql_where=sa.text(_PARTIAL),
            sqlite_where=sa.text(_PARTIAL),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("channels"):
        return
    if _INDEX in _index_names(conn):
        op.drop_index(_INDEX, table_name="channels")
    cols = _columns(conn)
    if "account_identity_kind" in cols:
        op.drop_column("channels", "account_identity_kind")
    if "account_identity" in cols:
        op.drop_column("channels", "account_identity")
