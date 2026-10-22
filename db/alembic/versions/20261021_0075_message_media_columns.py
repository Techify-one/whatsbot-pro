"""messages.media_state + messages.media_name — tela Arquivos (plano 179).

* ``media_state``: ``NULL`` = normal; ``'removed'`` = o arquivo foi tirado pela tela
  Arquivos (a mensagem fica com ``media_path = NULL`` e a bolha mostra "Arquivo
  removido" em vez de "Mídia indisponível"). Valor só do servidor, sem ``CHECK``.
* ``media_name``: o nome que o REMETENTE deu ao arquivo ("Contrato.pdf"), só quando o
  provider declarou em ``media_extras.file_name``. Sem ele a tela mostra o nome em
  disco (``1792…-uuid.pdf``).

Sem backfill de propósito: linha legada fica ``NULL`` e a tela lê o nome do texto
``[Documento recebido: x.pdf]``.

Revision ID: 0075_message_media_columns
Revises: 0074_ai_reactivation_perm
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0075_message_media_columns"
down_revision: Union[str, Sequence[str], None] = "0074_ai_reactivation_perm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("messages", sa.Column("media_state", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("media_name", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("messages", "media_name")
    op.drop_column("messages", "media_state")
