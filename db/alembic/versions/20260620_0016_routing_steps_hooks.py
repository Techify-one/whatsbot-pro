"""Schema de routing within-turn + hooks declarativos (plano 06)

Aditivo. `executions.routing_steps` (JSON dos saltos de handoff) e
`execution_steps.agent_key` (qual agente executou cada passo) alimentam o motor
de roteamento within-turn (`ai_engine/routing.py`). `ai_agents.hooks_config`
(JSON, default {}) guarda hooks declarativos (call_limit/requires_prior_call)
mapeados para `filter.tool.args`. Tudo consumido apenas com `ai_engine_enabled`.

Revision ID: 0016_routing_steps_hooks
Revises: 0015_ai_agent_binding
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_routing_steps_hooks"
down_revision: Union[str, Sequence[str], None] = "0015_ai_agent_binding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("routing_steps", sa.Text(), nullable=True))
    op.add_column("execution_steps", sa.Column("agent_key", sa.Text(), nullable=True))
    op.add_column("ai_agents", sa.Column("hooks_config", sa.Text(), nullable=False,
                                         server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("ai_agents") as batch:
        batch.drop_column("hooks_config")
    with op.batch_alter_table("execution_steps") as batch:
        batch.drop_column("agent_key")
    with op.batch_alter_table("executions") as batch:
        batch.drop_column("routing_steps")
