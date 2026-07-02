"""AGNO-based agent engine for WhatsBot.

Replaces the hand-rolled OpenAI tool-calling loop with the AGNO framework
(``agno.agent.Agent``) while preserving every WhatsBot
plugin hook (filters + events), usage accounting and execution tracking.

Design notes
------------
* **Stateless per request.** A fresh Agent is built for each message so
  the tool closures can capture a per-request ``executed`` collector without
  cross-talk between concurrent contacts. AGNO objects are cheap to build.
* **WhatsBot owns history/system prompt.** We do *not* hand AGNO a ``db`` nor
  let it build its own context. The system message and the conversation are
  passed in explicitly (already run through ``filter.system_prompt`` and
  ``filter.llm.messages`` by the handler), and AGNO's context builders are
  disabled (``build_context=False`` etc.).
* **Tools** are the same registry the legacy loop used. Each tool schema is
  wrapped in an ``agno.tools.function.Function`` whose entrypoint re-applies
  ``filter.tool.args`` / ``filter.tool.result`` and emits ``tool.before`` /
  ``tool.after`` — identical semantics to the old manual dispatch.

The engine never talks to plugins directly for the *llm.before/llm.after*
events — the handler owns those, since it also owns the surrounding
try/except and usage snapshot.
"""

import os
import time
import logging
from dataclasses import dataclass, field

from agno.agent import Agent
from agno.models.openai import OpenAILike
from agno.models.message import Message
from agno.tools.function import Function

from config.settings import LLM_API_BASE_URL
from ai_engine.hooks import check_hooks as _hooks_check
from agent.execution import track_step
from plugins.events import (
    apply_filter,
    apply_filter_sync,
    emit_with_filter,
    emit_with_filter_sync,
)

logger = logging.getLogger(__name__)

# AGNO may inject framework objects (the agent/team/session) as kwargs into a
# tool entrypoint when the parameter names match. We capture **kwargs and strip
# these reserved names so only the model-provided arguments reach the executor.
_RESERVED_TOOL_KWARGS = {
    "agent", "team", "session_state", "run_context", "dependencies",
    "run_id", "session_id", "user_id",
}

_DEFAULT_MAX_TOKENS = 1024
# Last-resort model when an AgentSpec carries no model (config-in-DB only path,
# plano 22). The AgentHandler no longer has an in-code ``model`` attribute.
from agent.agent_factory import DEFAULT_MODEL as _FALLBACK_MODEL


@dataclass
class EngineResult:
    """Outcome of one AGNO run, mapped back to WhatsBot's ProcessResult."""
    reply: str = ""
    executed_tools: list[dict] = field(default_factory=list)
    usage: dict | None = None  # {prompt_tokens, completion_tokens, total_tokens}


# --------------------------------------------------------------------------- #
# Model / message helpers
# --------------------------------------------------------------------------- #
def build_model(handler, model_id: str | None = None,
                model_config: dict | None = None) -> OpenAILike:
    """Build an OpenAILike model pointed at the Techify proxy.

    Techify is OpenAI-compatible, so ``OpenAILike`` (id + api_key + base_url) is
    all AGNO needs. ``telemetry`` is disabled on the Agent/Team, not here.

    ``model_config`` (config-in-DB path) may carry ``model``/``temperature``/
    ``top_p``/``max_tokens``. When absent, behaviour matches the legacy default
    (``handler.model`` + ``_DEFAULT_MAX_TOKENS``, provider-default sampling).
    """
    from ai_engine import model_factory
    # Per-agent tuning cascade (model_config > ai_variables{param}_{agent} > global)
    # only matters on the config-in-DB path; legacy path passes model_config=None,
    # so no variable lookup happens and behaviour is unchanged.
    variables = None
    if model_config:
        try:
            from ai_engine import dynamic_registry
            variables = dynamic_registry.variables_map()
        except Exception:
            variables = None
    kwargs = model_factory.build_kwargs(
        model_config,
        fallback_model=model_id or _FALLBACK_MODEL,
        default_max_tokens=_DEFAULT_MAX_TOKENS,
        variables=variables,
    )
    return OpenAILike(api_key=handler.api_key, base_url=LLM_API_BASE_URL, **kwargs)


