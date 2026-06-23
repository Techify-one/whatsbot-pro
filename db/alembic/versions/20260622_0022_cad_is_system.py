"""Atributos PADRÃO do contato — flag is_system em custom_attribute_definitions

Aditivo (plano 19). Adiciona a coluna ``is_system`` (Integer, NOT NULL, default 0)
a ``custom_attribute_definitions``. Atributos de sistema (ex.: CPF) compartilham a
MESMA estrutura dos atributos personalizados (plano 05), mas são semeados no boot
e protegidos contra delete/rename na UI/CRUD. As linhas existentes recebem 0.

Revision ID: 0022_cad_is_system
Revises: 0021_inbox_members
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_cad_is_system"
down_revision: Union[str, Sequence[str], None] = "0021_inbox_members"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "custom_attribute_definitions",
        sa.Column("is_system", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("custom_attribute_definitions", "is_system")
