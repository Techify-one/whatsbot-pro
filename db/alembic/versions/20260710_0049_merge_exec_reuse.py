"""Merge das duas heads do Alembic (merge developer).

Junta as ramas paralelas que forkaram de ``0045_mentions``:
  - ``0046_executions_search_cols`` (refactor da tela de execuções, origin/developer);
  - ``0046_message_agent_key → 0047_source_id_native → 0048_tool_reuse_result``
    (plano 42 + plano 47).
Revisão de merge (sem schema) — cada rama aplica suas próprias migrations; um DB
em qualquer das pontas alcança a head aplicando as revisões da OUTRA rama + este
merge. Mesmo padrão de ``0040_merge_granular_jsonb`` / ``0044_merge_perms_is_default``.

Revision ID: 0049_merge_exec_reuse
Revises: 0046_executions_search_cols, 0048_tool_reuse_result
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0049_merge_exec_reuse"
down_revision: Union[str, Sequence[str], None] = (
    "0046_executions_search_cols",
    "0048_tool_reuse_result",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
