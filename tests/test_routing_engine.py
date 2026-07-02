"""Unit tests for ai_engine.routing.run_with_routing (pure async orchestrator).

    venv/bin/python tests/test_routing_engine.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_engine import routing

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


class Fake:
    """Models a conversation whose active agent moves on each hop.

    ``transfers[key]`` = the agent ``key`` hands off to when it runs (None = stays).
    ``abort_on`` = agents whose hop is aborted by a filter (run_hop returns None,
    active unchanged). The first hop (agent 'A') is assumed already run, so the
    initial active reflects A's handoff.
    """
    def __init__(self, transfers, abort_on=()):
        self.transfers = transfers
        self.abort_on = set(abort_on)
        self.active = transfers.get("A")  # state after A's (already-run) hop
        self.hops = []

    def resolve_next(self):
        return self.active

    async def run_hop(self, key):
        self.hops.append(key)
        if key in self.abort_on:
            return None  # filter aborted this hop; active stays put
        self.active = self.transfers.get(key)
        return f"result-{key}"


async def main():
    # No handoff: A stays → run_hop never called, result unchanged
    f = Fake({"A": None})
    res, steps = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("sem handoff: 0 steps", steps == [])
    check("sem handoff: run_hop nunca chamado", f.hops == [])
    check("sem handoff: result inalterado", res == "result-A")

    # One handoff: A→B, B stays
    f = Fake({"A": "B", "B": None})
    res, steps = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("1 handoff: result do agente final (B)", res == "result-B")
    check("1 handoff: 1 step A→B", steps == [{"from": "A", "to": "B", "depth": 1}])
    check("1 handoff: rodou só B", f.hops == ["B"])

    # Chained: A→B→C
    f = Fake({"A": "B", "B": "C", "C": None})
    res, steps = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("encadeado: result final C", res == "result-C")
    check("encadeado: 2 steps", [s["to"] for s in steps] == ["B", "C"])

    # Depth cap: every agent transfers to a fresh one → capped at MAX-1 extra hops
    chain = {"A": "h1", "h1": "h2", "h2": "h3", "h3": "h4", "h4": "h5", "h5": "h6"}
    f = Fake(chain)
    res, steps = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check(f"depth cap: no máx {routing.MAX_ROUTING_DEPTH-1} hops extras",
          len(steps) == routing.MAX_ROUTING_DEPTH - 1)

    # Cycle A→B→A: stops when a seen agent reappears
    f = Fake({"A": "B", "B": "A"})
    res, steps = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("ciclo A→B→A: para (não repete A)", [s["to"] for s in steps] == ["B"])

    # Downstream hop aborts (filter on C) → keep last good result (B)
    f = Fake({"A": "B", "B": "C", "C": None}, abort_on={"C"})
    res, steps = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("hop C aborta no filtro: result fica no último bom (B)", res == "result-B")
    check("hop C aborta: 1 step (A→B), C não vira step", [s["to"] for s in steps] == ["B"])

    # First continuation hop aborts → 0 steps, hop-1 result kept
    f2 = Fake({"A": "B", "B": None}, abort_on={"B"})
    res2, steps2 = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f2.resolve_next, run_hop=f2.run_hop)
    check("1º hop de continuação aborta: 0 steps, result-A mantido",
          steps2 == [] and res2 == "result-A")

    # ── Caracterização plano 29 (Fase A0) — baseline ANTES da revisita (A3) ──
    # Documenta o comportamento ATUAL do `seen`: qualquer revisita (direta ou
    # indireta) é barrada. A3 remove isso de propósito e atualiza estes checks.
    f3 = Fake({"A": "B", "B": "C", "C": "A"})
    res3, steps3 = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f3.resolve_next, run_hop=f3.run_hop)
    check("A0: revisita indireta A→B→C→A barrada pelo seen (muda em A3)",
          [s["to"] for s in steps3] == ["B", "C"] and res3 == "result-C")
    check("A0: depth cap é hardcoded MAX_ROUTING_DEPTH=5 (vira config em A3)",
          routing.MAX_ROUTING_DEPTH == 5)
    check("A0: steps não carregam 'reason' (ganham em A7/A2)",
          steps3 and all("reason" not in s for s in steps3))


asyncio.run(main())
print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
