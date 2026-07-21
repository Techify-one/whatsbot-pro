"""Event handlers do plugin Protocolos.

Cada atendimento do protocolo é um CICLO (aberto→resolvido):
- ``message.saved`` (cliente engajou) abre/continua um ciclo — se o anterior já foi
  resolvido, abre um NOVO (cliente voltou) → as linhas acumulam.
- ``message.sent`` (operador/IA) só faz bootstrap: cria um ciclo se não houver
  nenhum, mas nunca abre um logo após uma resolução (evita ciclo fantasma).
- ``conversation.deleted`` (o core deletou uma conversa) fecha o ciclo órfão e
  finaliza o protocolo se ele ficou sem ciclo aberto — senão o protocolo ficaria
  pendurado em ``aberto`` no Kanban apontando para uma conversa que não existe mais.
- ``app.startup`` → backfill one-time do blob `fields` legado para a tabela
  normalizada de campos extras (idempotente; as migrations já rodaram nesse ponto).
"""

from . import logic

EVENT_HANDLERS = {
    "message.saved": logic.on_inbound,
    "message.sent": logic.on_outbound,
    "conversation.deleted": logic.on_conversation_deleted,
    "app.startup": logic.on_startup,
}
