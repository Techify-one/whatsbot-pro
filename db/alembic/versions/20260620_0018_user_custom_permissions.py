"""Permissões por usuário (custom) + base p/ papéis editáveis (RBAC)

Aditivo. Duas mudanças:

* Nova tabela ``user_permissions`` — grants de permissão por usuário, só
  consultados quando ``users.custom_permissions`` está ligado. Permite dar a um
  usuário um conjunto de permissões fora de qualquer papel ("custom").
* Nova coluna ``users.custom_permissions`` (0/1) — flag de modo custom. Quando 1,
  as permissões efetivas vêm de ``user_permissions`` (substituindo papéis).

A edição/criação de papéis (``roles``/``role_permissions``) NÃO precisa de schema
novo — as tabelas já existem desde 0012. ``add_column`` em SQLite usa batch
(``env.py`` já roda com ``render_as_batch=is_sqlite``).

Revision ID: 0018_user_custom_permissions
Revises: 0017_audit_log
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_user_custom_permissions"
down_revision: Union[str, Sequence[str], None] = "0017_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_permissions",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_id", sa.Integer(),
                  sa.ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "permission_id"),
    )
    op.create_index("idx_user_permissions_user", "user_permissions", ["user_id"])

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("custom_permissions", sa.Integer(), nullable=False,
                      server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("custom_permissions")

    op.drop_index("idx_user_permissions_user", table_name="user_permissions")
    op.drop_table("user_permissions")
