"""Tool: transfer_to_human — transfers the conversation to a human agent."""

TRANSFER_TO_HUMAN_TOOL = {
    "type": "function",
    "function": {
        "name": "transfer_to_human",
        "description": (
            "Transfere o atendimento para um atendente humano. "
            "Use esta função quando o cliente pedir explicitamente para falar com um humano, "
            "atendente, ou pessoa específica, ou quando você não souber responder uma pergunta "
            "com segurança suficiente. Informe o motivo da transferência."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Motivo da transferência (ex: 'cliente pediu atendente humano', 'dúvida fora do escopo')",
                },
            },
            "required": ["reason"],
        },
    },
}
