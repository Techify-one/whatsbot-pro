"""Times: assignee de fora do time continua vendo a conversa (plano 155).

Aditivo. ``teams.visible_to_assignee`` — opt-in, default 0, ANINHADO em
``restrict_visibility``: sem ele ligado, o time nem restringe nada, então a
exceção é irrelevante (a conversa já é visível a todo mundo da caixa). Com os
dois ligados, quem está na mesma caixa mas fora do time PASSA A VER na
listagem uma conversa deste time, SE E SÓ SE ela estiver atribuída a ele
(``atendimentos.assignee_user_id``) — qualquer outro atendente de fora do
time, não atribuído, continua sem ver. Abrir por ID/link continua SEMPRE
liberado (D3 do plano 154, sem exceção nenhuma) — este flag só afeta a
listagem, mesma superfície de ``restrict_visibility``.

Revision ID: 0069_team_visible_to_assignee
Revises: 0068_team_restrict_visibility
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0069_team_visible_to_assignee"
down_revision: Union[str, Sequence[str], None] = "0068_team_restrict_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("teams", sa.Column(
        "visible_to_assignee", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("teams", "visible_to_assignee")
