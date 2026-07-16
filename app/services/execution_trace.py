"""Reconstrução do rastro de execução por mensagem (plano 51 · 01 F4).

Fonte única de verdade para "dado um msg_id/row de resposta da IA → qual
execução a produziu, quais agentes atuaram (roteador → spoke), quais tools
rodaram (args), e qual contexto (prompt+histórico) o modelo viu". Consumida
pelo gateway do plugin ``melhorias`` (melhoria agêntica) e por qualquer
feature futura de auditoria — em vez de cada consumidor duplicar a lógica.

Contratos:
- ``find_execution_for_message`` PREFERE o link preciso ``messages.execution_id``
  (01 F1) e degrada para o match fuzzy por janela de ts (DL2) — nunca lança.
- ``exact_context`` devolve o que foi CAPTURADO no step ``llm_context`` (01 F2,
  um por hop). ``None`` = turno sem captura → o consumidor cai em
  ``approx_context`` (aproximação viva: prompt ATUAL renderizado + histórico
  ATUAL), sinalizando "aproximado" vs "exato".
- ``executions.agent_key`` é só o agente FINAL; a cadeia real vem de
  ``routing_steps`` + ``execution_steps.agent_key`` (``agent_chain``).
"""

from __future__ import annotations

import json
import logging

from db.repositories import execution_repo, message_repo

logger = logging.getLogger(__name__)


# ── Localizar a execução ─────────────────────────────────────────────────────

def find_execution_around(phone: str, ts: float) -> dict | None:
    """Fuzzy (plano B): a execução COMPLETA cuja janela ``[started_at - 5s,
    completed_at + 15s]`` contém o ts da resposta. Degrada para ``None``."""
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


def find_execution_for_message(msg_row: dict, *, phone: str | None = None) -> dict | None:
    """A execução que produziu a resposta: link preciso → fuzzy → ``None``.

    O link (``messages.execution_id``) resolve O(1) e sem ambiguidade; linhas
    legadas (NULL) caem no fuzzy quando ``phone`` é fornecido. Nunca lança.
    """
    found = message_repo.find_execution_for_message(msg_row or {})
    if found is not None:
        return found
    if phone:
        ts = (msg_row or {}).get("ts")
        if ts:
            return find_execution_around(phone, float(ts))
    return None


# ── Decompor a execução ──────────────────────────────────────────────────────

def tools_used(execution: dict | None) -> list[dict]:
    """Steps ``tool_executed`` como ``[{tool, args, result, agent_key}]``."""
    tools: list[dict] = []
    for step in (execution or {}).get("steps", []):
        if step.get("step_type") == "tool_executed":
            data = step.get("data") or {}
            tools.append({"tool": data.get("tool"), "args": data.get("args"),
                          "result": data.get("result"),
                          "agent_key": step.get("agent_key")})
    return tools


def agent_chain(execution: dict | None) -> list[str]:
    """Chaves de agente do turno, ordenadas e dedupadas (roteador → spoke).

    Lê ``routing_steps`` (JSON ``[{from,to,depth,reason}]``); sem saltos cai em
    ``execution.agent_key`` (turno single-agent)."""
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


def exact_context(execution: dict | None) -> list[dict] | None:
    """Contexto EXATO capturado (steps ``llm_context``, um por hop — 01 F2).

    Devolve ``[{agent_key, model, messages}]`` na ordem dos hops, ou ``None``
    quando o turno não teve captura (flag OFF / execução antiga) — o consumidor
    sinaliza "aproximado" e cai em :func:`approx_context`.
    """
    hops: list[dict] = []
    for step in (execution or {}).get("steps", []):
        if step.get("step_type") == "llm_context":
            data = step.get("data") or {}
            hops.append({"agent_key": step.get("agent_key"),
                         "model": data.get("model"),
                         "messages": data.get("messages") or []})
    return hops or None


def approx_context(agent_key: str | None, *,
                   conversation_id: int | None = None,
                   contact_id: int | None = None,
                   limit: int = 30) -> dict:
    """Aproximação VIVA (deriva no tempo): prompt ATUAL do agente renderizado
    com as ``ai_variables`` atuais + histórico ATUAL da conversa/contato.

    Molde do que o plugin ``melhorias`` sempre fez; usada quando não há
    ``llm_context`` capturado. Nunca lança — campos degradam para vazio."""
    prompt = ""
    if agent_key:
        try:
            from agent import agent_factory
            from db.repositories import agent_repo, variable_repo
            agent = agent_repo.get(agent_key)
            body = (agent or {}).get("prompt") or agent_factory.DEFAULT_SYSTEM_PROMPT
            prompt = agent_factory.render_template(body, variable_repo.as_map())
        except Exception as e:
            logger.debug("approx_context prompt failed for %s: %s", agent_key, e)
    history: list[dict] = []
    try:
        if conversation_id:
            history = message_repo.get_context_by_conversation(int(conversation_id), limit)
        elif contact_id:
            history = message_repo.get_context(int(contact_id), limit)
    except Exception as e:
        logger.debug("approx_context history failed: %s", e)
    return {"prompt": prompt, "history": history}


# ── Bundle único (a API que o gateway consome) ───────────────────────────────

def reconstruct_for_message(msg_row: dict, *, phone: str | None = None,
                            history_limit: int = 30) -> dict:
    """Trace completo de UMA resposta da IA, com flag de fidelidade.

    Retorna::

        {
          "message":     {_id, msg_id, ts, agent_key, execution_id},
          "execution":   dict | None,        # row completa (steps inclusos)
          "linked":      bool,               # True = via execution_id (preciso)
          "agent_chain": [agent_key, ...],   # roteador → spoke
          "tools_used":  [{tool, args, result, agent_key}],
          "exact":       bool,               # True = contexto capturado
          "contexts":    [{agent_key, model, messages}] | None,  # exato, por hop
          "approx":      {prompt, history} | None,  # só quando exact=False
          "total_tokens": int, "total_cost_usd": float,
        }

    ``agent_key`` da mensagem vem de ``messages.agent_key`` (zero core extra).
    """
    msg_row = msg_row or {}
    linked = bool(msg_row.get("execution_id"))
    execution = find_execution_for_message(msg_row, phone=phone)
    contexts = exact_context(execution)
    exact = contexts is not None
    approx = None
    if not exact:
        approx = approx_context(
            msg_row.get("agent_key") or (execution or {}).get("agent_key"),
            conversation_id=msg_row.get("conversation_id"),
            limit=history_limit)
    return {
        "message": {
            "_id": msg_row.get("_id") or msg_row.get("id"),
            "msg_id": msg_row.get("msg_id"),
            "ts": msg_row.get("ts"),
            "agent_key": msg_row.get("agent_key"),
            "execution_id": msg_row.get("execution_id"),
        },
        "execution": execution,
        "linked": linked and execution is not None,
        "agent_chain": agent_chain(execution),
        "tools_used": tools_used(execution),
        "exact": exact,
        "contexts": contexts,
        "approx": approx,
        "total_tokens": (execution or {}).get("total_tokens") or 0,
        "total_cost_usd": (execution or {}).get("total_cost_usd") or 0.0,
    }
