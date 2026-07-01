"""Event handlers do plugin Protocolos.

Cada atendimento do protocolo é um CICLO (aberto→resolvido):
- ``message.saved`` (cliente engajou) abre/continua um ciclo — se o anterior já foi
  resolvido, abre um NOVO (cliente voltou) → as linhas acumulam.
- ``message.sent`` (operador/IA) só faz bootstrap: cria um ciclo se não houver
  nenhum, mas nunca abre um logo após uma resolução (evita ciclo fantasma).
- ``app.startup`` → backfill one-time do blob `fields` legado para a tabela
  normalizada de campos extras (idempotente; as migrations já rodaram nesse ponto).
"""

from . import logic

EVENT_HANDLERS = {
    "message.saved": logic.on_inbound,
    "message.sent": logic.on_outbound,
    "app.startup": logic.on_startup,
}
