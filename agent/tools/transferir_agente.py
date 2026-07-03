"""Tool: transferir_agente — handoff entre agentes de IA (plano 06).

Handoff PERSISTENTE (estilo "assign" do Chatwoot, mas para agentes de IA): grava
``conversation.active_agent_key`` no destino. As PRÓXIMAS mensagens da conversa
passam a ser atendidas pelo agente de destino — :func:`agent_factory.build_for_contact`
resolve a precedência conversa→inbox→default a cada requisição.

Valida que o destino existe e está ativo; se o agente atual for um roteador
(``is_router``) com ``routing_targets``, exige que o destino esteja na allowlist.
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


def router_destinations(router: dict) -> list[dict]:
    """Destinos que ESTE roteador pode receber em ``transferir_agente``.

    Fonte ÚNICA da allowlist (plano 30 F5×F6): a MESMA regra que ``execute``
    aplica — agentes enabled, exceto o próprio, restritos a ``routing_targets``
    quando a lista está preenchida. O prompt do roteador (``agent_factory``)
    lista exatamente este conjunto, então o LLM nunca é induzido a um destino
    que ``execute`` barraria.
    """
    targets = router.get("routing_targets") or []
    agents = [a for a in agent_repo.list_all()
              if a.get("enabled") and a.get("agent_key") != router.get("agent_key")]
    if targets:
        agents = [a for a in agents if a["agent_key"] in targets]
    return agents


def execute(ctx, args: dict) -> str | None:
    """Persist the handoff on the open conversation. Returns feedback for the LLM."""
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

        # Validação pelo papel do agente ATUAL (plano 30 F5 — hub-and-spoke):
        # roteador respeita a própria allowlist; spoke SÓ devolve pro roteador.
        current_key = conv.get("active_agent_key")
        if current_key:
            current = agent_repo.get(current_key)
            if current and current.get("is_router"):
                targets = current.get("routing_targets")
                if targets and target not in targets:
                    return (f"Erro: '{target}' não está entre os destinos permitidos "
                            f"deste roteador: {', '.join(targets)}.")
            elif current:
                # Spoke (D4): o único destino válido é o roteador. Sem roteador
                # configurado — ou com o roteador DESABILITADO, que nem pode
                # receber a conversa (o check de enabled acima rejeitaria) —
                # não bloqueia (P4): instalação sem hub-and-spoke ativo mantém
                # o comportamento legado em vez de virar deadlock.
                router = agent_repo.get_router()
                if router and router.get("enabled") and target != router["agent_key"]:
                    return (
                        "Erro: agentes especializados só transferem a conversa "
                        "de volta ao roteador. Devolva usando transferir_agente "
                        f"com agente='{router['agent_key']}' e informe o motivo."
                    )

        conversation_repo.set_agent(conv["id"], target)
        logger.info("Handoff: conversa %s -> agente '%s' (motivo=%s)",
                    conv["id"], target, args.get("motivo") or "-")
        # plano 23 Fase C1: handoff between AI agents is a TYPED domain event
        # (``conversation.agent_changed``), emitted via ``emit_domain``.
        # ``current_key`` is the agent that was answering before this hop (None if
        # the conversation had no bound agent). Best-effort — same name + keys.
        try:
            from domain.events import emit_domain_sync, ConversationAgentChanged
            emit_domain_sync(ConversationAgentChanged(
                conversation_id=conv["id"],
                from_agent=current_key,
                to_agent=target,
                reason=args.get("motivo"),
            ))
        except Exception:
            logger.debug("conversation.agent_changed emit falhou para conversa %s",
                         conv["id"])
    except Exception as e:
        logger.warning("transferir_agente failed for %s: %s",
                       getattr(ctx.contact, "phone", "?"), e)
        return "Erro ao transferir o atendimento."

    label = target_agent.get("display_name") or target
    return (f"Transferência registrada: as próximas mensagens desta conversa serão "
            f"atendidas por '{label}'.")
