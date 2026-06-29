"""Tool: save_contact_info — saves personal data mentioned by the contact."""

import logging

logger = logging.getLogger(__name__)


SAVE_CONTACT_INFO_TOOL = {
    "type": "function",
    "display_label": "Salvar Dados do Contato",
    "function": {
        "name": "save_contact_info",
        "description": (
            "Salva o NOME do contato ou uma OBSERVAÇÃO relevante. "
            "Para email, profissão, empresa, endereço (ou qualquer outro atributo "
            "definido), use a tool set_custom_attribute — esses dados agora são "
            "atributos personalizados. "
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
                "observation": {
                    "type": "string",
                    "description": "Qualquer outra informação relevante sobre o contato",
                },
            },
            "required": [],
        },
    },
}


def execute(ctx, args: dict) -> str | None:
    """Persist contact info from an LLM tool call.

    Returns ``None`` so the handler uses its default follow-up message.
    """
    try:
        ctx.contact.update_info(**args)
    except Exception as e:
        logger.warning("save_contact_info failed for %s: %s", ctx.contact.phone, e)
    return None
