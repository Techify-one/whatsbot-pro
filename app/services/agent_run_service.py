"""Agent-turn orchestration service (Plano 23 · Fase B5, master §2.3/§2.4).

Owns ONE agent turn: history assembly, agent-spec resolution, the
``llm.before``/``llm.after`` events, the AGNO run, usage recording off
``RunMetrics``, within-turn multi-agent routing, and the assistant-row save.
Extracted from ``AgentHandler.aprocess_message`` so the handler is a thin facade
that delegates here (``handler.aprocess_message`` → :func:`run_turn`).

Reconciliation of the two agent paths (Q3): there is a SINGLE resolution funnel
— ``agent.agent_factory.build_for_contact`` — that already cascades
conversation→inbox→default and returns an ``AgentSpec`` for BOTH the static
single-Agent default and the ``ai_agents`` config-in-DB agents (gated by
``ai_engine_enabled`` upstream in the factory/registry). On top of that resolved
spec we apply the NEW ``filter.agent.resolve`` seam (§4.2): a plugin/DB layer may
return a replacement ``AgentSpec`` to swap the agent for this turn, or ``None`` to
KEEP the default (``None`` here means "no swap", NOT "abort"). ``prompt_builder``
and the handler's ``tool_registry`` feed both paths identically.

The handler still owns the tool registry, the LLM clients, ``ContactMemory`` and
config; this service reads them through the passed ``handler`` (no ``server.app``
import — deps flow in via the handler instance, like B3/B4).
"""

from __future__ import annotations

import asyncio
import logging
import time

from agent import agno_engine, agent_factory
from agent.execution import (
    track_step, set_execution_agent_key,
    set_current_step_agent, set_execution_routing_steps,
)
from agent.handler import ProcessResult
from channels import ai_settings
from plugins.events import apply_filter, emit_with_filter

logger = logging.getLogger(__name__)


async def _resolve_agent_spec(handler, contact, sender):
    """Resolve the per-turn ``AgentSpec`` then apply ``filter.agent.resolve``.

    The factory cascade (conversation→inbox→default agent, config-in-DB) gives the
    baseline spec for BOTH agent paths. ``filter.agent.resolve`` (NEW, §4.2) lets a
    plugin swap the resolved agent for this turn; returning ``None`` KEEPS the
    default (the filter's abort/``None`` is reinterpreted as "no swap" here, since
    a missing agent must not silently drop the turn).

    Returns the (possibly swapped) ``AgentSpec``. Raises
    ``agent_factory.AgentResolutionError`` exactly as before when the DB is broken.
    """
    spec = agent_factory.build_for_contact(handler, contact)
    swapped = await apply_filter(
        "filter.agent.resolve", spec,
        {"phone": sender, "contact_id": getattr(contact, "id", None),
         "channel_id": getattr(contact, "channel_id", "default")},
    )
    # ``None`` ⇒ keep default (no swap); only honour a real replacement spec.
    if swapped is not None and swapped is not spec:
        spec = swapped
    return spec


def _handoff_notice(from_agent: str, reason: str | None,
                    is_reinvoke: bool = False) -> str:
    """Synthetic user turn injected on a routing hop (plano 29 A2, estilo nexus).

    Threads WHY the conversation arrived: the target agent sees the sender and
    the ``transferir_agente`` motivo instead of receiving the hop blind.
    ``is_reinvoke`` (A3) tells a revisited agent to re-decide with the motivo
    instead of repeating its earlier take (nexus ``skip_history`` equivalent).
    """
    lines = [f"[REDIRECIONAMENTO de {from_agent}]"]
    if reason:
        lines.append(f"Motivo: {reason}")
    lines.append("")
    if is_reinvoke:
        lines.append("(você já atendeu esta conversa neste turno — reavalie com o "
                     "motivo acima e responda ao cliente; não repita a mesma "
                     "transferência)")
    else:
        lines.append("(responda à mensagem atual do cliente acima)")
    return "\n".join(lines)


def _resolve_max_route_depth() -> int:
    """Depth cap do routing (plano 29 A3): config ``ai_max_route_depth``.

    Fallback no default do módulo (:data:`ai_engine.routing.MAX_ROUTING_DEPTH`);
    valores malformados/não-positivos caem no default (fail safe, not open)."""
    from ai_engine import routing as _routing
    try:
        from db.repositories import config_repo
        n = int(config_repo.get("ai_max_route_depth", _routing.MAX_ROUTING_DEPTH))
        return n if n >= 1 else _routing.MAX_ROUTING_DEPTH
    except Exception:
        return _routing.MAX_ROUTING_DEPTH


