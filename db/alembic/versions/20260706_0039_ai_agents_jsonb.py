"""ai_agents JSON columns TEXT→JSONB (plano 34 F5 — Track B).

Saneamento estrutural: as 4 colunas JSON de ``ai_agents`` (``model_config``,
``tool_names``, ``routing_targets``, ``hooks_config``) deixam de ser TEXT
JSON-encoded e viram **JSONB nativo**. Com isso o SQLAlchemy serializa o dict/list
exatamente uma vez (o repo para de fazer ``json.dumps`` à mão), eliminando na
ORIGEM a classe de dupla-codificação que quebrou a resolução de agente (achado E.A
do QA / plano 34). Os leitores já tratam dict/list nativo (``coerce_json`` vira
no-op na leitura — defesa em profundidade preservada).

USING robusto (D1 diz que os bancos estão limpos hoje, mas defendemos mesmo assim):
- NULL / string vazia  → ``{}`` (colunas NOT NULL) ou NULL (colunas nullable);
- valor eventualmente DUPLO-codificado (um JSON string cujo conteúdo é JSON) →
  desembrulha 1 camada via ``#>> '{}'`` antes do cast (o restante, se houver, o
  ``coerce_json`` de leitura resolve);
- caso normal (JSON válido) → ``::jsonb`` direto.

Postgres-only (plano 29): a conversão só roda no dialeto postgresql; em sqlite o
``_json_type()`` genérico já é TEXT/JSON, então é no-op. Guardada + idempotente +
reversível, espelhando o estilo de ``0038``.

Revision ID: 0039_ai_agents_jsonb
Revises: 0038_channels_account_identity
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0039_ai_agents_jsonb"
down_revision: Union[str, Sequence[str], None] = "0038_channels_account_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NOT NULL, server_default "{}" (objeto) — vazio/NULL vira '{}'::jsonb.
_OBJ_COLS = ("model_config", "hooks_config")
# nullable, sem default (array ou NULL) — vazio/NULL vira SQL NULL.
_ARR_COLS = ("tool_names", "routing_targets")


def _to_jsonb_using(col: str, empty_sql: str) -> str:
    """``USING`` robusto: NULL/vazio→empty_sql; string duplo-codificada→1× unwrap; senão ::jsonb."""
    return (
        "ALTER TABLE ai_agents ALTER COLUMN " + col + " TYPE jsonb USING "
        "CASE "
        "WHEN " + col + " IS NULL OR btrim(" + col + ") = '' THEN " + empty_sql + " "
        "WHEN jsonb_typeof(" + col + "::jsonb) = 'string' "
        "AND (" + col + r"::jsonb #>> '{}') ~ '^\s*[\[{]' "
        "THEN (" + col + "::jsonb #>> '{}')::jsonb "
        "ELSE " + col + "::jsonb "
        "END"
    )


def _to_text_using(col: str) -> str:
    return ("ALTER TABLE ai_agents ALTER COLUMN " + col
            + " TYPE text USING " + col + "::text")


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("ai_agents"):
        return
    if conn.dialect.name != "postgresql":
        return  # _json_type() genérico já é TEXT/JSON no sqlite — nada a converter
    for col in _OBJ_COLS:
        op.execute("ALTER TABLE ai_agents ALTER COLUMN " + col + " DROP DEFAULT")
        op.execute(_to_jsonb_using(col, "'{}'::jsonb"))
        op.execute("ALTER TABLE ai_agents ALTER COLUMN " + col + " SET DEFAULT '{}'::jsonb")
    for col in _ARR_COLS:
        op.execute(_to_jsonb_using(col, "NULL"))


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("ai_agents"):
        return
    if conn.dialect.name != "postgresql":
        return
    for col in _OBJ_COLS:
        op.execute("ALTER TABLE ai_agents ALTER COLUMN " + col + " DROP DEFAULT")
        op.execute(_to_text_using(col))
        op.execute("ALTER TABLE ai_agents ALTER COLUMN " + col + " SET DEFAULT '{}'")
    for col in _ARR_COLS:
        op.execute(_to_text_using(col))
