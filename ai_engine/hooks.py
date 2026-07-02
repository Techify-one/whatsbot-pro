"""Declarative tool-call hooks for the config-in-DB engine (plano 06 F6).

``ai_agents.hooks_config`` is a JSON object keyed by tool name:

    {"buscar_pedido": {"call_limit": 2, "requires_prior_call": "autenticar"}}

- ``call_limit``: the tool may run at most N times per message.
- ``requires_prior_call``: the tool may only run after ``<other tool>`` ran in
  this message.

Enforced in the AGNO tool entrypoint BEFORE dispatch, using the per-request
``executed`` list as state — so it resets every message (closure state, P… —
the doc's "estado vive na closure e reseta a cada mensagem"). Pure + unit-tested;
no agno/DB import.
"""

from __future__ import annotations


# Engine-level default guards, applied unless an agent overrides the same tool.
# ``transferir_agente`` must never fire more than once per message: the handoff is
# per-message (it only rebinds ``active_agent_key`` for the NEXT message), so a
# second hop in the same turn just thrashes the pointer, and mutual routing
# allowlists let agents ping-pong (A→B→A) within a single turn. Capping at 1 kills
# the loop by default; an agent that sets its own ``transferir_agente`` hook takes
# full control (the agent entry replaces this default entirely).
_DEFAULT_HOOKS: dict[str, dict] = {"transferir_agente": {"call_limit": 1}}


def _ran_count(executed: list[dict], tool_name: str) -> int:
    return sum(1 for e in executed
              if e.get("tool") == tool_name and not e.get("skipped"))


def check_hooks(hooks_config: dict | None, tool_name: str,
                executed: list[dict]) -> str | None:
    """Return a block reason (string for the LLM) if the call is disallowed, else None.

    Defensive: a malformed hooks_config entry never raises — it just doesn't block.
    Engine defaults (:data:`_DEFAULT_HOOKS`) apply unless the agent overrides the
    same tool name.
    """
    if not isinstance(hooks_config, dict):
        hooks_config = {}
    # Agent config wins over the engine default for the same tool name.
    cfg = {**_DEFAULT_HOOKS, **hooks_config}.get(tool_name)
    if not isinstance(cfg, dict):
        return None

    limit = cfg.get("call_limit")
    if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0:
        if _ran_count(executed, tool_name) >= limit:
            return (f"A ferramenta '{tool_name}' já atingiu o limite de "
                    f"{limit} chamada(s) nesta mensagem. Não a chame de novo agora "
                    f"— o limite é por mensagem e reseta na próxima mensagem do cliente.")

    prior = cfg.get("requires_prior_call")
    if isinstance(prior, str) and prior:
        if _ran_count(executed, prior) == 0:
            return (f"A ferramenta '{tool_name}' só pode ser usada depois de "
                    f"'{prior}'. Chame '{prior}' primeiro.")

    return None
