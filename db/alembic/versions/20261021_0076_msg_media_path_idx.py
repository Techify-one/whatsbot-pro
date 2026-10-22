"""Índice parcial em messages.media_path (plano 179).

A tela Arquivos cruza cada arquivo do disco com as mensagens que apontam para ele
(``media_path IN (...)``), e ``media_path_is_referenced`` faz o mesmo a cada GET em
``/statics/outbox``. Até aqui a coluna não tinha índice nenhum.

``CREATE INDEX CONCURRENTLY`` dentro de ``autocommit_block``: sem isso o boot
seguraria o lock de escrita em ``messages`` durante a construção. ``IF NOT EXISTS``
deixa a migration idempotente — se o índice foi pré-criado à mão em produção (como
foi feito com o GIN da 0060), o boot vira no-op.

Revision ID: 0076_msg_media_path_idx
Revises: 0075_message_media_columns
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0076_msg_media_path_idx"
down_revision: Union[str, Sequence[str], None] = "0075_message_media_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_msg_media_path "
            "ON messages (media_path) WHERE media_path IS NOT NULL")

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_msg_media_path")
