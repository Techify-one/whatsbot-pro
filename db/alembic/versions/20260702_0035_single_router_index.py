"""Único agente roteador (plano 29 · Eixo B).

Hub-and-spoke com UM hub: no máximo um ``ai_agents.is_router = 1`` no sistema.
O enforce app-level vive em ``agent_repo.save`` (semântica radio: salvar um novo
roteador rebaixa o anterior); este índice único parcial é o cinto de segurança
no banco (Postgres e SQLite suportam partial index).

Antes de criar o índice, rebaixa duplicatas pré-existentes mantendo como
roteador o de ``updated_at`` mais recente (senão a criação do índice falharia).

Guardada + idempotente + reversível.

Revision ID: 0035_single_router
Revises: 0034_conversation_origin
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035_single_router"
down_revision: Union[str, Sequence[str], None] = "0034_conversation_origin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ux_ai_agents_single_router"


def _index_names(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("ai_agents"):
        return set()
    return {ix["name"] for ix in insp.get_indexes("ai_agents")}


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("ai_agents"):
        return
    # Rebaixa duplicatas (mantém o roteador editado mais recentemente).
    rows = conn.execute(sa.text(
        "SELECT agent_key FROM ai_agents WHERE is_router = 1 "
        "ORDER BY updated_at DESC, agent_key ASC"
    )).fetchall()
    if len(rows) > 1:
        keep = rows[0][0]
        conn.execute(
            sa.text("UPDATE ai_agents SET is_router = 0 "
                    "WHERE is_router = 1 AND agent_key != :keep"),
            {"keep": keep},
        )
    if _INDEX not in _index_names(conn):
        op.create_index(
            _INDEX, "ai_agents", ["is_router"], unique=True,
            sqlite_where=sa.text("is_router = 1"),
            postgresql_where=sa.text("is_router = 1"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _INDEX in _index_names(conn):
        op.drop_index(_INDEX, table_name="ai_agents")
