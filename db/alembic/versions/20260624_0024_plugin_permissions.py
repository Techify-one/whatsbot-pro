"""RBAC para plugins: colunas plugin_id/group_label em permissions.

Plano "RBAC para Plugins" §3.1:
- ``permissions.plugin_id`` (nullable) — NULL para perms do core; ``<id>`` para
  perms declaradas por um plugin no bloco ``rbac:`` do manifest.
- ``permissions.group_label`` (nullable) — rótulo do grupo no PermissionPicker
  (default = ``name`` do plugin).
- índice ``idx_permissions_plugin`` para o catálogo dinâmico e o cascade do delete.

Não-quebrador: linhas do core ficam com ambas as colunas em NULL. As perms de
plugin são upsertadas no load do plugin (``plugins/rbac.py``), não nesta migração.

Revision ID: 0024_plugin_permissions
Revises: 0023_channel_inbox_integrity
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_plugin_permissions"
down_revision: Union[str, Sequence[str], None] = "0023_channel_inbox_integrity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_index(conn, table: str, index: str) -> bool:
    insp = sa.inspect(conn)
    return any(ix["name"] == index for ix in insp.get_indexes(table))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "permissions", "plugin_id"):
        op.add_column("permissions", sa.Column("plugin_id", sa.Text(), nullable=True))
    if not _has_column(conn, "permissions", "group_label"):
        op.add_column("permissions", sa.Column("group_label", sa.Text(), nullable=True))
    if not _has_index(conn, "permissions", "idx_permissions_plugin"):
        op.create_index("idx_permissions_plugin", "permissions", ["plugin_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if _has_index(conn, "permissions", "idx_permissions_plugin"):
        op.drop_index("idx_permissions_plugin", table_name="permissions")
    if _has_column(conn, "permissions", "group_label"):
        op.drop_column("permissions", "group_label")
    if _has_column(conn, "permissions", "plugin_id"):
        op.drop_column("permissions", "plugin_id")
