"""Execuções: colunas denormalizadas de busca (plano "Execuções estilo Nexus").

Adiciona a `executions` as colunas que a tela de Execuções redesenhada usa para
buscar/filtrar sem varrer `execution_steps`:

- ``input_text`` — texto da mensagem do cliente que disparou o turno.
- ``output_text`` — resposta final gerada pela IA.
- ``msg_id`` — id da mensagem WhatsApp de origem (busca por "ID da mensagem").
- ``has_ai`` — 0/1: o turno realmente invocou o modelo (tem passo ``llm_*`` ou
  ``agent_key``) — alimenta o filtro "só execuções com IA".

Índices: ``idx_executions_msg_id`` (igualdade em msg_id). O índice trigram/GIN
para o ILIKE é opcional e só é criado se a extensão ``pg_trgm`` já estiver
disponível (senão o ILIKE simples serve na escala atual).

**Backfill** (best-effort) das execuções existentes a partir de ``execution_steps``:
- ``webhook_received`` → ``input_text`` (preview do batch) + ``msg_id``.
- ``response_sent`` → ``output_text``.
- presença de qualquer passo ``llm_*`` → ``has_ai = 1``.

Idempotente/guardado: safe re-run (espelha 0042).

Revision ID: 0046_executions_search_cols
Revises: 0045_mentions
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0046_executions_search_cols"
down_revision: Union[str, Sequence[str], None] = "0045_mentions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MSG_ID_INDEX = "idx_executions_msg_id"
_TRGM_OUT_INDEX = "idx_executions_output_trgm"
_TRGM_IN_INDEX = "idx_executions_input_trgm"


def _columns(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("executions"):
        return set()
    return {c["name"] for c in insp.get_columns("executions")}


def _index_names(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("executions"):
        return set()
    return {ix["name"] for ix in insp.get_indexes("executions")}


def _has_pg_trgm(conn) -> bool:
    try:
        return bool(conn.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        ).scalar())
    except Exception:
        return False


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("executions"):
        return
    cols = _columns(conn)
    if "input_text" not in cols:
        op.add_column("executions", sa.Column("input_text", sa.Text(), nullable=True))
    if "output_text" not in cols:
        op.add_column("executions", sa.Column("output_text", sa.Text(), nullable=True))
    if "msg_id" not in cols:
        op.add_column("executions", sa.Column("msg_id", sa.Text(), nullable=True))
    if "has_ai" not in cols:
        op.add_column("executions", sa.Column(
            "has_ai", sa.Integer(), nullable=False, server_default="0"))

    idx = _index_names(conn)
    if _MSG_ID_INDEX not in idx:
        op.create_index(_MSG_ID_INDEX, "executions", ["msg_id"])
    if _has_pg_trgm(conn):
        if _TRGM_OUT_INDEX not in idx:
            op.execute(sa.text(
                f"CREATE INDEX {_TRGM_OUT_INDEX} ON executions "
                "USING gin (output_text gin_trgm_ops)"))
        if _TRGM_IN_INDEX not in idx:
            op.execute(sa.text(
                f"CREATE INDEX {_TRGM_IN_INDEX} ON executions "
                "USING gin (input_text gin_trgm_ops)"))

    _backfill(conn)


def _backfill(conn) -> None:
    """Popular as novas colunas a partir de execution_steps (best-effort, um pass).

    Roda em SQL puro para não depender do JSON estar num formato específico:
    ``data`` é TEXT JSON; usamos os operadores JSON do Postgres com cast defensivo.
    Falha silenciosa (execuções antigas ficam NULL/0 — aceitável).
    """
    try:
        # has_ai: qualquer passo cujo step_type comece com 'llm_'
        conn.execute(sa.text("""
            UPDATE executions e SET has_ai = 1
            WHERE has_ai = 0 AND EXISTS (
                SELECT 1 FROM execution_steps s
                WHERE s.execution_id = e.id AND s.step_type LIKE 'llm\\_%'
            )
        """))
        # has_ai: execuções que já têm agent_key gravado também invocaram a IA
        conn.execute(sa.text(
            "UPDATE executions SET has_ai = 1 "
            "WHERE has_ai = 0 AND agent_key IS NOT NULL AND agent_key <> ''"))

        # input_text + msg_id do passo webhook_received (o mais recente por execução)
        conn.execute(sa.text("""
            UPDATE executions e SET
                input_text = COALESCE(e.input_text, sub.combined_preview),
                msg_id = COALESCE(e.msg_id, sub.msg_id)
            FROM (
                SELECT DISTINCT ON (s.execution_id)
                    s.execution_id,
                    NULLIF((s.data::jsonb -> 'items' -> 0 ->> 'msg_id'), '') AS msg_id,
                    NULLIF((s.data::jsonb ->> 'combined_preview'), '') AS combined_preview
                FROM execution_steps s
                WHERE s.step_type IN ('webhook_received', 'batch_accumulated')
                ORDER BY s.execution_id, s.ts DESC
            ) sub
            WHERE sub.execution_id = e.id
              AND (e.input_text IS NULL OR e.msg_id IS NULL)
        """))

        # output_text do passo response_sent (reply_preview)
        conn.execute(sa.text("""
            UPDATE executions e SET
                output_text = sub.reply_preview
            FROM (
                SELECT DISTINCT ON (s.execution_id)
                    s.execution_id,
                    NULLIF((s.data::jsonb ->> 'reply_preview'), '') AS reply_preview
                FROM execution_steps s
                WHERE s.step_type = 'response_sent'
                ORDER BY s.execution_id, s.ts DESC
            ) sub
            WHERE sub.execution_id = e.id AND e.output_text IS NULL
        """))
    except Exception:
        # Backfill é conveniência; qualquer formato inesperado de data não deve
        # abortar o upgrade. As colunas ficam NULL/0 e serão populadas nos turnos novos.
        pass


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("executions"):
        return
    idx = _index_names(conn)
    for name in (_TRGM_IN_INDEX, _TRGM_OUT_INDEX, _MSG_ID_INDEX):
        if name in idx:
            op.drop_index(name, table_name="executions")
    cols = _columns(conn)
    for col in ("has_ai", "msg_id", "output_text", "input_text"):
        if col in cols:
            op.drop_column("executions", col)
