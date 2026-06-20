"""Tool: transferir_agente — handoff entre agentes de IA (plano 06).

Handoff PERSISTENTE (estilo "assign" do Chatwoot, mas para agentes de IA): grava
``conversation.active_agent_key`` no destino. As PRÓXIMAS mensagens da conversa
passam a ser atendidas pelo agente de destino — :func:`agent_factory.build_for_contact`
resolve a precedência conversa→inbox→default a cada requisição.

Self-gated: só age quando ``ai_engine_enabled`` está ligado (multiagente). Em modo
legado devolve um erro claro em vez de fazer uma escrita inócua. Valida que o destino
existe e está ativo; se o agente atual for um roteador (``is_router``) com
``routing_targets``, exige que o destino esteja na allowlist.
"""

import logging

from db.repositories import agent_repo, conversation_repo

logger = logging.getLogger(__name__)


TRANSFERIR_AGENTE_TOOL = {
    "type": "function",
    "display_label": "Transferir para outro agente",
    "function": {
        "name": "transferir_agente",
        "description": (
            "Transfere o atendimento desta conversa para OUTRO agente de IA "
            "especializado (ex.: de triagem para vendas ou suporte). Use quando o "
            "assunto do contato corresponder a outro agente disponível. As próximas "
            "mensagens serão atendidas pelo agente de destino. Informe o 'agente' "
            "pela sua chave (agent_key) exata."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agente": {
                    "type": "string",
                    "description": "A chave (agent_key) do agente de destino.",
                },
                "motivo": {
                    "type": "string",
                    "description": "Motivo curto da transferência (opcional, para registro).",
                },
            },
            "required": ["agente"],
        },
    },
}


def execute(ctx, args: dict) -> str | None:
    """Persist the handoff on the open conversation. Returns feedback for the LLM."""
    if not getattr(ctx.handler, "ai_engine_enabled", False):
        return "Erro: o roteamento entre agentes está desativado (ai_engine_enabled)."

    target = (args.get("agente") or args.get("target") or "").strip()
    if not target:
        return "Erro: informe a chave do agente de destino em 'agente'."

    try:
        target_agent = agent_repo.get(target)
        if not target_agent or not target_agent.get("enabled"):
            disponiveis = [a["agent_key"] for a in agent_repo.list_all()
                           if a.get("enabled")]
            return (f"Erro: o agente '{target}' não existe ou está desativado. "
                    f"Agentes disponíveis: {', '.join(disponiveis) or '(nenhum)'}.")

        conv = conversation_repo.get_open_for_contact(ctx.contact.id)
        if not conv:
            return "Erro: não há conversa aberta para transferir."

        if conv.get("active_agent_key") == target:
            return f"O agente '{target}' já está atendendo esta conversa."

        # Se o agente ATUAL é um roteador com lista de destinos, respeite a allowlist.
        current_key = conv.get("active_agent_key")
        if current_key:
            current = agent_repo.get(current_key)
            targets = (current or {}).get("routing_targets")
            if current and current.get("is_router") and targets and target not in targets:
                return (f"Erro: '{target}' não está entre os destinos permitidos "
                        f"deste roteador: {', '.join(targets)}.")

        conversation_repo.set_agent(conv["id"], target)
        logger.info("Handoff: conversa %s -> agente '%s' (motivo=%s)",
                    conv["id"], target, args.get("motivo") or "-")
    except Exception as e:
        logger.warning("transferir_agente failed for %s: %s",
                       getattr(ctx.contact, "phone", "?"), e)
        return "Erro ao transferir o atendimento."

    label = target_agent.get("display_name") or target
    return (f"Transferência registrada: as próximas mensagens desta conversa serão "
            f"atendidas por '{label}'.")
