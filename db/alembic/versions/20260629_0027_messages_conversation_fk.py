"""Reconcilia messages.conversation_id ↔ db/tables.py: FK real → conversations.id
   ON DELETE CASCADE (Plano 23 · Fase E3).

Contexto: a migration 0013 adicionou ``messages.conversation_id`` como coluna
SIMPLES, sem FK (``op.add_column`` — SQLite não suporta ADD COLUMN com constraint
de FK). O modelo declarativo em ``db/tables.py`` SEMPRE declarou
``ForeignKey("conversations.id", ondelete="CASCADE")`` nessa coluna, então o schema
REAL divergia da metadata (a fonte de verdade). Esta revisão fecha o gap criando
a FK de verdade nos dois backends.

Comportamento PRESERVADO: ``conversation_repo.delete`` já apaga as mensagens da
conversa EXPLICITAMENTE (justamente porque a CASCADE não disparava em SQLite). Com
a FK no lugar, esse DELETE explícito continua e a CASCADE vira apenas um backstop
idempotente — nenhuma mudança observável (a caracterização P16 segue verde).

Antes de criar a constraint, NULL-amos qualquer ``conversation_id`` órfão (apontando
para uma conversa inexistente) — espelha o passo de "desórfana" da 0023. A coluna é
nullable, então isso é seguro e não muda o caminho normal (toda mensagem ativa tem
uma conversa válida).

Dialect-agnóstico e idempotente: SQLite via ``batch_alter_table`` (recria a tabela,
preservando a FK existente de ``contact_id``); Postgres via ``ADD CONSTRAINT`` (pula
se já existir).

Revision ID: 0027_messages_conversation_fk
Revises: 0026_ai_tools_kind
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027_messages_conversation_fk"
down_revision: Union[str, Sequence[str], None] = "0026_ai_tools_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK_NAME = "fk_messages_conversation_id"


def _existing_fk_names(conn, table: str) -> set[str]:
    return {fk.get("name") for fk in sa.inspect(conn).get_foreign_keys(table)}


def _fk_on_conversation_id(conn, table: str) -> bool:
    """True if any FK on ``table`` already constrains ``conversation_id``.

    SQLite-reflected FKs may have ``name=None``; match by the constrained column
    instead of the name so the migration stays idempotent across backends.
    """
    for fk in sa.inspect(conn).get_foreign_keys(table):
        if fk.get("constrained_columns") == ["conversation_id"]:
            return True
    return False


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    # Idempotência defensiva: se a FK já existe (re-run / DB já reconciliado),
    # não faz nada.
    if _fk_on_conversation_id(conn, "messages"):
        return

    # 1) Desórfana: zera conversation_id que aponta para conversa inexistente
    #    (necessário ANTES da FK, senão a recriação SQLite / o ADD CONSTRAINT
    #    Postgres falham). conversation_id é NULLABLE, então NULL é seguro.
    res = conn.execute(sa.text(
        "UPDATE messages SET conversation_id = NULL "
        "WHERE conversation_id IS NOT NULL "
        "AND conversation_id NOT IN (SELECT id FROM conversations)"
    ))
    if (res.rowcount or 0) > 0:
        print(f"[0027] {res.rowcount} mensagem(ns) órfã(s) tiveram conversation_id zerado.")

    # 2) FK messages.conversation_id → conversations.id ON DELETE CASCADE.
    if dialect == "sqlite":
        with op.batch_alter_table("messages", schema=None) as batch:
            batch.create_foreign_key(
                _FK_NAME, "conversations",
                ["conversation_id"], ["id"], ondelete="CASCADE")
    else:
        if _FK_NAME not in _existing_fk_names(conn, "messages"):
            op.create_foreign_key(
                _FK_NAME, "messages", "conversations",
                ["conversation_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("messages", schema=None) as batch:
            batch.drop_constraint(_FK_NAME, type_="foreignkey")
    else:
        op.drop_constraint(_FK_NAME, "messages", type_="foreignkey")