def split_messages(messages: list[dict]) -> tuple[str, list[Message]]:
    """Split an OpenAI-format message list into (system_prompt, conversation).

    The handler hands us the already-filtered list ``[{system}, *context]``.
    We concatenate every ``system`` entry into the system prompt and convert the
    remaining user/assistant turns into AGNO ``Message`` objects to feed as the
    run input. This keeps both ``filter.system_prompt`` and
    ``filter.llm.messages`` faithful: whatever a plugin left in the list is what
    AGNO sees.
    """
    system_parts: list[str] = []
    convo: list[Message] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str) else str(content))
            continue
        convo.append(Message(role=role or "user", content=content or ""))
    if not convo:
        # AGNO needs at least one input message; mirror legacy behaviour of
        # always carrying the just-saved user turn.
        convo.append(Message(role="user", content=""))
    return "\n".join(system_parts), convo


# --------------------------------------------------------------------------- #
# Tool wrapping (filters + events preserved)
# --------------------------------------------------------------------------- #
def _clean_args(kwargs: dict) -> dict:
    return {k: v for k, v in kwargs.items() if k not in _RESERVED_TOOL_KWARGS}


def _make_async_entrypoint(handler, contact, sender, tool_name, executed, hooks_config=None):
    async def entrypoint(**kwargs):
        args = _clean_args(kwargs)
        filtered = await apply_filter(
            "filter.tool.args",
            {"tool_name": tool_name, "args": args},
            {"phone": sender},
        )
        if filtered is None:
            executed.append({"tool": tool_name, "args": args, "skipped": True})
            return ""
        name = filtered.get("tool_name", tool_name)
        args = filtered.get("args", args)

        block = _hooks_check(hooks_config, name, executed)
        if block is not None:
            executed.append({"tool": name, "args": args, "skipped": True, "blocked": block})
            return block

        _t0 = time.monotonic()
        await emit_with_filter("tool.before", {
            "phone": sender, "tool_name": name, "args": args, "ts": time.time(),
        })
        feedback = handler._dispatch_tool(contact, name, args)
        await emit_with_filter("tool.after", {
            "phone": sender, "tool_name": name, "args": args,
            "result": feedback, "error": None,
            "latency_ms": int((time.monotonic() - _t0) * 1000), "ts": time.time(),
        })
        if feedback is not None:
            fr = await apply_filter(
                "filter.tool.result", feedback,
                {"phone": sender, "tool_name": name},
            )
            feedback = "" if fr is None else fr

        executed.append({"tool": name, "args": args, "result": feedback})
        track_step("tool_executed", {"tool": name, "args": args})
        logger.info("Tool call for %s: %s(%s)", sender, name, args)
        return feedback or "Informações salvas com sucesso."

    return entrypoint


def _make_sync_entrypoint(handler, contact, sender, tool_name, executed, hooks_config=None):
    def entrypoint(**kwargs):
        args = _clean_args(kwargs)
        filtered = apply_filter_sync(
            "filter.tool.args",
            {"tool_name": tool_name, "args": args},
            {"phone": sender},
        )
        if filtered is None:
            executed.append({"tool": tool_name, "args": args, "skipped": True})
            return ""
        name = filtered.get("tool_name", tool_name)
        args = filtered.get("args", args)

        block = _hooks_check(hooks_config, name, executed)
        if block is not None:
            executed.append({"tool": name, "args": args, "skipped": True, "blocked": block})
            return block

        _t0 = time.monotonic()
        emit_with_filter_sync("tool.before", {
            "phone": sender, "tool_name": name, "args": args, "ts": time.time(),
        })
        feedback = handler._dispatch_tool(contact, name, args)
        emit_with_filter_sync("tool.after", {
            "phone": sender, "tool_name": name, "args": args,
            "result": feedback, "error": None,
            "latency_ms": int((time.monotonic() - _t0) * 1000), "ts": time.time(),
        })
        if feedback is not None:
            fr = apply_filter_sync(
                "filter.tool.result", feedback,
                {"phone": sender, "tool_name": name},
            )
            feedback = "" if fr is None else fr

        executed.append({"tool": name, "args": args, "result": feedback})
        track_step("tool_executed", {"tool": name, "args": args})
        logger.info("Tool call for %s: %s(%s)", sender, name, args)
        return feedback or "Informações salvas com sucesso."

    return entrypoint


