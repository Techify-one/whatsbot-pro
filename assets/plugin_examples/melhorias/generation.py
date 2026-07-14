"""Seam de geração da análise de melhoria (plugin ``melhorias``).

Este módulo isola a produção da análise atrás de uma interface trocável, para
que uma futura implementação MULTI-AGENTE (um agente que orquestra sub-agentes)
possa substituir a atual chamada DIRETA a um modelo **sem reescrever os call
sites** (``logic.decide_suggestion`` só chama ``get_generator().generate(ctx)``).

- ``DirectApiGenerator`` (DEFAULT) — porta, intacto, o comportamento one-shot
  não-agentico do antigo ``app.services.improvement_service``: reconstrói a
  cadeia de agentes do turno (roteador → spoke) a partir de
  ``executions.routing_steps`` + ``execution_steps.agent_key``, mostra o prompt
  INLINE CRU de cada agente (renderizado com ``ai_variables`` — deliberadamente
  NÃO o system prompt enriquecido de runtime), as tools atribuídas e usadas por
  agente, o histórico recente da conversa marcada e o feedback do operador, e
  pede ao modelo um diagnóstico + recomendações. Usa o cliente SÍNCRONO ISOLADO
  do handler (``handler._get_client()``) e grava usage com ``call_type="improvement"``
  (nome preservado — identidade histórica dos registros de ``usage``).
- ``MultiAgentGenerator`` — stub para o futuro (levanta ``NotImplementedError``).
- ``StubGenerator`` — análise determinística p/ testes (sem LLM).

O backend ativo vem da config ``plugin.melhorias.generator_backend`` (default
``"direct"``), lida em ``get_generator()`` — trocar de backend é mudar essa chave
ou editar ``_BACKENDS``, sem tocar em ``logic``.

Modelo/prompt vêm do ``GenContext`` (``model_override``/``prompt_override``,
alimentados por ``plugin.melhorias.model``/``.prompt``), NÃO de atributos do
handler — o core não conhece mais este recurso.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from agent import agent_factory, history_filter
from ai_engine import dynamic_registry
from db.repositories import agent_repo, config_repo, execution_repo, message_repo

logger = logging.getLogger(__name__)

_BACKEND_CONFIG_KEY = "plugin.melhorias.generator_backend"


# Prompt de sistema padrão da análise one-shot. Editável pelo operador via a
# seção do plugin na aba "Configurações de IA" (persiste em ``plugin.melhorias.prompt``);
# um override vazio cai neste texto, então ajustes nesta constante alcançam todas
# as instalações que nunca customizaram o prompt.
DEFAULT_IMPROVEMENT_PROMPT = (
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


@dataclass
class GenContext:
    """Tudo que um gerador precisa para produzir a análise.

    ``handler`` é o seam de LLM do core (``_get_client`` / ``_record_usage`` /
    ``_get_contact`` / ``max_context_messages``). ``target_message`` é
    ``{content, ts, _id}`` da resposta marcada. ``model_override`` /
    ``prompt_override`` vêm das settings do plugin (vazio = fallback)."""
    handler: object
    phone: str
    target_message: dict
    feedback: str
    conversation_id: int | None = None
    model_override: str = ""
    prompt_override: str = ""


@dataclass
class GenResult:
    analysis: str
    model: str


class SuggestionGenerator(Protocol):
    def generate(self, ctx: GenContext) -> GenResult: ...


# ── Helpers (portados do improvement_service, intactos) ──────────────────────

def _find_execution_around(phone: str, ts: float) -> dict | None:
    """Best-effort: a execução COMPLETA (steps + routing) que produziu ``ts``.

    Encontra a execução cuja janela ``[started_at - 5s, completed_at + 15s]``
    contém o ts da resposta marcada. Degrada para ``None`` quando nada casa ou o
    tracking está indisponível — a análise ainda funciona, só sem as seções de
    tools/cadeia."""
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
    """Os steps ``tool_executed`` da execução como ``[{tool, args, agent_key}]``."""
    tools: list[dict] = []
    for step in (execution or {}).get("steps", []):
        if step.get("step_type") == "tool_executed":
            data = step.get("data") or {}
            tools.append({"tool": data.get("tool"), "args": data.get("args"),
                          "agent_key": step.get("agent_key")})
    return tools


def _agent_chain(execution: dict | None) -> list[str]:
    """Chaves de agente do turno, ordenadas e dedupadas (roteador → spoke)."""
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


class DirectApiGenerator:
    """DEFAULT: chamada DIRETA a um modelo (comportamento one-shot de antes)."""

    def generate(self, ctx: GenContext) -> GenResult:
        handler = ctx.handler
        phone = ctx.phone
        if not getattr(handler, "api_key", ""):
            return GenResult("[WhatsBot] API key não configurada.", "")

        # Canal da conversa marcada. Sem id → default (comportamento legado).
        from db.repositories import conversation_repo
        channel_id = "default"
        if ctx.conversation_id:
            try:
                conv = conversation_repo.get_with_channel(int(ctx.conversation_id))
                if conv and conv.get("channel_id"):
                    channel_id = conv["channel_id"]
            except (TypeError, ValueError):
                pass
        contact = handler._get_contact(phone, channel_id=channel_id)

        target_content = (ctx.target_message.get("content") or "").strip()
        target_ts = ctx.target_message.get("ts") or 0

        # 1. Cadeia de agentes do turno que produziu a resposta marcada.
        execution = _find_execution_around(phone, target_ts)
        chain = _agent_chain(execution)
        used = _tools_used(execution)

        agent_spec = agent_factory.build_for_contact(handler, contact)
        if not chain and agent_spec:
            chain = [agent_spec.agent_key]

        try:
            variables = dynamic_registry.variables_map()
        except Exception:
            variables = {}

        registered = {t["name"]: t for t in handler.list_tools() if t.get("enabled")}

        def _tool_line(name: str) -> str:
            desc = (registered.get(name) or {}).get("current_description") or ""
            return f"- {name}: {desc}".rstrip()

        # 2. Uma seção por agente do turno: prompt inline cru (renderizado com
        #    ai_variables), tools atribuídas e as usadas neste turno.
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

        # 3. Histórico recente — escopado à conversa marcada quando temos o id.
        if ctx.conversation_id:
            history = message_repo.get_context_by_conversation(
                int(ctx.conversation_id), handler.max_context_messages)
        else:
            history = message_repo.get_context(contact.id, handler.max_context_messages)
        # plano 43: a análise vê o MESMO histórico que a IA de atendimento viu —
        # aplica a mesma lista-negra por regex (config ``ai_history_exclude_patterns``).
        # A resposta-alvo marcada NUNCA é cortada (a análise se ancora nela, mesmo
        # que case um padrão).
        compiled = history_filter.load_compiled()
        if compiled:
            history = [
                m for m in history
                if (m.get("role") == "assistant"
                    and (m.get("content") or "").strip() == target_content)
                or not history_filter.matches(
                    m.get("role") or "", m.get("content") or "", compiled)
            ]
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

        feedback_block = ctx.feedback.strip() or "(o operador não detalhou o que saiu errado)"

        analysis_system = (ctx.prompt_override or "").strip() or DEFAULT_IMPROVEMENT_PROMPT
        analysis_user = (
            f"## Agentes do turno (na ordem em que atuaram)\n{agents_block}\n\n"
            f"## Histórico recente da conversa\n{history_block}\n\n"
            f"## Resposta marcada como incorreta\n{target_content}\n\n"
            f"## O que o operador disse que saiu errado\n{feedback_block}\n"
        )

        # Modelo: override do plugin → modelo do agente ativo do turno → agente
        # resolvido → DEFAULT_MODEL (mesma precedência de antes).
        active_key = (execution or {}).get("agent_key") or (chain[-1] if chain else None)
        active_agent = agent_rows.get(active_key) or (agent_repo.get(active_key)
                                                      if active_key else None)
        active_model = dict((active_agent or {}).get("model_config") or {}).get("model")
        analysis_model = (ctx.model_override
                          or active_model
                          or (agent_spec.model_config.get("model") if agent_spec else None)
                          or agent_factory.DEFAULT_MODEL)
        try:
            client = handler._get_client()
            response = client.chat.completions.create(
                model=analysis_model,
                temperature=0.4,
                # Generoso de propósito: reasoning_tokens contam contra este teto
                # de forma não-determinística. 8000 cobre reasoning pesado + análise.
                max_tokens=8000,
                timeout=120,
                messages=[
                    {"role": "system", "content": analysis_system},
                    {"role": "user", "content": analysis_user},
                ],
            )
            handler._record_usage(phone, "improvement", analysis_model, response)
            choice = response.choices[0]
            msg = choice.message
            text = (msg.content or "").strip()
            if not text:
                text = (getattr(msg, "reasoning_content", None)
                        or getattr(msg, "reasoning", None) or "").strip()
            if not text:
                finish = getattr(choice, "finish_reason", None)
                logger.warning(
                    "Improvement analysis returned empty content for %s "
                    "(model=%s, finish_reason=%s)", phone, analysis_model, finish)
                return GenResult(
                    "[WhatsBot] O modelo não retornou texto de análise "
                    f"(finish_reason={finish}). Tente gerar novamente ou troque "
                    "o modelo em Configurações → IA.", analysis_model)
            return GenResult(text, analysis_model)
        except Exception as e:
            logger.error("Improvement analysis failed for %s: %s", phone, e)
            return GenResult(
                f"[WhatsBot] Falha ao gerar a análise de melhoria: {e}", analysis_model)


class MultiAgentGenerator:
    """FUTURO: análise via um agente que orquestra sub-agentes. Ainda não
    implementado — existe para documentar o seam. Trocar o backend para
    ``"multi_agent"`` só faz sentido quando esta classe estiver pronta."""

    def generate(self, ctx: GenContext) -> GenResult:
        raise NotImplementedError(
            "MultiAgentGenerator ainda não implementado — use o backend 'direct'.")


class StubGenerator:
    """Análise determinística p/ testes (sem LLM)."""

    def generate(self, ctx: GenContext) -> GenResult:
        content = (ctx.target_message.get("content") or "").strip()
        model = ctx.model_override or "stub-model"
        return GenResult(
            f"**Diagnóstico**\n[stub] Resposta marcada: {content}\n\n"
            f"**Recomendações**\n- [stub] feedback: {ctx.feedback.strip() or '(vazio)'}",
            model)


_BACKENDS = {
    "direct": DirectApiGenerator,
    "multi_agent": MultiAgentGenerator,
    "stub": StubGenerator,
}


def get_generator() -> SuggestionGenerator:
    """O gerador ativo, escolhido por ``plugin.melhorias.generator_backend``
    (default ``"direct"``). Backend desconhecido cai no ``DirectApiGenerator``."""
    backend = config_repo.get(_BACKEND_CONFIG_KEY, "direct") or "direct"
    return _BACKENDS.get(backend, DirectApiGenerator)()
