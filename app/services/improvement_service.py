"""Reply-improvement analysis service (Plano 23 · Fase B5; multi-agente no plano 31).

``generate_improvement`` is a ONE-SHOT, NON-agentic quality analysis of an AI
reply flagged by an operator as incorrect. It reconstructs the AGENT CHAIN of
the turn that produced the flagged reply (router → spoke, via
``executions.routing_steps`` + ``execution_steps.agent_key``), shows each
agent's RAW inline prompt (rendered with ``ai_variables`` — deliberately NOT
the runtime-enriched system prompt), the tools assigned to and actually used
by each agent, the recent history of the flagged CONVERSATION (multi-channel
aware) and the operator's feedback, then asks the model for a diagnosis +
concrete prompt-tuning recommendations. The route saves the result as a
panel-only ``system`` message in that same conversation.

DECISION (Plano 23 Q1): this keeps the ISOLATED SYNC client — it calls
``handler._get_client()`` (the sync OpenAI client from ``agent.llm``) directly
rather than being forced async, to minimise churn. Tests patch
``handler._get_client``; this service goes through that exact seam so the patch
takes effect. Usage is recorded under ``call_type="improvement"``.

``conversation_id=None`` (plano 31 D9) preserves the legacy single-channel
behaviour: default channel + contact-wide history.
"""

from __future__ import annotations

import json
import logging

from agent import agent_factory
from ai_engine import dynamic_registry
from db.repositories import agent_repo, conversation_repo, execution_repo, message_repo

logger = logging.getLogger(__name__)


def _find_execution_around(phone: str, ts: float) -> dict | None:
    """Best-effort: the FULL execution (steps + routing) that produced ``ts``.

    Finds the execution whose ``[started_at - 5s, completed_at + 15s]`` window
    contains the flagged reply's timestamp. Degrades gracefully to ``None``
    when no execution matches or tracking is unavailable — the analysis still
    works, just without the tools/chain sections.
    """
    try:
        executions = execution_repo.list_executions(limit=50, phone=phone)
    except Exception as e:
        logger.debug("list_executions failed for %s: %s", phone, e)
        return None
    target = None
    for ex in executions:
        started = ex.get("started_at")
        if started is None:
            continue
        completed = ex.get("completed_at") or started
        if (started - 5) <= ts <= (completed + 15):
            target = ex
            break
    if not target:
        return None
    try:
        return execution_repo.get_by_id(target["id"])
    except Exception as e:
        logger.debug("get_by_id failed for execution %s: %s", target.get("id"), e)
        return None


def _tools_used(execution: dict | None) -> list[dict]:
    """The execution's ``tool_executed`` steps as ``[{tool, args, agent_key}]``.

    Covers every hop of the turn; ``agent_key`` may be ``None`` on rows written
    before per-step attribution existed.
    """
    tools: list[dict] = []
    for step in (execution or {}).get("steps", []):
        if step.get("step_type") == "tool_executed":
            data = step.get("data") or {}
            tools.append({"tool": data.get("tool"), "args": data.get("args"),
                          "agent_key": step.get("agent_key")})
    return tools


def _agent_chain(execution: dict | None) -> list[str]:
    """Ordered, deduped agent keys of the turn (plano 31 C1+).

    ``routing_steps`` (JSON ``[{from,to,depth,reason}]``) reconstructs the
    router→spoke chain; an execution without routing falls back to its own
    ``agent_key``. Order = order of first participation.
    """
    if not execution:
        return []
    raw = execution.get("routing_steps")
    steps: list = []
    if isinstance(raw, str):
        try:
            steps = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            steps = []
    elif isinstance(raw, list):
        steps = raw
    chain: list[str] = []
    if steps:
        first = (steps[0] or {}).get("from")
        if first:
            chain.append(first)
        for s in steps:
            to = (s or {}).get("to")
            if to:
                chain.append(to)
    elif execution.get("agent_key"):
        chain.append(execution["agent_key"])
    seen: set[str] = set()
    ordered: list[str] = []
    for key in chain:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _format_tools_used(used: list[dict]) -> str:
    return "\n".join(
        f"- {u.get('tool')}({json.dumps(u.get('args') or {}, ensure_ascii=False)})"
        for u in used
    )


