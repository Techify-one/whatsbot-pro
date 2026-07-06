"""Merge das duas heads do Alembic (merge developer).

O merge das lanes de trabalho na ``developer`` juntou dois ramos que partiam de
``0037_drop_ai_variables_category``:

- ``0037_drop`` → ``0038_channels_account_identity`` → ``0039_ai_agents_jsonb``
  (plano 32 identidade de conta + plano 34 F5 JSON→JSONB);
- ``0037_drop`` → ``0037_granular_ai_perms`` (permissões granulares de IA +
  ``contact.import``).

Isto é um **merge revision** (não re-encadeamento): mantém as duas linhagens
distintas e as converge numa única head. Escolhido de propósito porque um banco
que já aplicou ``0037_granular_ai_perms`` (ver a nota na própria migration) NÃO
teria ``0038``/``0039`` aplicadas — re-apontar o ``down_revision`` faria o Alembic
tratá-las como ancestrais já aplicadas e PULÁ-las (colunas ``account_identity`` e
a conversão JSONB nunca criadas). Com o merge, ambos os ramos rodam em qualquer
instância, seja qual for o stamp atual. As duas linhagens tocam tabelas
disjuntas (permissões vs channels/ai_agents), então a ordem entre elas é
irrelevante. Sem operações próprias — só reconcilia o grafo.

Revision ID: 0040_merge_granular_jsonb
Revises: 0039_ai_agents_jsonb, 0037_granular_ai_perms
Create Date: 2026-07-06
"""
from typing import Sequence, Union


revision: str = "0040_merge_granular_jsonb"
down_revision: Union[str, Sequence[str], None] = (
    "0039_ai_agents_jsonb",
    "0037_granular_ai_perms",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge revision — sem mudança de schema."""
    pass


def downgrade() -> None:
    """Merge revision — sem mudança de schema."""
    pass
