"""Tool: set_custom_attribute — lets the AI fill a defined custom attribute (plano 05).

The AI may write any defined attribute in the MVP (P53), in either scope: the
contact (default) or the contact's currently-open conversation (plano 54). The
value is validated against the attribute definition; unknown keys / invalid
values are rejected and the error is returned to the LLM.
"""

import logging

from db.repositories import conversation_repo
from db.repositories import custom_attribute_repo as ca_repo
from db.repositories.custom_attribute_validate import validate_value
from db.tables import contacts as contacts_table
from db.tables import conversations as conversations_table

logger = logging.getLogger(__name__)


SET_CUSTOM_ATTRIBUTE_TOOL = {
    "type": "function",
    "display_label": "Preencher Atributo Personalizado",
    "function": {
        "name": "set_custom_attribute",
        "description": (
            "Preenche um atributo personalizado definido pelo operador (ex.: plano, "
            "cpf, cidade). Chame APENAS com uma 'key' que apareça em uma das seções "
            "'Atributos personalizados ... que você pode preencher' do system prompt. "
            "Use scope='conversation' para um atributo DESTA CONVERSA e scope='contact' "
            "(padrão) para um atributo do contato. NÃO invente chaves novas — se a "
            "informação não casar com nenhum atributo definido, use save_contact_info "
            "ou registre como observação."
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
                "scope": {
                    "type": "string",
                    "enum": ["contact", "conversation"],
                    "description": (
                        "Escopo do atributo: 'contact' (padrão) ou 'conversation' "
                        "para um atributo da conversa atual."
                    ),
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
    scope = (args.get("scope") or "contact").strip().lower()
    if scope not in ("contact", "conversation"):
        scope = "contact"
    if not key:
        return "Erro: 'key' é obrigatória."
    try:
        defs = ca_repo.get_definitions_map(scope)
        definition = defs.get(key)
        if definition is None:
            # Forgiving: the AI may have passed the wrong scope. If the key only
            # exists in the other scope, switch to it.
            other = "conversation" if scope == "contact" else "contact"
            other_defs = ca_repo.get_definitions_map(other)
            if key in other_defs:
                scope, defs, definition = other, other_defs, other_defs[key]
        if definition is None:
            disponiveis = ", ".join(defs.keys()) or "(nenhum atributo definido)"
            return f"Erro: o atributo '{key}' não existe. Atributos disponíveis: {disponiveis}."
        norm, err = validate_value(definition, value)
        if err:
            return f"Erro: {err}"
        # Record the EFFECTIVE scope back into args (it may have been switched by
        # the forgiving block above). The same dict is stored in the executed
        # record, so the tool_call card and the webhook's live-refresh
        # (_broadcast_tool_calls → attr_scopes) both reflect WHERE it really saved,
        # not the scope the LLM originally requested (plano 19).
        args["scope"] = scope
        if scope == "conversation":
            conv = conversation_repo.get_open_for_contact(ctx.contact.id)
            if not conv:
                return "Erro: não há conversa aberta para gravar este atributo."
            ca_repo.set_values(conversations_table, conv["id"], {key: norm})
        else:
            ca_repo.set_values(contacts_table, ctx.contact.id, {key: norm})
    except Exception as e:
        logger.warning("set_custom_attribute failed for %s: %s", getattr(ctx.contact, "phone", "?"), e)
        return "Erro ao salvar o atributo."
    # Return a structured success message (plano 19): confirms the value WAS saved
    # and WHERE (contact vs conversation) — the painel card reflects this instead of
    # a generic "feito", and it tells the LLM the forgiving scope-switch (if any).
    label = definition.get("display_name") or key
    where = "na conversa" if scope == "conversation" else "no contato"
    return f"✅ {label} salvo {where}."
