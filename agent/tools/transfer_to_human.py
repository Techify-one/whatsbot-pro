"""Tool: transfer_to_human — transfers the conversation to a human agent."""

TRANSFER_TO_HUMAN_TOOL = {
    "type": "function",
    "function": {
        "name": "transfer_to_human",
        "description": (
            "Transfere o atendimento para um atendente humano. "
            "Use esta função quando: "
            "1) O cliente pedir explicitamente para falar com um humano, atendente ou pessoa real. "
            "2) O cliente fizer uma pergunta específica que você não sabe responder com certeza "
            "(ex: preços, prazos, disponibilidade, detalhes técnicos do negócio). "
            "NÃO transfira quando o cliente está apenas se apresentando, fornecendo dados pessoais "
            "(nome, email, profissão, endereço), cumprimentando ou fazendo conversa casual. "
            "Nesses casos, use save_contact_info se houver dados pessoais e responda normalmente."
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
