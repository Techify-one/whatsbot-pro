"""Tool: transferir_agente — handoff entre agentes de IA (plano 06).

Handoff PERSISTENTE (estilo "assign" do Chatwoot, mas para agentes de IA): grava
``conversation.active_agent_key`` no destino. As PRÓXIMAS mensagens da conversa
passam a ser atendidas pelo agente de destino — :func:`agent_factory.build_for_contact`
resolve a precedência conversa→inbox→default a cada requisição.

Valida que o destino existe e está ativo; se o agente atual for um roteador
(``is_router``) com ``routing_targets``, exige que o destino esteja na allowlist.

Spoke→spoke é COAGIDO, não recusado: um agente especializado que pede outro
especialista tem o destino reescrito para o roteador, com o pedido original
carimbado no motivo. Recusar custava uma volta no LLM e uma 2ª chamada da tool —
que o teto ``call_limit: 1`` de ``ai_engine.hooks`` barrava, deixando o handoff
sem acontecer e o turno morrendo em silêncio. A intenção do spoke é legítima
(a conversa precisa seguir); só a rota é que não é dele — quem escolhe destino é
o hub. Nada aqui casa nome de agente: o papel vem do flag ``is_router``.
"""

import logging

from db.repositories import agent_repo, conversation_repo

logger = logging.getLogger(__name__)


def _caller_agent_key(conv: dict) -> str | None:
    """O agente que executa ESTE hop — quem de fato está chamando a tool.

    O ContextVar de execução é a fonte precisa: ``active_agent_key`` da conversa
    pode estar NULL (conversa que nunca carimbou agente responde pelo default) e
    aí a validação de papel era pulada inteira. Cai na linha da conversa quando
    o ContextVar não estiver disponível.
    """
    try:
        from agent.execution import get_current_step_agent
        key = get_current_step_agent()
        if key:
            return key
    except Exception:
        pass
    return conv.get("active_agent_key")


def _record_intent(args: dict, caller: dict, pedido: str) -> None:
    """Carimba o pedido original do spoke no motivo, IN PLACE.

    O motivo que chega ao roteador sai do ``args['motivo']`` da chamada
    REGISTRADA (``agent_run_service._last_transfer_reason``), nunca do retorno
    desta tool — por isso a escrita tem de ser no próprio dict recebido.
    """
    quem = caller.get("display_name") or caller.get("agent_key") or "o agente anterior"
    motivo = str(args.get("motivo") or "").strip()
    pedido_txt = f"{quem} pediu encaminhamento para '{pedido}'"
    args["motivo"] = f"{pedido_txt}. {motivo}" if motivo else pedido_txt


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

    coerced_from = None
    try:
        target_agent = agent_repo.get(target)
        if not target_agent or not target_agent.get("enabled"):
            disponiveis = [a["agent_key"] for a in agent_repo.list_all()
                           if a.get("enabled")]
            return (f"Erro: o agente '{target}' não existe ou está desativado. "
                    f"Agentes disponíveis: {', '.join(disponiveis) or '(nenhum)'}.")

        conv = conversation_repo.get_open_for_contact_scoped(ctx.contact)
        if not conv:
            return "Erro: não há conversa aberta para transferir."

        if conv.get("active_agent_key") == target:
            return f"O agente '{target}' já está atendendo esta conversa."

        # Validação pelo papel do agente ATUAL (plano 30 F5 — hub-and-spoke):
        # roteador respeita a própria allowlist; spoke SÓ devolve pro roteador.
        current_key = _caller_agent_key(conv)
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
                    # COERÇÃO, não recusa: reescreve o destino para o hub e leva
                    # junto o pedido original, para o roteador decidir com ele.
                    coerced_from = target
                    target = router["agent_key"]
                    target_agent = router
                    _record_intent(args, current, coerced_from)

        conversation_repo.set_agent(conv["id"], target)
        logger.info("Handoff: conversa %s -> agente '%s' (motivo=%s)",
                    conv["id"], target, args.get("motivo") or "-")
        # plano 33 F1: paridade com o handoff MANUAL — o caminho manual
        # (conversation_service.set_agent) emite ``conversation_updated`` com
        # ``active_agent_key`` e por isso o badge da sidebar/board atualiza ao
        # vivo; o handoff da IA só emitia o evento de domínio (que NÃO está no
        # mapa de projeção WS). Emitimos o MESMO evento que o front já consome,
        # com um payload NOVO (não mutar o DTO ConversationAgentChanged, que é
        # compartilhado no fan-out de plugins e não tem ``active_agent_key``).
        # Best-effort: um broadcast que falhe nunca pode derrubar o handoff.
        try:
            from plugins.context import broadcast
            broadcast("conversation_updated",
                      {"conversation_id": conv["id"],
                       "contact_id": ctx.contact.id,
                       "active_agent_key": target})
        except Exception:
            logger.debug("broadcast conversation_updated (handoff) falhou p/ conversa %s",
                         conv["id"])
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
    if coerced_from:
        return (f"Agentes especializados não transferem direto entre si. A conversa "
                f"foi devolvida ao roteador '{label}', com o seu pedido "
                f"('{coerced_from}') registrado no motivo — ele decide o próximo "
                f"destino. NÃO chame transferir_agente de novo nesta mensagem.")
    return (f"Transferência registrada: as próximas mensagens desta conversa serão "
            f"atendidas por '{label}'.")
