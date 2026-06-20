"""custom_attribute_definitions.filterable + índice JSON (plano 05 Fase 6)

Marca quais definições entram no filter-schema do plano 08 e ganham índice. A
coluna é aditiva (nullable=False default 0). Para Postgres, cria um GIN sobre
contacts.custom_attributes (JSONB via _json_type) acelerando filtros por atributo;
no SQLite, índices de expressão por-key são criados sob demanda pelo motor de
filtros (plano 08) — não aqui.

Revision ID: 0014_cattr_filterable
Revises: 0013_inbox_conversations
Create Date: 2026-06-20

NOTE (P82): down_revision é o head real no momento.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_cattr_filterable"
down_revision: Union[str, Sequence[str], None] = "0013_inbox_conversations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("custom_attribute_definitions",
                  sa.Column("filterable", sa.Integer(), nullable=False, server_default="0"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_cattr_gin "
                   "ON contacts USING gin (custom_attributes)")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_contacts_cattr_gin")
    with op.batch_alter_table("custom_attribute_definitions") as batch:
        batch.drop_column("filterable")