def generate_improvement(handler, phone: str, target_message: dict,
                         feedback: str, *,
                         conversation_id: int | None = None) -> str:
    """One-shot quality analysis of an AI reply flagged as incorrect.

    DIRECT, non-agentic call to the LLM (does NOT go through the AGNO engine).
    Returns the trimmed analysis (or a ``[WhatsBot] ...`` fallback string)."""
    if not handler.api_key:
        return "[WhatsBot] API key não configurada."

    # Channel of the flagged conversation (plano 31 F3). No id → default (D9).
    channel_id = "default"
    if conversation_id:
        try:
            conv = conversation_repo.get_with_channel(int(conversation_id))
            if conv and conv.get("channel_id"):
                channel_id = conv["channel_id"]
        except (TypeError, ValueError):
            pass
    contact = handler._get_contact(phone, channel_id=channel_id)

    target_content = (target_message.get("content") or "").strip()
    target_ts = target_message.get("ts") or 0

    # 1. The agent chain of the turn that produced the flagged reply (C1+).
    execution = _find_execution_around(phone, target_ts)
    chain = _agent_chain(execution)
    used = _tools_used(execution)

    # Fallbacks/model resolution still come from the live resolution (may raise
    # AgentResolutionError on a genuinely broken DB — caller turns it into 500,
    # same as before).
    agent_spec = agent_factory.build_for_contact(handler, contact)
    if not chain and agent_spec:
        chain = [agent_spec.agent_key]

    try:
        variables = dynamic_registry.variables_map()
    except Exception:
        variables = {}

    # Registered+enabled tools (name → row) for allowlist rendering.
    registered = {t["name"]: t for t in handler.list_tools() if t.get("enabled")}

    def _tool_line(name: str) -> str:
        desc = (registered.get(name) or {}).get("current_description") or ""
        return f"- {name}: {desc}".rstrip()

    # 2. One section per agent of the turn: raw inline prompt (rendered with
    #    ai_variables — NOT the runtime-enriched system prompt), assigned tools
    #    and the tools it actually used in this turn.
    agent_rows: dict[str, dict | None] = {k: agent_repo.get(k) for k in chain}
    unattributed = [u for u in used if not u.get("agent_key")]
    sections: list[str] = []
    for key in chain:
        agent = agent_rows.get(key)
        display = (agent or {}).get("display_name") or key
        router_tag = " — ROTEADOR" if (agent or {}).get("is_router") else ""
        if agent:
            body = agent.get("prompt") or agent_factory.DEFAULT_SYSTEM_PROMPT
            prompt_text = agent_factory.render_template(body, variables)
        else:
            prompt_text = "(agente não encontrado no banco — prompt indisponível)"
        tool_names = (agent or {}).get("tool_names")
        if agent is None:
            assigned_block = "(desconhecidas)"
        elif tool_names is None:
            assigned_block = ("Todas as tools habilitadas:\n"
                              + ("\n".join(_tool_line(n) for n in sorted(registered))
                                 or "- (nenhuma tool habilitada)"))
        else:
            lines = [_tool_line(n) for n in tool_names if n in registered]
            assigned_block = "\n".join(lines) or "- (nenhuma tool habilitada)"
        used_here = [u for u in used if u.get("agent_key") == key]
        if len(chain) == 1 and not used_here and unattributed:
            # Single-agent turn with legacy steps (no per-step agent_key):
            # everything the execution ran belongs to this agent.
            used_here, unattributed = unattributed, []
        used_block = (_format_tools_used(used_here)
                      or "Nenhuma ferramenta foi usada por este agente.")
        sections.append(
            f"### Agente: {display} ({key}){router_tag}\n"
            f"Ferramentas atribuídas:\n{assigned_block}\n"
            f"Ferramentas usadas nesta resposta:\n{used_block}\n"
            f"Prompt do agente:\n{prompt_text}"
        )
    agents_block = "\n\n".join(sections) if sections else "(nenhum agente identificado)"
    if unattributed:
        agents_block += ("\n\n### Ferramentas usadas sem atribuição a um agente\n"
                         + _format_tools_used(unattributed))

    # 3. Recent history — scoped to the flagged CONVERSATION when we have the id
    #    (multi-canal não mistura threads); contact-wide otherwise (legado).
    if conversation_id:
        history = message_repo.get_context_by_conversation(
            int(conversation_id), handler.max_context_messages)
    else:
        history = message_repo.get_context(contact.id, handler.max_context_messages)
    lines: list[str] = []
    marked = False
    for m in history:
        role = m.get("role")
        who = {"user": "Cliente", "assistant": "IA"}.get(role, str(role))
        content = (m.get("content") or "").strip()
        marker = ""
        if not marked and role == "assistant" and content == target_content:
            marker = "   ⟵ RESPOSTA MARCADA COMO INCORRETA"
            marked = True
        lines.append(f"{who}: {content}{marker}")
    history_block = "\n".join(lines) if lines else "(sem histórico)"

    # 4. Operator feedback (optional).
    feedback_block = feedback.strip() or "(o operador não detalhou o que saiu errado)"

    analysis_system = (
        "Você é um especialista em qualidade de agentes de IA conversacionais "
        "para atendimento no WhatsApp. Sua tarefa é analisar uma resposta que a "
        "IA deu a um cliente e que foi marcada por um operador humano como "
        "incorreta ou insatisfatória. O sistema pode usar VÁRIOS agentes no "
        "mesmo turno (um roteador que transfere para agentes especializados): "
        "você receberá, na ordem em que atuaram, o prompt de CADA agente que "
        "participou, as ferramentas atribuídas a cada um e as que cada um "
        "realmente usou, além do histórico da conversa e do feedback do "
        "operador. Atenção: as mensagens do histórico NÃO são atribuíveis a um "
        "agente específico — apenas as ferramentas e a cadeia de agentes são. "
        "Com base nisso você deve: (1) diagnosticar a causa provável do "
        "problema, apontando QUAL agente (ou a passagem entre eles) "
        "provavelmente originou o erro; e (2) recomendar ajustes CONCRETOS e "
        "acionáveis no prompt do(s) agente(s) responsável(is) (e, quando fizer "
        "sentido, no uso ou na descrição das ferramentas) para que esse tipo "
        "de erro não se repita. Seja objetivo e prático: cite trechos do "
        "prompt quando útil e evite generalidades. NÃO reescreva a resposta ao "
        "cliente — foque em como melhorar o(s) agente(s). Escreva em português "
        "brasileiro e estruture a saída em duas seções de markdown: "
        "'**Diagnóstico**' e '**Recomendações**' (uma lista de itens "
        "acionáveis)."
    )
    analysis_user = (
        f"## Agentes do turno (na ordem em que atuaram)\n{agents_block}\n\n"
        f"## Histórico recente da conversa\n{history_block}\n\n"
        f"## Resposta marcada como incorreta\n{target_content}\n\n"
        f"## O que o operador disse que saiu errado\n{feedback_block}\n"
    )

    # Plano 22: there is no global ``handler.model`` — the chat model comes from
    # the resolved agent. Prefer the ACTIVE agent of the turn (last of the
    # chain), then the live resolution, then DEFAULT_MODEL.
    active_agent = agent_rows.get(chain[-1]) if chain else None
    active_model = dict((active_agent or {}).get("model_config") or {}).get("model")
    analysis_model = (handler.improvement_model
                      or active_model
                      or (agent_spec.model_config.get("model") if agent_spec else None)
                      or agent_factory.DEFAULT_MODEL)
    try:
        client = handler._get_client()
        response = client.chat.completions.create(
            model=analysis_model,
            temperature=0.4,
            max_tokens=1600,
            timeout=120,
            messages=[
                {"role": "system", "content": analysis_system},
                {"role": "user", "content": analysis_user},
            ],
        )
        handler._record_usage(phone, "improvement", analysis_model, response)
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error("Improvement analysis failed for %s: %s", phone, e)
        return f"[WhatsBot] Falha ao gerar a análise de melhoria: {e}"
