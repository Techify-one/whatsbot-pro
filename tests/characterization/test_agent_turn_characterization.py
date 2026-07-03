"""Golden-master characterization of the AgentHandler turn orchestration around a
tool call (Plano 23 · Fase A2b — BLOCKING; blocks Wave-2 block B5).

This locks the CURRENT behavior (bugs included) of ``AgentHandler`` driving ONE
agent turn that uses a tool, BEFORE Wave 2 extracts the messaging/agent-turn
services. The handler is the orchestrator: it owns system-prompt assembly,
history, the agent-spec resolution, the ``llm.before``/``llm.after`` events, the
tool dispatch hooks (``tool.before``/``tool.after`` + ``_dispatch_tool``), usage
accounting (``RunMetrics`` → ``usage`` table + execution), and the assistant-row
save. It DELEGATES only the reasoning/tool-calling loop to ``agno_engine``.

Seam choice (load-bearing): we do NOT use ``fake_agent_reply`` — that stubs the
handler's own ``aprocess_message`` and would bypass the entire orchestration we
want to characterize. Instead we stub DEEPER, at ``agno_engine.build_runner`` (the
same boundary the engine itself uses to obtain the AGNO ``Agent``). The fake
runner's ``arun``/``run`` invokes the REAL wrapped tool entrypoints that
``agno_engine.build_functions`` produced (so ``filter.tool.args`` /
``filter.tool.result`` / ``tool.before`` / ``tool.after`` / ``_dispatch_tool`` all
fire exactly as in production) and returns a deterministic fake ``RunOutput``
(messages + metrics). The OpenAI/Techify client and ``build_model`` are therefore
never reached — no network. This is the cut the task asks for: REAL handler
orchestration with a fixed model output.

Captured facets (all normalized via ``golden.normalize``):
  (a) ``result``  — the ProcessResult the handler returns (reply / tool_calls /
                    contact_info), the public turn outcome.
  (b) ``messages``— the persisted rows for the contact (user + assistant), proving
                    the user msg + the final reply were saved in order.
  (c) ``usage``   — the usage row(s) written for the turn (call_type/model/tokens/
                    cost), proving accounting ran off ``RunMetrics``.
  (d) ``events``  — the bus event SET (fan-out order is non-deterministic) AND the
                    ordered SEQUENCE where deterministic (llm.before → tool.before
                    → tool.after → llm.after within one serial turn).

Asserts beyond the goldens:
  * the tool is dispatched EXACTLY ONCE with the expected args;
  * a usage row is recorded for the turn (tokens; cost via a deterministic
    pricing_fn in one cell);
  * ``conversation.ai_takeover`` dedupe = EXACTLY ONE across two turns in the
    SAME conversation (this lives in the WEBHOOK layer, so that cell drives the
    real ``/api/webhook/gowa/default`` route twice).

Discipline: ADDITIVE only. We never change app/source and never "fix" behavior;
likely bugs are CAPTURED and flagged with ``# CHARACTERIZED BUG:``.

Distinct phone numbers per test (the suite shares one process DB). Subsystem
prefix ``agent_turn_`` on every golden + this file.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from tests.fakes import FakeGowaClient, fake_agent_reply
from tests.characterization.golden import (
    EventRecorder, normalize, golden_assert, sort_events,
)


# ── Fake AGNO run plumbing ────────────────────────────────────────────────────
#
# The engine's contract with AGNO (see agent/agno_engine.py):
#   * ``build_runner`` returns an object with ``arun(input=...)`` / ``run(input=...)``
#   * the returned ``run_output`` exposes ``.messages`` (list of Message-likes with
#     ``.role`` / ``.content`` / ``.tool_calls``) and ``.metrics`` (RunMetrics with
#     ``.input_tokens`` / ``.output_tokens`` / ``.total_tokens``).
# We reproduce JUST that surface, and have the fake runner call the REAL wrapped
# tool entrypoints so the handler's tool dispatch path runs unchanged.

class _FakeMsg:
    """A minimal AGNO ``Message`` stand-in for ``_extract_reply``/``_clean_final_reply``."""
    def __init__(self, role, content, tool_calls=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls


class _FakeMetrics:
    def __init__(self, input_tokens, output_tokens, total_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens


class _FakeRunOutput:
    def __init__(self, messages, metrics):
        self.messages = messages
        self.metrics = metrics
        self.content = None  # force _extract_reply to use the messages list


class _FakeRunner:
    """A stand-in AGNO Agent that invokes the real wrapped tool entrypoints then
    returns a deterministic run_output (one tool call + a final reply).

    ``functions`` is the dict ``agno_engine.build_functions`` produced — its values
    are the REAL wrapped ``Function`` objects, so calling ``.entrypoint(**args)``
    drives ``_dispatch_tool`` + tool filters + tool.before/after exactly as live.
    """
    def __init__(self, functions, *, tool_name, tool_args,
                 final_reply, in_tokens, out_tokens):
        self._functions = functions
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._final_reply = final_reply
        self._in = in_tokens
        self._out = out_tokens

    def _build_output(self, tool_result):
        # Mirror a realistic AGNO message trace: a preamble assistant turn that
        # carries the tool_call, the tool result row, then the final assistant
        # reply with NO tool_calls (so _clean_final_reply returns it and no forced
        # follow-up is triggered).
        msgs = [
            _FakeMsg("assistant", "", tool_calls=[{"name": self._tool_name}]),
            _FakeMsg("tool", tool_result or ""),
            _FakeMsg("assistant", self._final_reply, tool_calls=None),
        ]
        return _FakeRunOutput(
            msgs, _FakeMetrics(self._in, self._out, self._in + self._out))

    async def arun(self, input=None):
        fn = self._functions.get(self._tool_name)
        tool_result = await fn.entrypoint(**self._tool_args) if fn else None
        return self._build_output(tool_result)

    def run(self, input=None):
        fn = self._functions.get(self._tool_name)
        # sync entrypoint path
        tool_result = fn.entrypoint(**self._tool_args) if fn else None
        return self._build_output(tool_result)


class _patch_agno_turn:
    """Context manager: stub ``agno_engine.build_runner`` so the next handler turn
    issues ONE tool call (``tool_name(tool_args)``) and a fixed final reply, with
    fixed token metrics. Records the build args for inspection.
    """
    def __init__(self, *, tool_name="save_contact_info",
                 tool_args=None, final_reply="pronto, anotei seus dados!",
                 in_tokens=120, out_tokens=30):
        self.tool_name = tool_name
        self.tool_args = tool_args if tool_args is not None else {}
        self.final_reply = final_reply
        self.in_tokens = in_tokens
        self.out_tokens = out_tokens
        self._patch = None

    def __enter__(self):
        from agent import agno_engine

        def _fake_build_runner(handler, system_prompt, functions, model_config=None):
            return _FakeRunner(
                functions, tool_name=self.tool_name, tool_args=self.tool_args,
                final_reply=self.final_reply,
                in_tokens=self.in_tokens, out_tokens=self.out_tokens)

        self._patch = patch.object(agno_engine, "build_runner", _fake_build_runner)
        self._patch.__enter__()
        return self

    def __exit__(self, *exc):
        self._patch.__exit__(*exc)


# ── Shared helpers ────────────────────────────────────────────────────────────

# Lifecycle/system-notice chatter unrelated to the turn pipeline — kept out of the
# persisted-message projection so the golden characterizes the TURN, not the
# system-notice config toggles.
_NOISE_ROLES = {"conversation_event"}


def _project_messages(phone: str) -> list[dict]:
    from db.repositories import contact_repo, message_repo

    contact = contact_repo.get_by_phone(phone)
    if contact is None:
        return []
    rows = message_repo.get_all(contact["id"])
    out = []
    for r in rows:
        if r.get("role") in _NOISE_ROLES:
            continue
        out.append({
            "role": r.get("role"),
            "content": r.get("content"),
            "media_type": r.get("media_type"),
            "media_path": r.get("media_path"),
            "status": r.get("status"),
            "msg_id": r.get("msg_id"),
        })
    return out


def _project_usage(phone: str) -> list[dict]:
    from db.repositories import contact_repo, usage_repo

    contact = contact_repo.get_by_phone(phone)
    if contact is None:
        return []
    rows = usage_repo.detail(contact["id"])
    return [{
        "call_type": r.get("call_type"),
        "model": r.get("model"),
        "prompt_tokens": r.get("prompt_tokens"),
        "completion_tokens": r.get("completion_tokens"),
        "total_tokens": r.get("total_tokens"),
        "cost_usd": r.get("cost_usd"),
    } for r in rows]


def _result_dict(result) -> dict:
    return {
        "reply": result.reply,
        "tool_calls": result.tool_calls,
        "contact_info": result.contact_info,
    }


def _run_turn(built, sender, text, *, channel_id="default"):
    """Drive ONE real handler turn on the TestClient's ASGI loop, returning the
    ProcessResult. ``aprocess_message`` is the async seam the live webhook awaits."""
    async def _go():
        return await built.agent_handler.aprocess_message(
            sender, text, channel_id=channel_id)
    return built.client.portal.call(_go)


def _capture(phone, result, recorder) -> dict:
    return normalize({
        "result": _result_dict(result),
        "messages": _project_messages(phone),
        "usage": _project_usage(phone),
        "events": {
            "set": sort_events(list(recorder.name_set)),
            "sequence": recorder.names,
        },
    })


# ── A2b.1 — the turn: tool call → dispatch → reply → usage ────────────────────

def test_agent_turn_tool_call(build_app):
    """ONE handler turn that issues a ``save_contact_info`` tool call then a final
    reply. Characterizes the full orchestration:

      aprocess_message
        → build_for_contact (default agent, model deepseek/deepseek-v4-pro)
        → llm.before
        → agno run: wrapped tool entrypoint → tool.before → _dispatch_tool
          (save_contact_info persists name/email on the contact) → tool.after
        → _extract_reply (final assistant turn) + _record_usage_tokens
        → save assistant row
        → llm.after

    Asserts: tool dispatched ONCE with the expected args; usage row recorded with
    the turn's tokens; ProcessResult.contact_info populated (save_contact_info
    was among the executed tools)."""
    phone = "5511976000001"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    args = {"name": "Carlos Souza", "email": "carlos@exemplo.com"}
    with EventRecorder() as rec, _patch_agno_turn(
            tool_name="save_contact_info", tool_args=args,
            final_reply="pronto, Carlos, anotei seus dados!"):
        result = _run_turn(built, phone, "meu nome é Carlos e meu email é carlos@exemplo.com")
        rec.drain()

    # Tool dispatched exactly once with the expected args.
    assert len(result.tool_calls) == 1, result.tool_calls
    assert result.tool_calls[0]["tool"] == "save_contact_info"
    assert result.tool_calls[0]["args"] == args
    # save_contact_info ran → contact_info snapshot present on the result.
    assert result.contact_info is not None
    assert result.contact_info.get("name") == "Carlos Souza"
    # Usage row recorded for the turn (tokens; cost 0.0 with no pricing_fn).
    usage = _project_usage(phone)
    assert len(usage) == 1 and usage[0]["total_tokens"] == 150
    # Tool + llm lifecycle events all fired.
    assert {"llm.before", "tool.before", "tool.after", "llm.after"} <= rec.name_set
    golden_assert("agent_turn_tool_call", _capture(phone, result, rec))


# ── A2b.2 — usage cost computed via pricing_fn ────────────────────────────────

def test_agent_turn_usage_cost(build_app):
    """Same turn, but with a deterministic ``pricing_fn`` wired on the handler so a
    NON-zero ``cost_usd`` is recorded. Characterizes that the AGNO ``RunMetrics``
    path (``_record_usage_tokens``) multiplies prompt/completion tokens by the
    per-model price. ``pricing_fn`` returns (prompt_price, completion_price)."""
    phone = "5511976000002"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    # 120 prompt * 0.001 + 30 completion * 0.002 = 0.12 + 0.06 = 0.18
    built.agent_handler.pricing_fn = lambda model: (0.001, 0.002)
    try:
        with EventRecorder() as rec, _patch_agno_turn(
                tool_name="save_contact_info",
                tool_args={"name": "Dora Lima"},
                final_reply="anotado!", in_tokens=120, out_tokens=30):
            result = _run_turn(built, phone, "sou a Dora")
            rec.drain()
        usage = _project_usage(phone)
        assert len(usage) == 1
        assert usage[0]["prompt_tokens"] == 120
        assert usage[0]["completion_tokens"] == 30
        assert abs(usage[0]["cost_usd"] - 0.18) < 1e-9
        golden_assert("agent_turn_usage_cost", _capture(phone, result, rec))
    finally:
        built.agent_handler.pricing_fn = None


# ── A2b.3 — tool args/result are run through the plugin filters ───────────────

def test_agent_turn_tool_filter_args(build_app):
    """The wrapped tool entrypoint re-applies ``filter.tool.args`` BEFORE dispatch
    and ``filter.tool.result`` AFTER. Characterizes that a plugin filter mutating
    the args reaches ``_dispatch_tool`` (the saved contact reflects the MUTATED
    args, and ``executed_tools`` records the mutated args, not the model's)."""
    phone = "5511976000003"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    from plugins import events as bus

    def _mutate_args(ctx, value):
        # value == {"tool_name": ..., "args": {...}}
        if value.get("tool_name") == "save_contact_info":
            value = dict(value)
            value["args"] = {**value.get("args", {}), "company": "FiltradoCo"}
        return value

    bus.register_filter("_charz_agent_turn_args", "filter.tool.args",
                        _mutate_args, priority=10)
    try:
        with EventRecorder() as rec, _patch_agno_turn(
                tool_name="save_contact_info",
                tool_args={"name": "Eva Reis"},
                final_reply="dados atualizados!"):
            result = _run_turn(built, phone, "me chamo Eva")
            rec.drain()
        # The filter injected company → the dispatched args (recorded) carry it.
        assert result.tool_calls[0]["args"].get("company") == "FiltradoCo"
        assert result.contact_info.get("company") == "FiltradoCo"
        golden_assert("agent_turn_tool_filter_args", _capture(phone, result, rec))
    finally:
        bucket = bus._filters.get("filter.tool.args") or []
        bus._filters["filter.tool.args"] = [
            t for t in bucket if t[1] != "_charz_agent_turn_args"]


# ── A2b.4 — ai_takeover dedupe (webhook layer, two turns / one conversation) ──

def _drain_orchestrator(built, channel_id: str, phone: str) -> None:
    """Flush the background batch orchestrator on the TestClient's ASGI loop
    (same approach as the A2 webhook characterization)."""
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


def _text_payload(phone: str, msg_id: str, body: str) -> dict:
    return {"event": "message", "payload": {
        "from": f"{phone}@s.whatsapp.net", "id": msg_id,
        "body": body, "from_name": "Cliente"}}


def test_agent_turn_ai_takeover_dedupe(build_app):
    """``conversation.ai_takeover`` fires EXACTLY ONCE across TWO AI replies in the
    same conversation (plano 12 §3.3 dedupe; promoted to a bus event in Fase C0).

    This dedupe lives in the WEBHOOK layer (``_maybe_emit_ai_takeover``), not the
    handler, so this cell drives the real ``/api/webhook/gowa/default`` route twice
    with a stubbed reply. After turn 1 the card exists → turn 2's ``has_event``
    short-circuits, so the bus sees a single ``conversation.ai_takeover`` total."""
    phone = "5511976000004"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "split_messages": False, "split_message_delay": 0,
        "response_delay_min": 0, "response_delay_max": 0,
    })
    try:
        with EventRecorder() as rec:
            with fake_agent_reply("primeira resposta da IA"):
                r1 = built.client.post(
                    "/api/webhook/gowa/default",
                    json=_text_payload(phone, "tk_1", "oi, primeira"))
                assert r1.status_code == 200, r1.text
                _drain_orchestrator(built, "default", phone)
            with fake_agent_reply("segunda resposta da IA"):
                r2 = built.client.post(
                    "/api/webhook/gowa/default",
                    json=_text_payload(phone, "tk_2", "oi de novo, segunda"))
                assert r2.status_code == 200, r2.text
                _drain_orchestrator(built, "default", phone)
            rec.drain()

        assert "conversation.ai_takeover" in rec.name_set
        # The dedupe guarantee: exactly one takeover across both turns.
        assert rec.names.count("conversation.ai_takeover") == 1, rec.names
        # Both replies were sent (two turns really happened).
        assert built.gowa_client.sent_texts == [
            "primeira resposta da IA", "segunda resposta da IA"]
        golden_assert("agent_turn_ai_takeover_dedupe", normalize({
            "sent": [{"kind": k, **a} for k, a in built.gowa_client.sent],
            "events": {
                "set": sort_events(list(rec.name_set)),
                "ai_takeover_count": rec.names.count("conversation.ai_takeover"),
            },
        }))
    finally:
        built.settings.set("auto_reply", False)


