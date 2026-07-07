"""Filters do plugin Protocolos.

``filter.conversation.before_status``: recusa fechar a atendimento (HTTP 403) se os
campos OBRIGATÓRIOS de resolução (escopo ``atendimento``) não estiverem gravados no
vínculo. Pareia com o popup do frontend, que grava os campos ANTES de chamar
``/status`` — então o caminho normal passa e uma chamada direta à API é barrada.
Desligável via setting ``enforce_backend``. Reabrir nunca é bloqueado.

``filter.conversation.before_reopen``: impede a REABERTURA automática de uma conversa
fechada quando uma mensagem ENVIADA (operador) casa a regra "ignorar abertura" (direção
sent/both). O core aplica no envio do operador com ``value=True``; ``False`` mantém fechada.

``filter.llm.messages``: quando a última mensagem RECEBIDA do contato casa a regra
"ignorar abertura" (direção received/both), aborta a resposta da IA (retorna None). A
mensagem continua salva/visível; só a resposta automática — que reabriria a conversa e
abriria protocolo — é impedida. O protocolo em si é pulado em ``logic.on_inbound``.

``filter.message.notify``: quando a mensagem RECEBIDA casa a regra "ignorar abertura"
(received/both), devolve ``False`` para que ela NÃO gere badge de não-lida, som nem alerta
de nova mensagem (a mensagem continua salva e visível — só o aviso é suprimido).
"""

from . import logic

FILTERS = {
    "filter.conversation.before_status": logic.before_status,
    "filter.conversation.before_reopen": logic.before_reopen,
    "filter.llm.messages": logic.suppress_ai_on_ignored,
    "filter.message.notify": logic.notify_on_ignored,
}
