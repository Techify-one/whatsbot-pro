"""Agente padrão para novas conversas (plano 36).

Semântica radio: no máximo um ``ai_agents.is_default = 1`` no sistema — espelha
fielmente a máquina do ``is_router`` (migration 0035). O enforce app-level vive em
``agent_repo.save`` (salvar um novo padrão rebaixa o anterior); este índice único
parcial é o cinto de segurança no banco (Postgres e SQLite suportam partial index).

Só o CARIMBO de criação de conversa passa a ler ``is_default`` (via
``get_new_conversation_default``); o runtime (``build_for_contact`` /
``get_default_agent``) continua caindo no agente-chave ``"default"``, então nenhuma
conversa em andamento muda.

Passos (idempotentes, guardados, reversíveis):
1. add_column ``is_default`` (se não existir).
2. rebaixa duplicatas pré-existentes (mantém a de ``updated_at`` mais recente).
3. seed condicional: marca o agente ``"default"`` SÓ se ninguém já tiver ``is_default=1``.
4. índice único parcial ``ux_ai_agents_single_default``.

Revision ID: 0043_agent_is_default
Revises: 0042_exec_conversation_channel
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0043_agent_is_default"
down_revision: Union[str, Sequence[str], None] = "0042_exec_conversation_channel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ux_ai_agents_single_default"


def _has_column(conn, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    if not insp.has_table(table):
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def _index_names(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("ai_agents"):
        return set()
    return {ix["name"] for ix in insp.get_indexes("ai_agents")}


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("ai_agents"):
        return

    if not _has_column(conn, "ai_agents", "is_default"):
        op.add_column(
            "ai_agents",
            sa.Column("is_default", sa.Integer(), nullable=False, server_default="0"),
        )

    # Rebaixa duplicatas (mantém o padrão editado mais recentemente).
    rows = conn.execute(sa.text(
        "SELECT agent_key FROM ai_agents WHERE is_default = 1 "
        "ORDER BY updated_at DESC, agent_key ASC"
    )).fetchall()
    if len(rows) > 1:
        keep = rows[0][0]
        conn.execute(
            sa.text("UPDATE ai_agents SET is_default = 0 "
                    "WHERE is_default = 1 AND agent_key != :keep"),
            {"keep": keep},
        )

    # Seed condicional: marca o "default" SÓ se ninguém já for padrão.
    existing = conn.execute(
        sa.text("SELECT COUNT(*) FROM ai_agents WHERE is_default = 1")
    ).scalar()
    if not existing:
        conn.execute(sa.text(
            "UPDATE ai_agents SET is_default = 1 WHERE agent_key = 'default'"
        ))

    if _INDEX not in _index_names(conn):
        op.create_index(
            _INDEX, "ai_agents", ["is_default"], unique=True,
            sqlite_where=sa.text("is_default = 1"),
            postgresql_where=sa.text("is_default = 1"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _INDEX in _index_names(conn):
        op.drop_index(_INDEX, table_name="ai_agents")
    if _has_column(conn, "ai_agents", "is_default"):
        op.drop_column("ai_agents", "is_default")
