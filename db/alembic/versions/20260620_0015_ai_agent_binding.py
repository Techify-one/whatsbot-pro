"""Binding agente↔conversa/inbox + campos de roteamento em ai_agents (plano 06)

Aditivo. Liga um agente específico a uma conversa (active_agent_key) ou a uma inbox
inteira (default_agent_key), e dá a ai_agents os campos que o roteamento/handoff vão
consumir (description, is_router, routing_targets). Colunas sem FK (agent_key é texto;
P1 robustez de ordem). O motor de routing que consome isso vem depois — esta migration
só prepara o schema e o binding manual via API.

Revision ID: 0015_ai_agent_binding
Revises: 0014_cattr_filterable
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_ai_agent_binding"
down_revision: Union[str, Sequence[str], None] = "0014_cattr_filterable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("active_agent_key", sa.Text(), nullable=True))
    op.add_column("inboxes", sa.Column("default_agent_key", sa.Text(), nullable=True))
    op.add_column("ai_agents", sa.Column("description", sa.Text(), nullable=False,
                                         server_default=""))
    op.add_column("ai_agents", sa.Column("is_router", sa.Integer(), nullable=False,
                                         server_default="0"))
    op.add_column("ai_agents", sa.Column("routing_targets", sa.Text(), nullable=True))  # JSON array


def downgrade() -> None:
    with op.batch_alter_table("ai_agents") as batch:
        batch.drop_column("routing_targets")
        batch.drop_column("is_router")
        batch.drop_column("description")
    with op.batch_alter_table("inboxes") as batch:
        batch.drop_column("default_agent_key")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("active_agent_key")
