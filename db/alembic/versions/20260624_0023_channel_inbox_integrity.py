"""Integridade canal↔inbox: archived em channels, limpeza de órfãs, sync de nomes
   e FK inboxes.channel_id → channels.id ON DELETE CASCADE.

Plano "inboxes/canais/permissões":
- §4.3-c: coluna ``channels.archived`` (soft-delete — "excluir" canal o arquiva).
- §6.1: sincroniza ``inboxes.name`` com o ``channels.display_name`` atual e
  desórfana (channel_id → NULL) as inboxes cujo canal não existe mais, preservando
  o histórico (conversas seguem no banco; somem da UI pelo JOIN de list_with_channel).
- §4.5: adiciona a FK ``inboxes.channel_id → channels.id ON DELETE CASCADE`` para
  garantir o invariante 1:1 e o cascade num purge. Em SQLite via batch_alter_table.

Idempotente e dialect-agnóstico (rodado em SQLite e Postgres).

Revision ID: 0023_channel_inbox_integrity
Revises: 0022_cad_is_system
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_channel_inbox_integrity"
down_revision: Union[str, Sequence[str], None] = "0022_cad_is_system"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    # 1) channels.archived (soft-delete). Idempotente.
    if not _has_column(conn, "channels", "archived"):
        op.add_column(
            "channels",
            sa.Column("archived", sa.Integer(), nullable=False, server_default="0"),
        )

    # 2) Sincroniza o nome da inbox com o display_name atual do canal (corrige
    #    nomes velhos). Subquery correlacionada — funciona em SQLite e Postgres.
    conn.execute(sa.text(
        "UPDATE inboxes SET name = ("
        "  SELECT display_name FROM channels WHERE channels.id = inboxes.channel_id"
        ") WHERE channel_id IN (SELECT id FROM channels)"
    ))

    # 3) Desórfana inboxes cujo canal não existe mais (channel_id → NULL),
    #    preservando conversas/contatos. Necessário ANTES da FK (senão ela falha).
    res = conn.execute(sa.text(
        "UPDATE inboxes SET channel_id = NULL "
        "WHERE channel_id IS NOT NULL "
        "AND channel_id NOT IN (SELECT id FROM channels)"
    ))
    if (res.rowcount or 0) > 0:
        print(f"[0023] {res.rowcount} inbox(es) órfã(s) desvinculada(s) do canal.")

    # 4) FK inboxes.channel_id → channels.id ON DELETE CASCADE.
    if dialect == "sqlite":
        with op.batch_alter_table("inboxes", schema=None) as batch:
            batch.create_foreign_key(
                "fk_inboxes_channel_id", "channels",
                ["channel_id"], ["id"], ondelete="CASCADE")
    else:
        # Postgres: não duplica se já existir (idempotência defensiva).
        existing = {fk["name"] for fk in sa.inspect(conn).get_foreign_keys("inboxes")}
        if "fk_inboxes_channel_id" not in existing:
            op.create_foreign_key(
                "fk_inboxes_channel_id", "inboxes", "channels",
                ["channel_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("inboxes", schema=None) as batch:
            batch.drop_constraint("fk_inboxes_channel_id", type_="foreignkey")
    else:
        op.drop_constraint("fk_inboxes_channel_id", "inboxes", type_="foreignkey")
    op.drop_column("channels", "archived")
