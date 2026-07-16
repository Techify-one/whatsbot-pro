"""Desvincula atendimentos presos no agente 'default' (fix agente-padrão).

Contexto: fechar um atendimento limpa o ``active_agent_key`` e a reabertura
automática não re-vincula; o runtime então caía SEMPRE no agente de chave
literal ``default`` (piso hardcoded), ignorando o agente marcado como padrão
de novas conversas (``is_default``, plano 36). O fix de código faz o fallback
de runtime honrar o ``is_default`` — mas atendimentos que ficaram
EXPLICITAMENTE vinculados ao ``default`` (pelo carimbo/religa antigos, ou pelo
fallback de erro que devolvia a chave literal) continuariam respondendo por
ele, porque vínculo explícito vence fallback.

Esta migration (data-only) desfaz esses vínculos: ``active_agent_key`` volta a
NULL e o runtime resolve o fallback do momento. Em instalações SEM agente
``is_default`` marcado o comportamento é idêntico ao anterior (NULL resolve
para o próprio ``default``); com marcação, as conversas passam a ser atendidas
pelo agente que o operador escolheu — que é o fix.

Revision ID: 0055_unbind_default_agent
Revises: 0054_ai_variables_versioning
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0055_unbind_default_agent"
down_revision: Union[str, Sequence[str], None] = "0054_ai_variables_versioning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE atendimentos SET active_agent_key = NULL "
        "WHERE active_agent_key = 'default'"
    )


def downgrade() -> None:
    # Data-only e não reversível: não há como saber quais rows tinham o vínculo.
    pass
