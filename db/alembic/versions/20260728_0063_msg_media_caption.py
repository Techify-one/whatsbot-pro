"""Legenda da mídia (``messages.media_caption``) — plano 87.

``messages.content`` é um campo COMPOSTO: depois da descrição de imagem /
transcrição de áudio / extração de documento por IA, ele é reescrito para
``"[Descrição da imagem]: <desc>\\n<legenda do cliente>"`` (imagem) ou
``"<legenda>\\n[Conteúdo do documento]: <extração>"`` (documento) — ver
``server/transcription.py::format_media_content``. O painel tentava separar os
dois públicos adivinhando por prefixo de string e errava nos dois sentidos:
escondia a legenda da imagem junto com a descrição, e desenhava a extração do
documento como se fosse texto do cliente.

Esta coluna guarda a legenda **verbatim**, gravada no INSERT, antes de qualquer
reescrita. ``content`` NÃO muda de formato (o gate de re-inline de imagem em
``agent/memory.py`` depende dele). NULL = mídia sem legenda ou linha legada.

Aditiva e nullable: nenhuma linha existente é tocada.

Revision ID: 0063_msg_media_caption
Revises: 0062_settings_tab_perms
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0063_msg_media_caption"
down_revision: Union[str, Sequence[str], None] = "0062_settings_tab_perms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("media_caption", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "media_caption")
