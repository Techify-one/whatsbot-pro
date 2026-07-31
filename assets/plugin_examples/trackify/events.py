"""Assinaturas do barramento → fila de saída.

Assinamos por STRING, sem importar nenhum outro plugin: se o ``protocolos``
estiver ausente ou desativado, estes verbos simplesmente nunca chegam e o resto
segue funcionando.

``protocolos.*`` não está em ``KNOWN_EVENTS`` (o catálogo é o vocabulário do
core), então o carregador loga um WARNING informativo por nome. É documentado
como informativo-e-nunca-bloqueante — aceito de propósito. NÃO usar ``"*"`` só
para fugir do warning: isso entregaria todo ``message.received`` (com base64 de
mídia no ``raw``) a cada mensagem, para uns poucos eventos por dia.
"""

from __future__ import annotations

from . import mirror

EVENT_HANDLERS = {
    # Conversa (o ciclo do atendimento)
    "conversation.created": mirror.on_conversation_created,
    "conversation.status_changed": mirror.on_conversation_status,
    "conversation.reopened": mirror.on_conversation_reopened,
    # Protocolo (do plugin protocolos, via o patch do plano 94)
    "protocolos.opened": mirror.on_protocolo_opened,
    "protocolos.closed": mirror.on_protocolo_closed,
    "protocolos.rated": mirror.on_protocolo_rated,
    # Rótulos gravados no protocolo FORA do fechamento (o fechamento manda o
    # snapshot completo; este manda o incremento, para o CDP não esperar dias
    # por um protocolo que fica aberto).
    "protocolos.fields_updated": mirror.on_protocolo_fields,
    # Contato
    "contact.updated": mirror.on_contact_updated,
    "contact.tagged": mirror.on_contact_tagged,
    "contact.untagged": mirror.on_contact_untagged,
    # A IA gravando no cadastro. O core NÃO emite ``contact.updated`` nesse
    # caminho — a tool ``save_contact_info`` escreve direto no repo e só a rota
    # do painel emite. Sem este gancho, tudo que a IA descobre sobre o cliente
    # (e-mail, profissão, empresa) nunca chegava ao CDP.
    "tool.after": mirror.on_tool_after,
}
