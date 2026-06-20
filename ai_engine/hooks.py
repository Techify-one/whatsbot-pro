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


def _ran_count(executed: list[dict], tool_name: str) -> int:
    return sum(1 for e in executed
              if e.get("tool") == tool_name and not e.get("skipped"))


def check_hooks(hooks_config: dict | None, tool_name: str,
                executed: list[dict]) -> str | None:
    """Return a block reason (string for the LLM) if the call is disallowed, else None.

    Defensive: a malformed hooks_config entry never raises — it just doesn't block.
    """
    if not hooks_config or not isinstance(hooks_config, dict):
        return None
    cfg = hooks_config.get(tool_name)
    if not isinstance(cfg, dict):
        return None

    limit = cfg.get("call_limit")
    if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0:
        if _ran_count(executed, tool_name) >= limit:
            return (f"A ferramenta '{tool_name}' já atingiu o limite de "
                    f"{limit} chamada(s) nesta conversa. Não a chame de novo.")

    prior = cfg.get("requires_prior_call")
    if isinstance(prior, str) and prior:
        if _ran_count(executed, prior) == 0:
            return (f"A ferramenta '{tool_name}' só pode ser usada depois de "
                    f"'{prior}'. Chame '{prior}' primeiro.")

    return None