def build_functions(handler, contact, sender, active_tools, executed, *, is_async,
                    hooks_config=None):
    """Wrap each active tool schema into an AGNO Function.

    ``active_tools`` is the post-``filter.llm.tools`` list of OpenAI tool
    schemas. ``executed`` is the per-request sink that collects what actually
    ran (used to build ProcessResult and detect ``save_contact_info``).
    ``hooks_config`` (config-in-DB) gates calls declaratively (call_limit /
    requires_prior_call) using ``executed`` as per-message state.
    """
    make = _make_async_entrypoint if is_async else _make_sync_entrypoint
    functions: dict[str, Function] = {}
    for schema in active_tools:
        fn = schema.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        functions[name] = Function(
            name=name,
            description=fn.get("description", ""),
            parameters=params,
            entrypoint=make(handler, contact, sender, name, executed, hooks_config),
            skip_entrypoint_processing=True,
        )
    return functions


# --------------------------------------------------------------------------- #
# Agent construction
# --------------------------------------------------------------------------- #
# Context builders for the Agent. WhatsBot owns the system prompt and history,
# so AGNO must not prepend/resolve anything of its own.
_CONTEXT_OFF = dict(
    add_history_to_context=False,
    resolve_in_context=False,
    add_name_to_context=False,
    add_datetime_to_context=False,
    add_location_to_context=False,
    markdown=False,
    store_history_messages=False,
    telemetry=False,
    retries=0,
)
# ``build_context`` configures the single Agent.
_AGENT_CONTEXT_OFF = dict(_CONTEXT_OFF, build_context=False)

# Safety backstop for tool-call loops. AGNO's ``Agent.tool_call_limit`` defaults
# to ``None`` (unbounded): a tool that keeps asking to be called again loops until
# the model gives up — which it may not (QA Teste 3b: 12/12 iterations without
# stopping, ~US$0.025 in a single message). We cap the number of tool calls per
# agent run. On overflow AGNO does NOT raise: it feeds the model a "limit reached"
# message for the extra calls and the run ends gracefully. Override via env
# ``WHATSBOT_TOOL_CALL_LIMIT`` (set to ``0`` — or any non-positive — to disable).
DEFAULT_TOOL_CALL_LIMIT = 25


def _resolve_tool_call_limit() -> int | None:
    """Per-run tool-call cap: env override, else :data:`DEFAULT_TOOL_CALL_LIMIT`.

    Returns ``None`` (no cap) only when explicitly disabled with a non-positive
    value; a malformed value falls back to the default (fail safe, not open)."""
    raw = os.environ.get("WHATSBOT_TOOL_CALL_LIMIT")
    if raw is None or raw.strip() == "":
        return DEFAULT_TOOL_CALL_LIMIT
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_TOOL_CALL_LIMIT
    return n if n > 0 else None


def _build_single_agent(handler, system_prompt, functions, model_config=None):
    return Agent(
        model=build_model(handler, model_config=model_config),
        system_message=system_prompt,
        tools=list(functions.values()) or None,
        tool_call_limit=_resolve_tool_call_limit(),
        **_AGENT_CONTEXT_OFF,
    )


def build_runner(handler, system_prompt, functions, model_config=None):
    """Return a ready-to-run single Agent for one message."""
    return _build_single_agent(handler, system_prompt, functions, model_config=model_config)