def _last_transfer_reason(executed_tools: list[dict] | None) -> str | None:
    """Motivo of the last real (non-skipped) ``transferir_agente`` call, if any."""
    for e in reversed(executed_tools or []):
        if e.get("tool") == "transferir_agente" and not e.get("skipped"):
            motivo = (e.get("args") or {}).get("motivo")
            return str(motivo) if motivo else None
    return None


async def _run_routing_hop(handler, contact, sender, context_messages, spec, *,
                           disable_tools, handoff: dict | None = None):
    """Build prompt/messages/tools for ``spec`` and run one AGNO hop.

    Mirrors the inline first-hop build, but returns ``None`` if a filter
    aborts (so within-turn routing stops on the last good reply instead of
    sending an empty message). Used only for handoff continuations.

    ``handoff`` (plano 29 A2/A3): ``{"from": agent_key, "reason": str | None,
    "is_reinvoke": bool}`` — when present, a synthetic user turn
    (:func:`_handoff_notice`) is appended AFTER the frozen context so this hop
    sees who handed the conversation over and why (the nexus
    ``[REDIRECIONAMENTO de X]`` pattern).
    """
    eff_split = bool(ai_settings.value(
        getattr(contact, "channel_id", "default"),
        "split_messages", handler.split_messages))
    system_prompt_str = handler._build_system_prompt(
        contact, base_prompt=spec.base_prompt, split_messages=eff_split)
    system_prompt_str = await apply_filter(
        "filter.system_prompt", system_prompt_str, {"phone": sender})
    if system_prompt_str is None:
        return None
    messages = [{"role": "system", "content": system_prompt_str}, *context_messages]
    if handoff:
        messages.append({
            "role": "user",
            "content": _handoff_notice(handoff.get("from") or "?",
                                       handoff.get("reason"),
                                       bool(handoff.get("is_reinvoke"))),
        })
    messages = await apply_filter("filter.llm.messages", messages, {"phone": sender})
    if messages is None:
        return None
    active_tools = [] if disable_tools else handler._select_active_tools(spec)
    active_tools = await apply_filter("filter.llm.tools", active_tools, {"phone": sender})
    if active_tools is None:
        active_tools = []
    return await agno_engine.run_async(
        handler, contact, sender, messages, active_tools, model_config=spec.model_config)


async def _continue_routing(handler, contact, sender, context_messages, first_spec,
                            first_result, executed_tools, usage_dict, *, disable_tools):
    """Within-turn routing: if the agent handed off this turn, let the new
    agent answer the same message (up to ``MAX_ROUTING_DEPTH`` total hops).

    Returns ``(result, executed_tools, usage_dict, routing_steps)``. When no
    handoff happened (the common case) it returns the inputs unchanged and
    ``run_hop`` is never invoked, so the single-agent path is untouched.
    """
    from ai_engine import routing as _routing

    combined = list(executed_tools)
    acc_usage = dict(usage_dict) if usage_dict else {}
    # Plano 29 A2: state of the hop that HANDED OFF — the next hop reads the
    # motivo of its last transferir_agente call and receives it in-context.
    last_hop = {"agent": first_spec.agent_key,
                "executed": first_result.executed_tools}

    def _resolve_next():
        # transferir_agente persisted the handoff on the conversation; re-resolve.
        try:
            return agent_factory.build_for_contact(handler, contact).agent_key
        except agent_factory.AgentResolutionError:
            return None

    def _pending_reason():
        return _last_transfer_reason(last_hop["executed"])

    async def _run_hop(agent_key, *, is_reinvoke=False):
        try:
            spec = agent_factory.build_for_contact(handler, contact)
        except agent_factory.AgentResolutionError:
            return None
        if spec.agent_key != agent_key:
            return None
        set_execution_agent_key(spec.agent_key)
        set_current_step_agent(spec.agent_key)
        handoff = {"from": last_hop["agent"], "reason": _pending_reason(),
                   "is_reinvoke": is_reinvoke}
        hop = await _run_routing_hop(
            handler, contact, sender, context_messages, spec,
            disable_tools=disable_tools, handoff=handoff)
        if hop is None:
            return None
        if hop.usage:
            handler._record_usage_tokens(
                sender, "text",
                spec.model_config.get("model") or agent_factory.DEFAULT_MODEL,
                hop.usage.get("prompt_tokens", 0),
                hop.usage.get("completion_tokens", 0),
                hop.usage.get("total_tokens", 0))
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                acc_usage[k] = acc_usage.get(k, 0) + hop.usage.get(k, 0)
        combined.extend(hop.executed_tools or [])
        last_hop["agent"] = agent_key
        last_hop["executed"] = hop.executed_tools
        return hop

    result, steps, halted = await _routing.run_with_routing(
        first_result=first_result, first_agent_key=first_spec.agent_key,
        resolve_next=_resolve_next, run_hop=_run_hop,
        max_depth=_resolve_max_route_depth(),
        get_reason=_pending_reason)
    if steps:
        set_execution_routing_steps(steps)
    if halted is not None:
        # Plano 29 A4: fim anômalo (cap estourado com handoff pendente) → rede de
        # segurança: escala pra humano em vez do break silencioso. A10: registra
        # o halt na execução. Ambos best-effort — nunca quebram o turno.
        chain = "→".join([first_spec.agent_key] + [s["to"] for s in steps])
        track_step("routing_halted", {"reason": halted.get("reason"),
                                      "pending": halted.get("pending"),
                                      "chain": chain}, status="error")
        motivo = (f"Limite de roteamento atingido ({chain}). "
                  f"Nenhum agente conseguiu resolver.")
        try:
            feedback = await asyncio.to_thread(
                handler._dispatch_tool, contact, "transfer_to_human",
                {"reason": motivo})
            combined.append({"tool": "transfer_to_human",
                             "args": {"reason": motivo},
                             "result": feedback, "forced": True})
            logger.warning("Routing halted for %s (%s) — escalado pra humano",
                           sender, chain)
        except Exception:
            logger.exception("Escalação pra humano falhou para %s", sender)
    return result, combined, (acc_usage or None), steps


