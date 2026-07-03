"""Declarative tool-call hooks for the config-in-DB engine (plano 06 F6 · plano 29 A1).

``ai_agents.hooks_config`` is a JSON object keyed by tool name:

    {"buscar_pedido": {"call_limit": 2, "requires_prior_call": "autenticar"}}

- ``call_limit``: the tool may run at most N times per message. Tools without
  their own limit fall back to ``default_call_limit`` (config
  ``ai_tool_call_limit_per_tool``; ``0``/absent = unlimited).
- ``requires_prior_call``: the tool may only run after ``<other tool>`` ran
  **successfully** in this message. Accepts a single name or a list of names
  (all must have succeeded). A prior whose last real run returned a failure
  marker (see :data:`_FAILURE_MARKERS`) does NOT satisfy the dependency —
  ported from the nexus ``hooks_interpreter`` (success-aware).

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

# The tool a blocked agent should reach for when it cannot proceed — block
# messages name it so the model has a way OUT instead of a dead end (nexus:
# "ex: solicitar_roteamento"; here the equivalent handoff tool).
ESCAPE_TOOL = "transferir_agente"

# Substrings (lowercase) that mark a tool result as "not found" / failed — used
# to decide whether a call satisfies ``requires_prior_call``. Ported verbatim
# from the nexus ``hooks_interpreter._FAILURE_MARKERS``. Kept short on purpose:
# substring matching is fuzzy, and a legit result that merely *mentions* an
# error must not be flagged (risco §6 do plano 29).
_FAILURE_MARKERS = (
    "[erro]",
    "nao existe nenhuma",
    "não existe nenhuma",
    "nao encontrad",
    "não encontrad",
)


def _result_ok(result) -> bool:
    """True when a tool result does not look like a 'not found' / error.

    Besides the nexus markers, a result that *starts with* "erro" is a failure —
    WhatsBot core tools use the ``"Erro: …"`` / ``"Erro ao …"`` prefix contract.
    Prefix-only (not substring) so a legit result mentioning an error mid-text
    is not false-flagged.
    """
    low = str(result).strip().lower()
    if low.startswith("erro"):
        return False
    return not any(marker in low for marker in _FAILURE_MARKERS)


def _ran_count(executed: list[dict], tool_name: str) -> int:
    return sum(1 for e in executed
              if e.get("tool") == tool_name and not e.get("skipped"))


def _prior_satisfied(executed: list[dict], prior: str) -> bool:
    """The prior tool's LAST real (non-skipped) run exists and succeeded.

    Last-call-wins, like the nexus interpreter: a tool that succeeded and later
    failed no longer satisfies the dependency.
    """
    last = None
    for e in executed:
        if e.get("tool") == prior and not e.get("skipped"):
            last = e
    if last is None:
        return False
    return _result_ok(last.get("result"))


def _escape_hint(tool_name: str, escape_tool: str) -> str:
    """Guidance out of the dead end — omitted when the blocked tool IS the escape."""
    if not escape_tool or tool_name == escape_tool:
        return ""
    return (f" Se não for possível concluir o atendimento sem ela, use "
            f"'{escape_tool}' para transferir a conversa.")


def check_hooks(hooks_config: dict | None, tool_name: str,
                executed: list[dict], *,
                default_call_limit: int | None = None,
                escape_tool: str = ESCAPE_TOOL) -> str | None:
    """Return a block reason (string for the LLM) if the call is disallowed, else None.

    Defensive: a malformed hooks_config entry never raises — it just doesn't block.
    Engine defaults (:data:`_DEFAULT_HOOKS`) apply unless the agent overrides the
    same tool name. ``default_call_limit`` (config ``ai_tool_call_limit_per_tool``)
    caps tools that have no explicit ``call_limit`` of their own; ``0``/``None``
    = unlimited.
    """
    if not isinstance(hooks_config, dict):
        hooks_config = {}
    # Agent config wins over the engine default for the same tool name.
    cfg = {**_DEFAULT_HOOKS, **hooks_config}.get(tool_name)
    if not isinstance(cfg, dict):
        cfg = {}

    limit = cfg.get("call_limit")
    if not (isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0):
        limit = None
    if limit is None and isinstance(default_call_limit, int) \
            and not isinstance(default_call_limit, bool) and default_call_limit > 0:
        limit = default_call_limit
    if limit is not None and _ran_count(executed, tool_name) >= limit:
        return (f"A ferramenta '{tool_name}' já atingiu o limite de "
                f"{limit} chamada(s) nesta mensagem. Não a chame de novo agora "
                f"— o limite é por mensagem e reseta na próxima mensagem do cliente. "
                f"Use as informações já obtidas para responder."
                + _escape_hint(tool_name, escape_tool))

    prior = cfg.get("requires_prior_call")
    priors: list[str] = []
    if isinstance(prior, str) and prior:
        priors = [prior]
    elif isinstance(prior, list):
        priors = [p for p in prior if isinstance(p, str) and p]
    missing = [p for p in priors if not _prior_satisfied(executed, p)]
    if missing:
        quoted = ", ".join(f"'{p}'" for p in missing)
        return (f"A ferramenta '{tool_name}' só pode ser usada depois de "
                f"{quoted} ter sido chamada COM SUCESSO nesta mensagem. "
                f"Chame primeiro e verifique o resultado."
                + _escape_hint(tool_name, escape_tool))

    return None