# --------------------------------------------------------------------------- #
# Run result extraction
# --------------------------------------------------------------------------- #
def _extract_usage(run_output) -> dict | None:
    metrics = getattr(run_output, "metrics", None)
    if not metrics:
        return None
    pt = getattr(metrics, "input_tokens", 0) or 0
    ct = getattr(metrics, "output_tokens", 0) or 0
    tt = getattr(metrics, "total_tokens", 0) or (pt + ct)
    if not (pt or ct or tt):
        return None
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}


def _merge_usage(base: dict | None, extra: dict | None) -> dict | None:
    """Add the forced follow-up's tokens to the primary run's usage."""
    if not extra:
        return base
    if not base:
        return extra
    return {k: (base.get(k, 0) or 0) + (extra.get(k, 0) or 0)
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _extract_reply(run_output) -> str:
    """Return the agent's final user-facing reply.

    Prefer the *last* assistant message that carries no tool calls. AGNO's
    aggregated ``run_output.content`` can concatenate a pre-tool "chatter" turn
    with the post-tool final turn when the model emits text alongside a tool
    call — for WhatsApp we only want the final message, and with split_messages
    on (JSON array output) the concatenation would otherwise corrupt the JSON.
    """
    messages = getattr(run_output, "messages", None) or []
    for m in reversed(messages):
        if getattr(m, "role", None) != "assistant":
            continue
        if getattr(m, "tool_calls", None):
            continue
        content = getattr(m, "content", None)
        if content:
            return content.strip() if isinstance(content, str) else str(content).strip()

    content = getattr(run_output, "content", None)
    if content is None:
        return ""
    if not isinstance(content, str):
        # Structured output is not used by WhatsBot's text agent; stringify
        # defensively so a misconfigured model never crashes the pipeline.
        content = str(content)
    return content.strip()


def _msg_text(m) -> str:
    content = getattr(m, "content", None)
    if not content:
        return ""
    return content.strip() if isinstance(content, str) else str(content).strip()


def _clean_final_reply(run_output) -> str | None:
    """Return the post-tool final assistant text, or ``None`` if there isn't one.

    Walks the messages backwards: the first assistant turn that still carries
    ``tool_calls`` is the *pre-tool preamble* ("vou criar... um minuto..."), so we
    stop and report ``None`` — there is no genuine final answer after the tool
    ran. A reasoning model that returns an empty ``content`` on the follow-up turn
    lands here, which is exactly the case ``run_sync``/``run_async`` repair with a
    forced tools-less follow-up instead of surfacing the dangling preamble.
    """
    messages = getattr(run_output, "messages", None) or []
    for m in reversed(messages):
        if getattr(m, "role", None) != "assistant":
            continue
        if getattr(m, "tool_calls", None):
            return None
        text = _msg_text(m)
        if text:
            return text
    return None


def _followup_input(run_output) -> list[Message]:
    """Rebuild the conversation (sans system) to re-ask for a final answer.

    Reuses AGNO's own ``Message`` objects from the completed run — including the
    assistant turn that carries the ``tool_calls`` and the ``tool`` result rows —
    so the model sees the tool outcome and produces the confirmation. The trailing
    empty assistant turn (the failed follow-up) is dropped so the model is the one
    completing the conversation.
    """
    src = getattr(run_output, "messages", None) or []
    convo: list[Message] = []
    for m in src:
        role = getattr(m, "role", None)
        if role == "system":
            continue
        if role == "assistant" and not getattr(m, "tool_calls", None) and not _msg_text(m):
            continue  # drop the empty/failed final assistant turn
        convo.append(m)
    return convo


def _needs_forced_followup(run_output, executed) -> bool:
    """A tool ran but no genuine post-tool answer came back."""
    return bool(executed) and _clean_final_reply(run_output) is None


def _build_followup_agent(handler, system_prompt, model_config):
    """A tools-less agent that only writes the final reply (no further tool loop)."""
    return Agent(
        model=build_model(handler, model_config=model_config),
        system_message=system_prompt,
        tools=None,
        **_AGENT_CONTEXT_OFF,
    )


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
async def run_async(handler, contact, sender, messages, active_tools,
                    model_config=None) -> EngineResult:
    """Run the AGNO agent for one message (cancellable async path)."""
    system_prompt, convo = split_messages(messages)
    executed: list[dict] = []
    hooks_config = (model_config or {}).get("_hooks_config")
    functions = build_functions(handler, contact, sender, active_tools, executed,
                                is_async=True, hooks_config=hooks_config)
    runner = build_runner(handler, system_prompt, functions, model_config=model_config)
    model_id = (model_config or {}).get("model") or _FALLBACK_MODEL

    track_step("llm_request", {
        "model": model_id,
        "engine": "agno",
        "context_messages": len(convo),
        "tools": list(functions.keys()),
    })
    run_output = await runner.arun(input=convo)

    reply = _extract_reply(run_output)
    usage = _extract_usage(run_output)

    # The model may answer with a pre-tool preamble ("vou criar... um minuto...")
    # alongside the tool call and then return an *empty* follow-up turn (common
    # with reasoning models). In that case ``_extract_reply`` would surface the
    # dangling preamble as the final reply — the client sees the bot "stop
    # replying" after the action ran. Force one tools-less follow-up so the model
    # actually writes the confirmation based on the tool result.
    if _needs_forced_followup(run_output, executed):
        try:
            track_step("llm_request", {"model": model_id, "engine": "agno", "type": "followup"})
            fu_agent = _build_followup_agent(handler, system_prompt, model_config)
            fu_output = await fu_agent.arun(input=_followup_input(run_output))
            fu_reply = _extract_reply(fu_output)
            fu_usage = _extract_usage(fu_output)
            track_step("llm_response", {"model": model_id, "engine": "agno",
                                        "type": "followup", "has_reply": bool(fu_reply)})
            if fu_reply:
                reply = fu_reply
                usage = _merge_usage(usage, fu_usage)
        except Exception:
            logger.exception("Forced follow-up after tool call failed for %s", sender)
    track_step("llm_response", {
        "model": model_id, "engine": "agno",
        "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
        "completion_tokens": (usage or {}).get("completion_tokens", 0),
        "has_tool_calls": bool(executed),
    })
    return EngineResult(reply=reply, executed_tools=executed, usage=usage)


def run_sync(handler, contact, sender, messages, active_tools,
             model_config=None) -> EngineResult:
    """Run the AGNO agent for one message (synchronous path)."""
    system_prompt, convo = split_messages(messages)
    executed: list[dict] = []
    hooks_config = (model_config or {}).get("_hooks_config")
    functions = build_functions(handler, contact, sender, active_tools, executed,
                                is_async=False, hooks_config=hooks_config)
    runner = build_runner(handler, system_prompt, functions, model_config=model_config)
    model_id = (model_config or {}).get("model") or _FALLBACK_MODEL

    track_step("llm_request", {
        "model": model_id,
        "engine": "agno",
        "context_messages": len(convo),
        "tools": list(functions.keys()),
    })
    run_output = runner.run(input=convo)

    reply = _extract_reply(run_output)
    usage = _extract_usage(run_output)

    # See run_async: repair the "dangling preamble" case where the post-tool
    # follow-up came back empty by forcing one tools-less follow-up.
    if _needs_forced_followup(run_output, executed):
        try:
            track_step("llm_request", {"model": model_id, "engine": "agno", "type": "followup"})
            fu_agent = _build_followup_agent(handler, system_prompt, model_config)
            fu_output = fu_agent.run(input=_followup_input(run_output))
            fu_reply = _extract_reply(fu_output)
            fu_usage = _extract_usage(fu_output)
            track_step("llm_response", {"model": model_id, "engine": "agno",
                                        "type": "followup", "has_reply": bool(fu_reply)})
            if fu_reply:
                reply = fu_reply
                usage = _merge_usage(usage, fu_usage)
        except Exception:
            logger.exception("Forced follow-up after tool call failed for %s", sender)
    track_step("llm_response", {
        "model": model_id, "engine": "agno",
        "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
        "completion_tokens": (usage or {}).get("completion_tokens", 0),
        "has_tool_calls": bool(executed),
    })
    return EngineResult(reply=reply, executed_tools=executed, usage=usage)
