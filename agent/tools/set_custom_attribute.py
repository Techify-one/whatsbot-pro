"""Tool: set_custom_attribute — lets the AI fill a defined custom attribute (plano 05).

The AI may write any defined contact attribute in the MVP (P53). The value is
validated against the attribute definition; unknown keys / invalid values are
rejected and the error is returned to the LLM.
"""

import logging

from db.repositories import custom_attribute_repo as ca_repo
from db.repositories.custom_attribute_validate import validate_value
from db.tables import contacts as contacts_table

logger = logging.getLogger(__name__)


SET_CUSTOM_ATTRIBUTE_TOOL = {
    "type": "function",
    "display_label": "Preencher Atributo Personalizado",
    "function": {
        "name": "set_custom_attribute",
        "description": (
            "Preenche um atributo personalizado do contato definido pelo operador "
            "(ex.: plano, cpf, cidade). Chame APENAS com uma 'key' que apareça na "
            "seção 'Atributos personalizados que você pode preencher' do system "
            "prompt. NÃO invente chaves novas — se a informação não casar com nenhum "
            "atributo definido, use save_contact_info ou registre como observação."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "A chave (snake_case) de um atributo definido, listada no prompt.",
                },
                "value": {
                    "type": "string",
                    "description": "O valor a gravar (para checkbox use 'true'/'false'; para list use uma das opções).",
                },
            },
            "required": ["key", "value"],
        },
    },
}


def execute(ctx, args: dict) -> str | None:
    """Persist a custom attribute value. Returns an error string for the LLM, or None."""
    key = (args.get("key") or "").strip()
    value = args.get("value")
    if not key:
        return "Erro: 'key' é obrigatória."
    try:
        defs = ca_repo.get_definitions_map("contact")
        definition = defs.get(key)
        if definition is None:
            disponiveis = ", ".join(defs.keys()) or "(nenhum atributo definido)"
            return f"Erro: o atributo '{key}' não existe. Atributos disponíveis: {disponiveis}."
        norm, err = validate_value(definition, value)
        if err:
            return f"Erro: {err}"
        ca_repo.set_values(contacts_table, ctx.contact.id, {key: norm})
    except Exception as e:
        logger.warning("set_custom_attribute failed for %s: %s", getattr(ctx.contact, "phone", "?"), e)
        return "Erro ao salvar o atributo."
    return None
