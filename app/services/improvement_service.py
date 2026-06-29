"""Reply-improvement analysis service (Plano 23 · Fase B5).

``generate_improvement`` is a ONE-SHOT, NON-agentic quality analysis of an AI
reply flagged by an operator as incorrect. It assembles the agent's live system
prompt, the available tools, the tools actually used around the flagged reply,
the recent history (with the flagged reply marked inline) and the operator's
feedback, then asks the model for a diagnosis + concrete prompt-tuning
recommendations. The route saves the result as a panel-only ``system`` message.

DECISION (Plano 23 Q1): this keeps the ISOLATED SYNC client — it calls
``handler._get_client()`` (the sync OpenAI client from ``agent.llm``) directly
rather than being forced async, to minimise churn. Tests patch
``handler._get_client``; this service goes through that exact seam so the patch
takes effect. Usage is recorded under ``call_type="improvement"``.

Extracted from ``AgentHandler.generate_improvement``; the handler keeps a thin
delegate (``handler.generate_improvement`` → :func:`generate_improvement`).
"""

from __future__ import annotations

import json
import logging

from agent import agent_factory
from db.repositories import execution_repo, message_repo

logger = logging.getLogger(__name__)


def _find_tools_used_around(phone: str, ts: float) -> list[dict]:
    """Best-effort: the tools executed in the run that produced ``ts``.

    Finds the execution whose ``[started_at - 5s, completed_at + 15s]`` window
    contains the flagged reply's timestamp and returns its ``tool_executed``
    steps as ``[{"tool", "args"}, ...]``. Degrades gracefully to ``[]`` when
    no execution matches or tracking is unavailable — the analysis still
    works, just without the "ferramentas usadas" section.
    """
    try:
        executions = execution_repo.list_executions(limit=50, phone=phone)
    except Exception as e:
        logger.debug("list_executions failed for %s: %s", phone, e)
        return []
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
        return []
    try:
        full = execution_repo.get_by_id(target["id"])
    except Exception as e:
        logger.debug("get_by_id failed for execution %s: %s", target.get("id"), e)
        return []
    tools: list[dict] = []
    for step in (full or {}).get("steps", []):
        if step.get("step_type") == "tool_executed":
            data = step.get("data") or {}
            tools.append({"tool": data.get("tool"), "args": data.get("args")})
    return tools


def generate_improvement(handler, phone: str, target_message: dict,
                         feedback: str) -> str:
    """One-shot quality analysis of an AI reply flagged as incorrect.

    DIRECT, non-agentic call to the LLM (does NOT go through the AGNO engine).
    Returns the trimmed analysis (or a ``[WhatsBot] ...`` fallback string)."""
    if not handler.api_key:
        return "[WhatsBot] API key não configurada."

    contact = handler._get_contact(phone)

    # 1. The system prompt exactly as this contact would receive it.
    agent_spec = agent_factory.build_for_contact(handler, contact)
    base_prompt = agent_spec.base_prompt if agent_spec else None
    system_prompt_str = handler._build_system_prompt(contact, base_prompt=base_prompt)

    # 2. Available tools (only the ones currently enabled).
    available = [t for t in handler.list_tools() if t.get("enabled")]
    if available:
        tools_block = "\n".join(
            f"- {t['name']}: {t.get('current_description') or ''}".rstrip()
            for t in available
        )
    else:
        tools_block = "Nenhuma ferramenta disponível."

    # 3. Tools actually used around the flagged reply (best-effort).
    target_content = (target_message.get("content") or "").strip()
    target_ts = target_message.get("ts") or 0
    used = _find_tools_used_around(phone, target_ts)
    if used:
        used_block = "\n".join(
            f"- {u.get('tool')}({json.dumps(u.get('args') or {}, ensure_ascii=False)})"
            for u in used
        )
    else:
        used_block = "Nenhuma ferramenta foi usada nesta resposta."

    # 4. Recent history, with the flagged reply marked inline.
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

    # 5. Operator feedback (optional).
    feedback_block = feedback.strip() or "(o operador não detalhou o que saiu errado)"

    analysis_system = (
        "Você é um especialista em qualidade de agentes de IA conversacionais "
        "para atendimento no WhatsApp. Sua tarefa é analisar uma resposta que a "
        "IA deu a um cliente e que foi marcada por um operador humano como "
        "incorreta ou insatisfatória. Com base no prompt principal do agente, "
        "nas ferramentas disponíveis, nas ferramentas que foram realmente "
        "usadas, no histórico da conversa e no feedback do operador, você deve: "
        "(1) diagnosticar a causa provável do problema; e (2) recomendar ajustes "
        "CONCRETOS e acionáveis no prompt principal (e, quando fizer sentido, no "
        "uso ou na descrição das ferramentas) para que esse tipo de erro não se "
        "repita. Seja objetivo e prático: cite trechos do prompt quando útil e "
        "evite generalidades. NÃO reescreva a resposta ao cliente — foque em como "
        "melhorar o agente. Escreva em português brasileiro e estruture a saída "
        "em duas seções de markdown: '**Diagnóstico**' e '**Recomendações**' "
        "(uma lista de itens acionáveis)."
    )
    analysis_user = (
        f"## Prompt principal do agente\n{system_prompt_str}\n\n"
        f"## Ferramentas disponíveis\n{tools_block}\n\n"
        f"## Ferramentas usadas nesta resposta\n{used_block}\n\n"
        f"## Histórico recente da conversa\n{history_block}\n\n"
        f"## Resposta marcada como incorreta\n{target_content}\n\n"
        f"## O que o operador disse que saiu errado\n{feedback_block}\n"
    )

    # Plano 22: there is no global ``handler.model`` — the chat model comes from
    # the resolved agent. Fall back to the agent's model (then DEFAULT_MODEL).
    analysis_model = (handler.improvement_model
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