async def run_turn(handler, sender: str, text: str, *,
                   save_user_message: bool = True,
                   save_response: bool = True,
                   image_path: str | None = None,
                   audio_path: str | None = None,
                   disable_tools: bool = False,
                   channel_id: str = "default") -> ProcessResult:
    """Run one agent turn for ``sender`` and return the ``ProcessResult``.

    Async + cancellable: uses the AGNO async path so cancelling the surrounding
    asyncio task aborts the in-flight HTTP request. ``channel_id`` routes the
    contact/conversation to the originating channel's inbox (plano 11) so agent
    resolution honours conversation→inbox→default.
    """
    if not handler.api_key:
        return ProcessResult(reply="[WhatsBot] API key não configurada.")

    contact = handler._get_contact(sender, channel_id=channel_id)

    media_type: str | None = None
    media_path: str | None = None
    if image_path:
        media_type = "image"
        media_path = image_path
    elif audio_path:
        media_type = "audio"
        media_path = audio_path

    if save_user_message:
        contact.add_message("user", text or "", media_type=media_type, media_path=media_path)

    # Per-channel overrides (plano 21): context size + split format follow the
    # channel's config, falling back to the global (handler) value.
    eff_max_context = ai_settings.value(
        channel_id, "max_context_messages", handler.max_context_messages)
    eff_split = bool(ai_settings.value(
        channel_id, "split_messages", handler.split_messages))

    context_messages = contact.get_context_messages(eff_max_context)
    if eff_split:
        context_messages = handler._encode_history_for_split(context_messages)

    # Plano 37 A1: memória compacta de tool. O role ``tool_call`` é excluído do
    # contexto do LLM (get_context) e o motor roda stateless, então o modelo
    # re-executa as mesmas tools todo turno. Injeta um bloco ``system`` curto
    # ("já executadas / atributos já definidos") que atravessa split_messages
    # (concatenado ao system prompt) e é herdado por todos os hops de routing.
    # Best-effort + kill-switch: falha ou OFF ⇒ segue sem o bloco.
    try:
        from agent import tool_memory
        _mem_block = tool_memory.build_block(contact)
        if _mem_block:
            context_messages = [*context_messages,
                                {"role": "system", "content": _mem_block}]
    except Exception:
        logger.debug("tool_memory: injeção falhou para %s", sender, exc_info=True)

    # Config-in-DB: resolve the DB-driven agent for this contact + the
    # filter.agent.resolve seam. Always returns a spec; a genuinely broken DB
    # raises AgentResolutionError, which we isolate to this one conversation
    # (error card, nothing sent to client).
    try:
        agent_spec = await _resolve_agent_spec(handler, contact, sender)
    except agent_factory.AgentResolutionError as e:
        handler._emit_resolution_error(contact, sender, e)
        # aborted: já sinalizado com card próprio — não é mudez do motor.
        return ProcessResult(reply="", aborted=True)
    set_execution_agent_key(agent_spec.agent_key)
    set_current_step_agent(agent_spec.agent_key)
    # The AI is now handling this message: attribute the conversation to it so
    # the inbox shows its assignee chip (covers reopened/legacy threads).
    handler._ensure_conversation_agent(contact, agent_spec)
    model = agent_spec.model_config.get("model") or agent_factory.DEFAULT_MODEL
    model_config = agent_spec.model_config
    base_prompt = agent_spec.base_prompt

    system_prompt_str = handler._build_system_prompt(
        contact, base_prompt=base_prompt, split_messages=eff_split)
    system_prompt_str = await apply_filter(
        "filter.system_prompt", system_prompt_str, {"phone": sender}
    )
    if system_prompt_str is None:
        # aborted: silêncio intencional de plugin (contrato do filter) — não é
        # mudez do motor (plano 31 F4).
        return ProcessResult(reply="", aborted=True)

    messages = [
        {"role": "system", "content": system_prompt_str},
        *context_messages,
    ]
    messages = await apply_filter(
        "filter.llm.messages", messages, {"phone": sender}
    )
    if messages is None:
        # aborted: idem — plugin cancelou o turno de propósito.
        return ProcessResult(reply="", aborted=True)

    active_tools = [] if disable_tools else handler._select_active_tools(agent_spec)
    active_tools = await apply_filter(
        "filter.llm.tools", active_tools, {"phone": sender}
    )
    if active_tools is None:
        active_tools = []

    try:
        _llm_t0 = time.monotonic()
        await emit_with_filter("llm.before", {
            "phone": sender,
            "model": model,
            "message_count": len(messages),
            "has_tools": bool(active_tools),
            "tool_count": len(active_tools),
            "image_path": image_path,
            "audio_path": audio_path,
            "ts": time.time(),
        })

        # Delegate the reasoning + tool-calling loop to the AGNO engine.
        # Tool filters/events (filter.tool.args/result, tool.before/after)
        # are applied inside the wrapped tool entrypoints; usage is reported
        # via AGNO RunMetrics rather than an OpenAI response object.
        result = await agno_engine.run_async(
            handler, contact, sender, messages, active_tools,
            model_config=model_config,
        )
        reply = result.reply
        executed_tools = result.executed_tools
        usage_dict = result.usage

        if usage_dict:
            handler._record_usage_tokens(
                sender, "text", model,
                usage_dict.get("prompt_tokens", 0),
                usage_dict.get("completion_tokens", 0),
                usage_dict.get("total_tokens", 0),
            )

        # Within-turn routing (config-in-DB): a mid-turn handoff lets the new
        # agent answer this same message. No-op when no handoff occurred, so
        # the single-agent path is unchanged.
        result, executed_tools, usage_dict, routing_steps = await _continue_routing(
            handler, contact, sender, context_messages, agent_spec,
            result, executed_tools, usage_dict, disable_tools=disable_tools)
        reply = result.reply
        # Agente que EFETIVAMENTE respondeu: o último hop do routing (ou o resolvido
        # no início, sem handoff). Capturado ANTES de qualquer efeito colateral do
        # turno (ex.: transfer_to_human zera o active_agent_key da conversa), então é
        # a fonte confiável para atribuir a resposta e os cards de tool ao agente.
        final_agent_key = routing_steps[-1]["to"] if routing_steps else agent_spec.agent_key

        if save_response:
            contact.add_message("assistant", reply, agent_key=final_agent_key)
        logger.info("Processed message from %s", sender)

        updated_info = None
        # ``skipped`` marca a chamada que NÃO rodou (bloqueada por filter ou por
        # limite de tool — agno_engine). Sem o filtro, um save_contact_info
        # bloqueado faria o turno reportar dados de contato como salvos, e isso
        # vaza para o payload de ``llm.after`` que os plugins leem. Mesmo molde de
        # ``messaging_service`` no set_custom_attribute (plano 91·F5).
        if any(tc.get("tool") == "save_contact_info" and not tc.get("skipped")
               for tc in executed_tools):
            updated_info = dict(contact.info)
            updated_info["observations"] = list(updated_info.get("observations", []))

        await emit_with_filter("llm.after", {
            "phone": sender,
            "model": model,
            "reply": reply,
            "tool_calls": executed_tools,
            "usage": usage_dict,
            "latency_ms": int((time.monotonic() - _llm_t0) * 1000),
            "ts": time.time(),
        })

        return ProcessResult(reply=reply, tool_calls=executed_tools,
                             contact_info=updated_info, agent_key=final_agent_key)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("LLM error for %s: %s", sender, e)
        track_step("error", {"error": str(e), "phase": "llm_call"}, status="error")
        await emit_with_filter("llm.after", {
            "phone": sender, "model": model,
            "reply": "", "tool_calls": [], "usage": None,
            "error": str(e),
            "latency_ms": int((time.monotonic() - locals().get("_llm_t0", time.monotonic())) * 1000),
            "ts": time.time(),
        })
        error_msg = str(e)
        if "401" in error_msg or "unauthorized" in error_msg.lower():
            return ProcessResult(reply="[WhatsBot] API key inválida. Verifique sua chave Techify.")
        if "429" in error_msg or "rate" in error_msg.lower():
            return ProcessResult(reply="[WhatsBot] Limite de requisições atingido. Tente novamente em instantes.")
        return ProcessResult(reply="[WhatsBot] Erro ao processar mensagem. Tente novamente.")
