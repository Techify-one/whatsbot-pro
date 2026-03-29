"""Tool: save_contact_info — saves personal data mentioned by the contact."""

SAVE_CONTACT_INFO_TOOL = {
    "type": "function",
    "function": {
        "name": "save_contact_info",
        "description": (
            "Salva informações pessoais do contato (nome, email, profissão, empresa, "
            "endereço ou observação relevante). "
            "Chame APENAS quando a ÚLTIMA mensagem do usuário contiver dados pessoais "
            "NOVOS que ainda NÃO estão listados na seção 'Informações já conhecidas' "
            "do system prompt. NÃO chame se os dados já foram salvos anteriormente."
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