# ── A2b.5 — multi-agent routing hop (handoff within one turn) ─────────────────

def test_agent_turn_routing_hop(build_app):
    """Within-turn multi-agent handoff: the first agent calls ``transferir_agente``
    (a real CORE tool) to hand off to a second agent, which then answers the SAME
    message. Characterizes ``_continue_routing`` running a second AGNO hop and the
    final reply coming from the handed-off agent.

    This IS reachable: ``aprocess_message`` always calls ``_continue_routing`` after
    the first run, which re-resolves the agent via ``build_for_contact`` and, if it
    changed (because ``transferir_agente`` persisted a new ``active_agent_key`` on
    the conversation), runs the new agent's hop. We seed a ``vendas`` target agent,
    have the first turn's stubbed run call ``transferir_agente(agente='vendas')``,
    and verify the second hop produced the final reply + a SECOND usage row."""
    phone = "5511976000005"

    # Plano 30 F3 (D3): ``transferir_agente`` nasce DESLIGADA em instalação nova,
    # e o assunto deste teste é a MÁQUINA de routing (que pressupõe a tool ligada
    # pelo operador) — liga os dois gates ANTES do boot do app pra tool entrar no
    # registry e no schema como antes.
    from agent import ai_builtin_tools
    from db.repositories import tool_override_repo, tool_repo
    ai_builtin_tools.seed_builtin_tools()
    tool_repo.set_enabled("transferir_agente", True)
    tool_override_repo.ensure("transferir_agente", None, default_enabled=True)
    tool_override_repo.upsert("transferir_agente", enabled=True)

    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    # Seed a handoff target distinct from the default agent.
    from db.repositories import agent_repo
    agent_repo.save("vendas", display_name="Vendas", prompt_key="default",
                    model_config={"model": "test/vendas-model"},
                    tool_names=None, enabled=True)
    try:
        from ai_engine import dynamic_registry
        dynamic_registry.invalidate()
    except Exception:
        pass

    from agent import agno_engine

    call_log = {"hops": 0}
    original_build_runner = agno_engine.build_runner

    def _routing_build_runner(handler, system_prompt, functions, model_config=None):
        call_log["hops"] += 1
        hop = call_log["hops"]
        if hop == 1:
            # First hop: hand off to 'vendas' via the real transferir_agente tool.
            return _FakeRunner(
                functions, tool_name="transferir_agente",
                tool_args={"agente": "vendas"},
                final_reply="vou te transferir...",
                in_tokens=100, out_tokens=20)
        # Second hop (the handed-off 'vendas' agent) answers the same message.
        return _FakeRunner(
            functions, tool_name="transferir_agente",  # tool not re-invoked meaningfully
            tool_args={"agente": "vendas"},
            final_reply="aqui é o Vendas, posso ajudar com seu pedido!",
            in_tokens=80, out_tokens=25)

    try:
        with EventRecorder() as rec, patch.object(
                agno_engine, "build_runner", _routing_build_runner):
            result = _run_turn(built, phone, "quero comprar")
            rec.drain()

        # The handoff MUST run a second hop: transferir_agente persists a new
        # active_agent_key and _continue_routing re-resolves + runs it. A single
        # hop here means routing regressed — fail loudly instead of silently
        # skipping (which would let the regression pass as green).
        assert call_log["hops"] >= 2, (
            "routing hop not reached: transferir_agente did not change the "
            f"resolved agent (hops={call_log['hops']}) — single-agent path only")

        # Two hops ran → final reply is the handed-off agent's, and TWO usage rows
        # were recorded (one per hop).
        assert result.reply == "aqui é o Vendas, posso ajudar com seu pedido!"
        usage = _project_usage(phone)
        assert len(usage) == 2, usage
        golden_assert("agent_turn_routing_hop", _capture(phone, result, rec))
    finally:
        # Don't leak the seeded 'vendas' agent / handoff binding into sibling
        # tests on the SHARED process DB: clear this conversation's agent binding,
        # remove the agent, and invalidate the config cache.
        try:
            from db.repositories import contact_repo, conversation_repo
            c = contact_repo.get_by_phone(phone)
            if c:
                conv = conversation_repo.get_open_for_contact(c["id"])
                if conv:
                    conversation_repo.set_agent(conv["id"], None)
            agent_repo.delete("vendas")
        except Exception:
            pass
        try:
            from ai_engine import dynamic_registry
            dynamic_registry.invalidate()
        except Exception:
            pass
