"""Event handlers do plugin Protocolos.

Cada atendimento do protocolo é um CICLO (aberto→resolvido):
- ``message.saved`` (cliente engajou) abre/continua um ciclo — se o anterior já foi
  resolvido, abre um NOVO (cliente voltou) → as linhas acumulam.
- ``message.sent`` (operador/IA) só faz bootstrap: cria um ciclo se não houver
  nenhum, mas nunca abre um logo após uma resolução (evita ciclo fantasma). Também
  encerra a posse temporária quando quem respondeu foi um ATENDENTE.
- ``conversation.deleted`` (o core deletou uma conversa) fecha o ciclo órfão e
  finaliza o protocolo se ele ficou sem ciclo aberto — senão o protocolo ficaria
  pendurado em ``aberto`` no Kanban apontando para uma conversa que não existe mais.
  Apaga também o hold de posse temporária da conversa.
- ``app.startup`` → backfill one-time do blob `fields` legado para a tabela
  normalizada de campos extras (idempotente; as migrations já rodaram nesse ponto).

Posse temporária ("o atendente fica com a conversa N minutos, depois a IA reassume"):
- ``conversation.status_changed`` ARMA a janela ao fechar e a CANCELA numa reabertura
  MANUAL (a automática, por mensagem do cliente, não emite este evento — de propósito:
  o prazo segue correndo com o atendente segurando a conversa reaberta).
- ``conversation.ai_toggled`` / ``conversation.assigned`` CANCELAM quando alguém
  devolve a conversa à IA na mão (ou atualizam o dono numa transferência entre humanos).
"""

from . import logic

EVENT_HANDLERS = {
    "message.saved": logic.on_inbound,
    "message.sent": logic.on_outbound,
    "conversation.deleted": logic.on_conversation_deleted,
    "conversation.status_changed": logic.on_conversation_status,
    "conversation.ai_toggled": logic.on_conversation_ai_toggled,
    "conversation.assigned": logic.on_conversation_assigned,
    "app.startup": logic.on_startup,
}
