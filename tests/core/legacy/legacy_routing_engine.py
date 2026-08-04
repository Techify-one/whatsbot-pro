"""Unit tests for ai_engine.routing.run_with_routing (pure async orchestrator).

    venv/bin/python -m tests.core.legacy.legacy_routing_engine
"""

import asyncio
import sys
from pathlib import Path
from tests.paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

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
    initial active reflects A's handoff. ``reinvokes`` records the ``is_reinvoke``
    flag passed to each hop (plano 29 A3).
    """
    def __init__(self, transfers, abort_on=()):
        self.transfers = transfers
        self.abort_on = set(abort_on)
        self.active = transfers.get("A")  # state after A's (already-run) hop
        self.hops = []
        self.reinvokes = []

    def resolve_next(self):
        return self.active

    async def run_hop(self, key, *, is_reinvoke=False):
        self.hops.append(key)
        self.reinvokes.append(is_reinvoke)
        if key in self.abort_on:
            return None  # filter aborted this hop; active stays put
        self.active = self.transfers.get(key)
        return f"result-{key}"


async def main():
    # No handoff: A stays → run_hop never called, result unchanged
    f = Fake({"A": None})
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("sem handoff: 0 steps", steps == [])
    check("sem handoff: run_hop nunca chamado", f.hops == [])
    check("sem handoff: result inalterado", res == "result-A")
    check("A4: fim normal -> halted=None", halted is None)

    # One handoff: A→B, B stays
    f = Fake({"A": "B", "B": None})
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("1 handoff: result do agente final (B)", res == "result-B")
    check("1 handoff: 1 step A→B",
          steps == [{"from": "A", "to": "B", "depth": 1, "reason": None}])
    check("1 handoff: rodou só B", f.hops == ["B"])

    # Chained: A→B→C
    f = Fake({"A": "B", "B": "C", "C": None})
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("encadeado: result final C", res == "result-C")
    check("encadeado: 2 steps", [s["to"] for s in steps] == ["B", "C"])

    # Depth cap: every agent transfers to a fresh one → capped at MAX-1 extra hops
    chain = {"A": "h1", "h1": "h2", "h2": "h3", "h3": "h4", "h4": "h5", "h5": "h6"}
    f = Fake(chain)
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check(f"depth cap: no máx {routing.MAX_ROUTING_DEPTH-1} hops extras",
          len(steps) == routing.MAX_ROUTING_DEPTH - 1)

    # Plano 29 A3 — revisita CONTROLADA: A→B→A roda os 3 hops no mesmo turno
    # (era barrado pelo `seen`); B fica estável em A → routing termina.
    f = Fake({"A": "B", "B": "A", })
    f.transfers["A"] = "B"  # A (revisitado) transfere de novo → ping-pong até o cap

    class Stabilizing(Fake):
        """A devolve pra B, mas na REVISITA fica (modela o roteador decidindo
        diferente com o motivo threadado — A2)."""
        async def run_hop(self, key, *, is_reinvoke=False):
            self.hops.append(key)
            self.reinvokes.append(is_reinvoke)
            self.active = None if is_reinvoke else self.transfers.get(key)
            return f"result-{key}"

    f = Stabilizing({"A": "B", "B": "A"})
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("A3: revisita A→B→A roda no mesmo turno",
          [s["to"] for s in steps] == ["B", "A"])
    check("A3: hop revisitado recebe is_reinvoke=True", f.reinvokes == [False, True])
    check("A3: result final é o da revisita", res == "result-A")

    # Ping-pong A→B→A→B…: bounded pelo depth cap (não roda infinito)
    f = Fake({"A": "B", "B": "A"})
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check(f"A3: ping-pong é limitado pelo cap ({routing.MAX_ROUTING_DEPTH-1} hops extras)",
          len(steps) == routing.MAX_ROUTING_DEPTH - 1)
    check("A4: cap estourado com handoff pendente -> halted depth_exhausted",
          halted is not None and halted["reason"] == "depth_exhausted")

    # max_depth configurável (A3): cap menor via parâmetro
    f = Fake({"A": "B", "B": "A"})
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop, max_depth=3)
    check("A3: max_depth=3 limita a 2 hops extras", len(steps) == 2)
    check("A4: max_depth=3 com ping-pong pendente -> halted", halted is not None)

    # Cadeia que se resolve EXATAMENTE no último hop do cap → fim normal
    f = Fake({"A": "h1", "h1": "h2", "h2": "h3", "h3": "h4", "h4": None})
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("A4: cadeia resolvida no último hop do cap -> halted=None",
          len(steps) == routing.MAX_ROUTING_DEPTH - 1 and halted is None)

    # Downstream hop aborts (filter on C) → keep last good result (B)
    f = Fake({"A": "B", "B": "C", "C": None}, abort_on={"C"})
    res, steps, halted = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f.resolve_next, run_hop=f.run_hop)
    check("hop C aborta no filtro: result fica no último bom (B)", res == "result-B")
    check("hop C aborta: 1 step (A→B), C não vira step", [s["to"] for s in steps] == ["B"])

    # First continuation hop aborts → 0 steps, hop-1 result kept
    f2 = Fake({"A": "B", "B": None}, abort_on={"B"})
    res2, steps2, halted2 = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f2.resolve_next, run_hop=f2.run_hop)
    check("1º hop de continuação aborta: 0 steps, result-A mantido",
          steps2 == [] and res2 == "result-A")

    # ── Plano 29 A3 (era caracterização A0; comportamento mudou de propósito) ──
    # Revisita indireta A→B→C→A também roda; C→A marca is_reinvoke.
    f3 = Fake({"A": "B", "B": "C", "C": "A", })

    class Stab3(Fake):
        async def run_hop(self, key, *, is_reinvoke=False):
            self.hops.append(key)
            self.reinvokes.append(is_reinvoke)
            self.active = None if is_reinvoke else self.transfers.get(key)
            return f"result-{key}"

    f3 = Stab3({"A": "B", "B": "C", "C": "A"})
    res3, steps3, halted3 = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f3.resolve_next, run_hop=f3.run_hop)
    check("A3: revisita indireta A→B→C→A roda no mesmo turno",
          [s["to"] for s in steps3] == ["B", "C", "A"]
          and f3.reinvokes == [False, False, True])
    check("A3: MAX_ROUTING_DEPTH segue como default do parâmetro (config no caller)",
          routing.MAX_ROUTING_DEPTH == 5)

    # ── Plano 29 A2/A7 — reason nos steps via get_reason ────────────────────
    check("A2: sem get_reason, steps carregam reason=None",
          steps3 and all(s.get("reason") is None for s in steps3))
    f4 = Fake({"A": "B", "B": "C", "C": None})
    _reasons = {"B": "oferta X não existe", "C": None}
    res4, steps4, halted4 = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f4.resolve_next, run_hop=f4.run_hop,
        get_reason=lambda: _reasons.get(f4.active))
    check("A2: get_reason threada o motivo no step correspondente",
          steps4[0].get("reason") == "oferta X não existe"
          and steps4[1].get("reason") is None)
    _boom = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # noqa: E731
    f5 = Fake({"A": "B", "B": None})
    res5, steps5, halted5 = await routing.run_with_routing(
        first_result="result-A", first_agent_key="A",
        resolve_next=f5.resolve_next, run_hop=f5.run_hop,
        get_reason=_boom)
    check("A2: get_reason que quebra não derruba o routing (reason=None)",
          res5 == "result-B" and steps5[0].get("reason") is None)


asyncio.run(main())
print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
