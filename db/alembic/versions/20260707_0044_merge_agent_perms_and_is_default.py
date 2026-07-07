"""Merge das duas heads do Alembic (merge developer).

O merge da ``origin/developer`` na lane local juntou dois ramos que partiam de
``0041_seed_audit_manage``:

- ``0041_seed`` → ``0042_exec_conversation_channel`` → ``0043_agent_is_default``
  (plano 36: conversation_id/canal na execução + agente padrão de novas conversas);
- ``0041_seed`` → ``0042_granular_prompt_perms`` → ``0043_agent_create_duplicate``
  (permissões granulares de agente de IA: prompt edit/version/delete + create/duplicate).

Isto é um **merge revision** (não re-encadeamento): mantém as duas linhagens
distintas e as converge numa única head. Escolhido de propósito porque um banco
que já aplicou um dos ramos NÃO teria o outro aplicado — re-apontar o
``down_revision`` faria o Alembic tratar migrations como ancestrais já aplicadas e
PULÁ-las. Com o merge, ambos os ramos rodam em qualquer instância, seja qual for o
stamp atual. As duas linhagens tocam tabelas disjuntas (``executions``/``ai_agents``
vs ``permissions``), então a ordem entre elas é irrelevante. Sem operações
próprias — só reconcilia o grafo.

Revision ID: 0044_merge_perms_is_default
Revises: 0043_agent_is_default, 0043_agent_create_duplicate
Create Date: 2026-07-07

Nota: o ``revision`` cabe em ``alembic_version.version_num`` (``varchar(32)``) —
mantido curto de propósito (o id descritivo completo vai no nome do arquivo).
"""
from typing import Sequence, Union


revision: str = "0044_merge_perms_is_default"
down_revision: Union[str, Sequence[str], None] = (
    "0043_agent_is_default",
    "0043_agent_create_duplicate",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge revision — sem mudança de schema."""
    pass


def downgrade() -> None:
    """Merge revision — sem mudança de schema."""
    pass
