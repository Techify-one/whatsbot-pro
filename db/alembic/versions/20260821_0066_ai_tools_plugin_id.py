"""Add ``plugin_id`` to ai_tools (dono da tool, quando ela vem de um plugin).

``kind='plugin'`` responde "não passe pelo installer isolado"; ``plugin_id``
responde "de quem é". Os dois são necessários e não dá para derivar o segundo de
``tool_overrides.plugin_id``: aquela row é justamente a que some quando o plugin
é desabilitado ou removido — o caso em que mais se precisa da resposta (sem a
coluna, a row de um plugin desativado fica indistinguível de uma tool
code-in-DB do operador: o chip some da tela e o DELETE cai no ramo errado).

As rows são semeadas no boot a partir da fonte em disco do plugin
(``agent.ai_plugin_tools``), não aqui — esta migração só cria a coluna.

Revision ID: 0066_ai_tools_plugin_id
Revises: 0065_outbound_webhooks
Create Date: 2026-08-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0066_ai_tools_plugin_id"
down_revision: Union[str, Sequence[str], None] = "0065_outbound_webhooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "ai_tools", "plugin_id"):
        op.add_column("ai_tools", sa.Column("plugin_id", sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "ai_tools", "plugin_id"):
        op.drop_column("ai_tools", "plugin_id")
