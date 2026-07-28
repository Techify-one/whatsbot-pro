"""Desvincula o agente de conversas nascidas com a IA desligada (fix atribuição).

``_insert_conversation`` carimbava o agente padrão INDEPENDENTEMENTE do
``ai_active`` (só o gate global ``auto_reply`` limpava os dois): uma conversa
nascida com a IA desligada (``default_ai_enabled`` off no canal ou no global)
ficava "atribuída" a um agente de IA que nunca a responderia — fora da fila
"Não atribuídas" do painel (não-atribuída = sem humano E sem agente). O fix de
código passa a carimbar o agente só quando a conversa nasce IA-ativa; esta
migration (data-only) limpa as rows que já estavam no estado inconsistente:
IA desligada + sem humano atribuído + agente vinculado.

Nota: o predicado também alcança conversas pausadas pelo toggle de IA por
CONTATO (cujo mirror mantém o vínculo de propósito). O efeito nelas é apenas a
triagem recomeçar no agente padrão quando a IA for religada — aceitável.

Revision ID: 0056_unbind_ai_off_agent
Revises: 0055_unbind_default_agent
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0056_unbind_ai_off_agent"
down_revision: Union[str, Sequence[str], None] = "0055_unbind_default_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE atendimentos SET active_agent_key = NULL "
        "WHERE ai_active = 0 AND assignee_user_id IS NULL "
        "AND active_agent_key IS NOT NULL"
    )


def downgrade() -> None:
    # Data-only e não reversível: não há como saber quais rows tinham o vínculo.
    pass
