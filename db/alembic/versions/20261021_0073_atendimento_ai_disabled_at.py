"""atendimentos.ai_disabled_at — quando a IA foi desligada NESTA conversa (plano 178).

A religação automática da IA conta o tempo de espera a partir de quando a IA parou,
não da última mensagem: uma conversa pausada à mão há 1 minuto cuja última mensagem
é de 3 dias atrás não pode ser religada na hora.

``NULL`` = desconhecido (toda conversa anterior a esta migration, e toda conversa com
a IA ligada). Sem backfill de propósito: para o legado a regra cai na última mensagem.

Revision ID: 0073_atendimento_ai_disabled_at
Revises: 0072_team_rr_cursor
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0073_atendimento_ai_disabled_at"
down_revision: Union[str, Sequence[str], None] = "0072_team_rr_cursor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("atendimentos", sa.Column("ai_disabled_at", sa.Float(), nullable=True))

def downgrade() -> None:
    op.drop_column("atendimentos", "ai_disabled_at")
