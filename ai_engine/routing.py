"""Within-turn agent routing (plano 06 F3/F5).

When a router agent calls ``transferir_agente`` mid-turn, the target agent should
answer the SAME message instead of waiting for the next one. The handoff is
already persisted (``conversation.active_agent_key``) by the tool; this module
drives the loop that re-runs the conversation under the new agent until a hop
ends without handing off (or the depth cap is hit).

``run_with_routing`` is pure orchestration: it is given two callbacks and knows
nothing about agno, the DB, or the handler — so it is unit-testable in isolation.

- ``resolve_next``: ``() -> str | None`` — the conversation's current active agent
  key (re-resolved after each hop; the tool moved it on a handoff).
- ``run_hop``: ``async (agent_key) -> object | None`` — build + run that agent for
  the same message; returns the hop result, or ``None`` to stop routing (a filter
  aborted the hop).

Returns ``(final_result, routing_steps)`` where ``routing_steps`` is the list of
``{"from", "to", "depth", "reason"}`` handoffs taken (empty when no handoff
happened — the common single-agent case, in which ``run_hop`` is never called
again). ``reason`` (plano 29 A2/A7) is the handoff motivo supplied by the
optional ``get_reason`` callback (``None`` when absent) — the caller reads it
off the previous hop's ``transferir_agente`` call.
"""

from __future__ import annotations

# Total agent runs allowed per message (the first run + up to MAX-1 handoffs).
MAX_ROUTING_DEPTH = 5


async def run_with_routing(*, first_result, first_agent_key, resolve_next, run_hop,
                           max_depth: int = MAX_ROUTING_DEPTH, get_reason=None):
    """Drive within-turn handoffs starting from an already-run first hop.

    The first hop (``first_agent_key`` → ``first_result``) has already executed;
    this only handles continuations. Loops while the active agent keeps changing,
    capped so total runs (first + handoffs) never exceed ``max_depth``.

    ``get_reason``: ``() -> str | None`` — called right before each hop runs;
    returns the motivo of the pending handoff (recorded on the step, plano 29 A7).
    Pure orchestration is preserved: the callback owns the extraction.
    """
    result = first_result
    current = first_agent_key
    steps: list[dict] = []
    seen = {first_agent_key}

    # depth counts hops already run (1 = the first). Stop before exceeding max_depth.
    for depth in range(1, max_depth):
        nxt = resolve_next()
        if not nxt or nxt == current:
            break  # no handoff this turn
        if nxt in seen:
            # Cycle (A→B→A): stop to avoid bouncing within the depth budget.
            break
        reason = None
        if get_reason is not None:
            try:
                reason = get_reason()
            except Exception:
                reason = None
        hop = await run_hop(nxt)
        if hop is None:
            break  # filter aborted the hop — keep the last good result
        steps.append({"from": current, "to": nxt, "depth": depth, "reason": reason})
        result = hop
        current = nxt
        seen.add(nxt)

    return result, steps
