"""Tool: transfer_to_human — transfers the conversation to a human agent."""

import logging
import time

from db.repositories import conversation_repo

logger = logging.getLogger(__name__)


TRANSFER_TO_HUMAN_TOOL = {
    "type": "function",
    "display_label": "Transferir para Humano",
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


_TRANSFER_FEEDBACK = (
    "Transferência realizada. Responda ao cliente de forma curta e natural, "
    "apenas confirmando que já vai ser atendido pela pessoa solicitada. "
    "NÃO mencione 'humano', 'atendente' nem 'transferência'."
)


def execute(ctx, args: dict) -> str | None:
    """Disable AI for the contact and tag the conversation as transferred.

    Returns a feedback string that the handler will inject as the tool reply
    when the model issues only the tool call (no inline text).
    """
    try:
        ctx.contact.set_ai_enabled(False)
        ctx.tag_registry.create("transferido_atendente", "#ef4444")
        ctx.contact.add_tag("transferido_atendente")
        ctx.contact.save()
        # Unassign the conversation so it lands in the "Não atribuídas" inbox:
        # clear both the human assignee and the bound AI agent, and pause the
        # conversation-level AI gate (a human takes over from here).
        try:
            conv = conversation_repo.get_open_for_contact(ctx.contact.id)
            if conv:
                # plano 23 Fase B5 (§4.2): the round-robin DESTINATION of a
                # human handoff is a plugin seam. Default = unassigned
                # ("Não atribuídas" inbox). A plugin returning a dict with
                # ``assignee_user_id`` rewrites the destination; ``None`` keeps
                # the default. No-op (value passes through) when unregistered.
                assignment = {"assignee_user_id": None,
                              "reason": args.get("reason")}
                try:
                    from plugins.events import apply_filter_sync
                    rewritten = apply_filter_sync(
                        "filter.conversation.assignment", assignment,
                        {"conversation_id": conv["id"],
                         "contact_id": ctx.contact.id,
                         "phone": ctx.contact.phone})
                    if isinstance(rewritten, dict):
                        assignment = rewritten
                except Exception:
                    logger.debug("filter.conversation.assignment falhou para %s",
                                 ctx.contact.phone)
                conversation_repo.assign_agent(
                    conv["id"], assignee_user_id=assignment.get("assignee_user_id"),
                    active_agent_key=None, ai_active=0)
                # plano 23 Fase C0: surface the handoff to a human as a domain event
                # (``conversation.transferred_to_human``) on the plugin bus. Best-effort.
                try:
                    from plugins.events import emit_with_filter_sync
                    emit_with_filter_sync("conversation.transferred_to_human", {
                        "conversation_id": conv["id"],
                        "contact_id": ctx.contact.id,
                        "reason": args.get("reason"),
                        "ts": time.time(),
                    })
                except Exception:
                    logger.debug("conversation.transferred_to_human emit falhou para %s",
                                 ctx.contact.phone)
        except Exception as e:
            logger.warning("transfer_to_human: falha ao desatribuir conversa de %s: %s",
                           ctx.contact.phone, e)
        logger.info("Transfer to human for %s: %s", ctx.contact.phone, args.get("reason", ""))
    except Exception as e:
        logger.warning("transfer_to_human failed for %s: %s", ctx.contact.phone, e)
    return _TRANSFER_FEEDBACK
