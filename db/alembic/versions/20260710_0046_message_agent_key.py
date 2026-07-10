"""messages.agent_key — atribuição de qual agente de IA produziu a mensagem

Rastreia o agente que respondeu (mensagem 'assistant') ou executou a tool
(card 'tool_call'), para o painel exibir "IA - <NOME>" / "Ferramenta IA - <NOME>".
Aditivo e nullable: linhas legadas ficam sem atribuição (frontend cai em "IA").

Revision ID: 0046_message_agent_key
Revises: 0045_mentions
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0046_message_agent_key"
down_revision: Union[str, Sequence[str], None] = "0045_mentions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("agent_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "agent_key")
