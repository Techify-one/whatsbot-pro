"""Times: restringir visibilidade de conversa por time (plano 154).

Aditivo. ``teams.restrict_visibility`` — opt-in, default 0 (D2): quando ligado,
esconde da LISTAGEM (sidebar/filtro/contagem) as conversas daquele time para
quem está na mesma caixa mas não é membro do time. Não afeta acesso direto por
ID/link (D3) — só as 3 funções de listagem em ``conversation_repo.py`` leem a
coluna.

Revision ID: 0068_team_restrict_visibility
Revises: 0067_teams
Create Date: 2026-09-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0068_team_restrict_visibility"
down_revision: Union[str, Sequence[str], None] = "0067_teams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("teams", sa.Column(
        "restrict_visibility", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("teams", "restrict_visibility")
