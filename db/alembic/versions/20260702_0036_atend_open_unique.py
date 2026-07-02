"""Única conversa ABERTA por (contato, inbox) — índice parcial (plano 28, dívida).

O plano 28 descreveu o índice único parcial ``uq_atend_open_contact_inbox`` em
``atendimentos(contact_id, inbox_id) WHERE status='open'`` como o fecho do
double-create de resolvers concorrentes, mas a migration nunca o criou — ele só
existia no banco dev (aplicado manualmente na época). O guard app-level de
``_create_open_atomic`` (re-check + insert numa transação) dependia do lock de
escritor único do SQLite; em Postgres READ COMMITTED duas transações passam pelo
re-check juntas, então o índice é o cinto de segurança real (o loser recebe
``IntegrityError`` e adota a thread do winner — catch em ``conversation_repo``).

Antes de criar o índice, fecha duplicatas abertas pré-existentes mantendo a de
atividade mais recente (senão a criação falharia), espelhando a forma de coluna
de ``conversation_repo.set_status("closed")``.

Guardada + idempotente + reversível.

Revision ID: 0036_atend_open_unique
Revises: 0035_single_router
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0036_atend_open_unique"
down_revision: Union[str, Sequence[str], None] = "0035_single_router"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_atend_open_contact_inbox"


def _index_names(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("atendimentos"):
        return set()
    return {ix["name"] for ix in insp.get_indexes("atendimentos")}


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("atendimentos"):
        return
    # Fecha duplicatas abertas (mantém a mais recente por atividade; empate por id).
    conn.execute(sa.text(
        "UPDATE atendimentos a SET status = 'closed', "
        "    resolved_at = EXTRACT(EPOCH FROM now()), "
        "    assignee_user_id = NULL, active_agent_key = NULL, "
        "    updated_at = EXTRACT(EPOCH FROM now()) "
        "WHERE a.status = 'open' AND EXISTS ("
        "    SELECT 1 FROM atendimentos b "
        "    WHERE b.status = 'open' AND b.contact_id = a.contact_id "
        "      AND b.inbox_id = a.inbox_id "
        "      AND (b.last_activity_at > a.last_activity_at "
        "           OR (b.last_activity_at = a.last_activity_at AND b.id > a.id)))"
    ))
    if _INDEX not in _index_names(conn):
        op.create_index(
            _INDEX, "atendimentos", ["contact_id", "inbox_id"], unique=True,
            postgresql_where=sa.text("status = 'open'"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _INDEX in _index_names(conn):
        op.drop_index(_INDEX, table_name="atendimentos")
