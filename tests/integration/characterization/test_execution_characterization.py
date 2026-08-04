"""Golden-master characterization of the EXECUTIONS / EXECUTION_STEPS subsystem
(Plano 23 · Fase A2b — BLOCKING).

This locks the CURRENT behavior of execution tracking around ONE webhook turn,
BEFORE Wave 2 extracts the messaging/ingest/event-action services that must
thread ``execution_id`` through every hop. The silent-regression risk here is a
refactor that creates 0 executions (lost tracking) or 2 (double-create), or that
stops the writer from populating ``agent_key`` / ``total_tokens`` /
``total_cost_usd`` / the ordered step rows.

Where the rows come from (so the golden's faithfulness is auditable):

* ``server/routes/webhook.py::_run_one_cycle`` opens the execution
  (``astart_execution``) and ALWAYS closes it (``aend_execution`` in finally),
  tracking ``webhook_received`` → ``batch_accumulated`` up front (BEFORE the agent
  resolves, so those two steps carry ``agent_key=None``), then ``channel_send`` +
  ``response_sent`` on the send leg.
* ``agent/handler.py::aprocess_message`` calls ``set_execution_agent_key`` (→
  ``executions.agent_key``) and ``_record_usage_tokens`` (→ ``add_execution_usage``
  → ``executions.total_tokens`` / ``total_cost_usd``).
* ``agent/agno_engine.py::run_async`` emits ``llm_request`` / ``llm_response``
  (and the per-tool ``tool_executed``) steps via ``track_step``.

So the ONLY honest cut for an AI-replied turn is to stub the LLM at the
``agno_engine.run_async`` seam (NOT the handler seam ``fake_agent_reply`` uses):
the handler's own orchestration (agent_key + usage writes) and the engine's
step-emission must keep running for the writer path to be characterized. Our
fake ``run_async`` therefore mirrors the real engine: it emits the same
``track_step`` calls and returns an ``EngineResult`` with deterministic usage.

ADDITIVE only: we never change app/source and never "fix" behavior. Likely bugs
are CAPTURED with ``# CHARACTERIZED BUG:`` comments + listed for Wave 2.

Distinct phone numbers per cell (the suite shares one process DB).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from tests.golden import normalize, golden_assert


# ── Shared helpers ───────────────────────────────────────────────────────────

# DEFAULT_MODEL is config-driven (a model bump would churn every golden), so we
# collapse the model name inside step ``data`` to a stable placeholder. The
# IDENTITY of the model isn't what A2b guards — the step ORDER, the step TYPES,
# and the execution-row writer fields are.
def _scrub_model(obj):
    if isinstance(obj, dict):
        return {k: ("<MODEL>" if k == "model" and v else _scrub_model(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_model(v) for v in obj]
    return obj


def _drain_orchestrator(built, channel_id: str, phone: str) -> None:
    """Flush the background batch orchestrator on the TestClient's ASGI loop —
    same pattern as the A2 webhook characterization."""
    async def _drain():
        for _ in range(6):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_drain)


def _executions_for(phone: str) -> list[dict]:
    """All executions for ``phone`` (newest-first via the repo) with their full
    ordered steps, projected to the load-bearing, deterministic columns.

    Excludes ``id`` / ``started_at`` / ``completed_at`` / ``step_count`` ordering
    noise; keeps the writer-populated fields the Wave-2 services must preserve.
    """
    from db.repositories import execution_repo

    rows = execution_repo.list_executions(limit=100, phone=phone)
    out: list[dict] = []
    # Oldest-first so the projection is order-stable regardless of insert id.
    for row in sorted(rows, key=lambda r: r["id"]):
        full = execution_repo.get_by_id(row["id"])
        steps = []
        for s in full.get("steps", []):
            steps.append({
                "step_type": s.get("step_type"),
                "status": s.get("status"),
                "agent_key": s.get("agent_key"),
                "data": _scrub_model(s.get("data")),
            })
        out.append({
            "phone": full.get("phone"),
            "trigger_type": full.get("trigger_type"),
            "status": full.get("status"),
            "error": full.get("error"),
            "agent_key": full.get("agent_key"),
            "total_tokens": full.get("total_tokens"),
            "total_cost_usd": full.get("total_cost_usd"),
            "routing_steps": full.get("routing_steps"),
            "step_count": len(steps),
            "step_types": [s["step_type"] for s in steps],
            "steps": steps,
        })
    return out


def _seed_default_agent() -> None:
    """Ensure the config-in-DB default agent exists (the no-op test lifespan skips
    the boot seed). Idempotent; also invalidates the registry cache so a prior
    cached ``None`` default doesn't make ``build_for_contact`` raise
    AgentResolutionError (which would silently swallow the AI turn)."""
    from agent import agent_factory
    from ai_engine import dynamic_registry

    agent_factory.seed_default_agent()
    dynamic_registry.invalidate()


def _make_fake_run_async(*, executed_tools, usage, with_tool_step=False):
    """Build a fake ``agno_engine.run_async`` that mirrors the real engine's
    ``track_step`` emissions so the execution_steps golden is faithful to the
    production writer path (only the network LLM call is replaced)."""
    from agent.execution import track_step
    from agent.agno_engine import EngineResult

    async def _fake(handler, contact, sender, messages, active_tools,
                    model_config=None):
        model_id = (model_config or {}).get("model") or "fake/model"
        track_step("llm_request", {
            "model": model_id, "engine": "agno",
            "context_messages": len(messages),
            "tools": [t["function"]["name"] for t in (active_tools or [])
                      if isinstance(t, dict) and t.get("function")],
        })
        if with_tool_step:
            for tc in executed_tools:
                track_step("tool_executed",
                           {"tool": tc.get("tool"), "args": tc.get("args")})
        track_step("llm_response", {
            "model": model_id, "engine": "agno",
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "has_tool_calls": bool(executed_tools),
        })
        return EngineResult(reply="resposta da IA",
                            executed_tools=list(executed_tools),
                            usage=dict(usage))

    return _fake


# ── Cell 1: AI turn, NO tools — the primary invariant ────────────────────────

def test_execution_ai_turn_no_tools(build_app):
    """ONE AI-replied turn (AI gate ON, LLM stubbed at the engine seam):

    * EXACTLY 1 executions row for the turn (not 0, not 2).
    * status=completed, error=None.
    * agent_key populated by the writer (= ``default``, the config-in-DB agent).
    * total_tokens populated by ``add_execution_usage`` (= 15 from our stub usage).
    * total_cost_usd populated by the writer — 0.0 here because the test handler
      has no ``pricing_fn`` (cost is a function of pricing, not of token count);
      cell 4 proves the writer DOES accumulate cost when pricing is wired.
    * Ordered steps: webhook_received → batch_accumulated → llm_request →
      llm_response → channel_send → response_sent.
    * The two pre-agent steps carry agent_key=None; every step after the agent
      resolves carries agent_key=default.
    """
    _seed_default_agent()
    phone = "5511975500001"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "split_messages": False, "split_message_delay": 0,
        "response_delay_min": 0, "response_delay_max": 0,
    })
    fake = _make_fake_run_async(
        executed_tools=[],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    try:
        from agent import agno_engine
        with patch.object(agno_engine, "run_async", side_effect=fake):
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": "exec_ai_1",
                    "body": "oi, quero falar com a IA", "from_name": "Cliente"}})
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
    finally:
        built.settings.set("auto_reply", False)

    execs = _executions_for(phone)
    # THE invariant: exactly one execution per turn.
    assert len(execs) == 1, f"expected exactly 1 execution, got {len(execs)}"
    assert execs[0]["agent_key"] == "default"
    assert execs[0]["total_tokens"] == 15
    assert execs[0]["step_types"] == [
        "webhook_received", "batch_accumulated", "llm_request",
        "llm_response", "channel_send", "response_sent",
    ]
    golden_assert("exec_ai_turn_no_tools", normalize(execs))


# ── Cell 2: AI turn WITH a tool call — tool_executed step ────────────────────

def test_execution_ai_turn_with_tool(build_app):
    """ONE AI-replied turn that runs a tool: still EXACTLY 1 execution, and the
    ordered steps gain a ``tool_executed`` row (the engine's per-tool step)
    between llm_request and llm_response. total_tokens reflects the writer
    accumulation (= 28 from our stub usage)."""
    _seed_default_agent()
    phone = "5511975500002"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "split_messages": False, "split_message_delay": 0,
        "response_delay_min": 0, "response_delay_max": 0,
    })
    fake = _make_fake_run_async(
        executed_tools=[{"tool": "save_contact_info",
                         "args": {"name": "João"}, "result": "ok"}],
        usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        with_tool_step=True)
    try:
        from agent import agno_engine
        with patch.object(agno_engine, "run_async", side_effect=fake):
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": "exec_tool_1",
                    "body": "meu nome é João", "from_name": "Cliente"}})
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
    finally:
        built.settings.set("auto_reply", False)

    execs = _executions_for(phone)
    assert len(execs) == 1, f"expected exactly 1 execution, got {len(execs)}"
    assert execs[0]["total_tokens"] == 28
    assert "tool_executed" in execs[0]["step_types"]
    assert execs[0]["step_types"] == [
        "webhook_received", "batch_accumulated", "llm_request",
        "tool_executed", "llm_response", "channel_send", "response_sent",
    ]
    golden_assert("exec_ai_turn_with_tool", normalize(execs))


# ── Cell 3: AI gate OFF — execution STILL created, no LLM/agent steps ────────

def test_execution_gate_off_still_one(build_app):
    """A turn with AI gated OFF (global ``auto_reply=False``): the orchestrator
    ALWAYS opens+closes an execution, so there is STILL EXACTLY 1 execution row —
    but it has NO agent/LLM steps, agent_key=None, total_tokens=0.

    This guards the invariant "1 execution per inbound turn, regardless of whether
    the AI runs". A Wave-2 refactor that only created an execution when the AI
    replies would silently break execution tracking for the no-reply majority
    (operator-handled / gated chats)."""
    phone = "5511975500003"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    r = built.client.post("/api/webhook/gowa/default", json={
        "event": "message", "payload": {
            "from": f"{phone}@s.whatsapp.net", "id": "exec_off_1",
            "body": "oi", "from_name": "Cliente"}})
    assert r.status_code == 200, r.text
    _drain_orchestrator(built, "default", phone)

    execs = _executions_for(phone)
    assert len(execs) == 1, f"expected exactly 1 execution, got {len(execs)}"
    assert execs[0]["agent_key"] is None
    assert execs[0]["total_tokens"] == 0
    assert execs[0]["step_types"] == ["webhook_received", "batch_accumulated"]
    golden_assert("exec_gate_off", normalize(execs))


# ── Cell 4: writer accumulates cost when pricing is wired ────────────────────

def test_execution_cost_accumulation(build_app):
    """With a ``pricing_fn`` on the handler, ``add_execution_usage`` accumulates a
    NON-zero ``total_cost_usd`` onto the SAME single execution — proving the
    writer threads cost (not just tokens) onto the execution row. Still 1
    execution per turn.

    pricing = 0.001 prompt + 0.002 completion per token; usage = 10/5 →
    cost = 10*0.001 + 5*0.002 = 0.02. (Characterizes the writer arithmetic, which
    Wave-2 services must not drop.)"""
    _seed_default_agent()
    phone = "5511975500004"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "split_messages": False, "split_message_delay": 0,
        "response_delay_min": 0, "response_delay_max": 0,
    })
    built.agent_handler.pricing_fn = lambda model: (0.001, 0.002)
    fake = _make_fake_run_async(
        executed_tools=[],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    try:
        from agent import agno_engine
        with patch.object(agno_engine, "run_async", side_effect=fake):
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": "exec_cost_1",
                    "body": "quanto custa?", "from_name": "Cliente"}})
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
    finally:
        built.agent_handler.pricing_fn = None
        built.settings.set("auto_reply", False)

    execs = _executions_for(phone)
    assert len(execs) == 1, f"expected exactly 1 execution, got {len(execs)}"
    assert execs[0]["total_tokens"] == 15
    assert abs(execs[0]["total_cost_usd"] - 0.02) < 1e-9, execs[0]["total_cost_usd"]
    golden_assert("exec_cost_accumulation", normalize(execs))


# ── Cell 5: structurally-unreachable matrix entry (kept visible) ─────────────

def test_execution_zero_executions_unreachable():
    """Matrix completeness marker: "an inbound turn that produces ZERO executions".

    Structurally unreachable on the GOWA path this subsystem characterizes:
    ``_run_one_cycle`` opens an execution UNCONDITIONALLY at the top of every
    batch cycle (before any gate), so a delivered inbound turn cannot yield 0
    executions. The only 0-execution outcomes are (a) a message dropped before
    ingest (handled in the A2 classify/discard cells, which is a different
    subsystem) or (b) the orchestrator task never running — neither is an
    execution-tracking branch. Recorded as SKIP so the matrix is visibly complete
    rather than silently missing."""
    pytest.skip("unreachable: _run_one_cycle always opens exactly one execution "
                "per batch cycle; 0-execution requires a pre-ingest drop (A2 cells)")
