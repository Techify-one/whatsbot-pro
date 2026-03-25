"""Tool: save_contact_info — saves personal data mentioned by the contact."""

SAVE_CONTACT_INFO_TOOL = {
    "type": "function",
    "function": {
        "name": "save_contact_info",
        "description": (
            "Salva informações pessoais do contato quando ele mencionar dados como "
            "nome, email, profissão, empresa, endereço ou qualquer observação importante. "
            "Chame esta função SEMPRE que o usuário revelar dados pessoais na conversa."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nome completo do contato",
                },
                "email": {
                    "type": "string",
                    "description": "Email do contato",
                },
                "profession": {
                    "type": "string",
                    "description": "Profissão ou cargo do contato",
                },
                "company": {
                    "type": "string",
                    "description": "Empresa onde trabalha",
                },
                "address": {
                    "type": "string",
                    "description": "Endereço completo do contato (rua, número, bairro, cidade)",
                },
                "observation": {
                    "type": "string",
                    "description": "Qualquer outra informação relevante sobre o contato",
                },
            },
            "required": [],
        },
    },
}
