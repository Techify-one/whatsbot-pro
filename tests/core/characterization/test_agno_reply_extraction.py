"""Golden-master characterization of AGNO reply-extraction (Plano 23 · Fase A2b
— BLOCKING).

Locks the CURRENT behavior (bugs included) of ``agent.agno_engine._extract_reply``
and its interaction with the handler-side reply splitter
(``server.helpers.parse_split_reply``), BEFORE the Wave-2 refactor touches the
engine. CLAUDE.md flags this extraction step as "crítico": with ``split_messages``
ON the model emits a JSON array and AGNO can otherwise concatenate a pre-tool
"chatter" turn with the post-tool final turn — which would corrupt the array.
``_extract_reply`` defends against that by returning the LAST assistant message
that carries NO tool calls (not the preamble), falling back to
``run_output.content`` only when no such message exists.

This is a focused UNIT golden: it feeds synthetic ``run_output`` objects straight
into ``_extract_reply`` (no ``build_test_app``, no DB, no network). Each case
``golden_assert``s the NORMALIZED extracted reply. The split cases additionally
capture how extraction composes with ``parse_split_reply`` (the route/handler
splitter), since CLAUDE.md describes the two as one critical chain.

Mandatory cells (see each test docstring):

  extraction:
    (a) chatter → tool_call → final assistant      → picks the FINAL clean turn
    (b) only one assistant                         → picks it
    multi-clean assistants                         → picks the LAST clean turn
    empty tool_calls list (falsy)                  → treated as clean
  fallback:
    only-tool_call assistants (no clean turn)      → run_output.content
    no messages / empty list                       → run_output.content
    content is None                                → "" (empty string)
    content non-str (structured)                   → stringified
  split interaction:
    (c) JSON-array reply, split ON                 → extract returns raw array
                                                      string; parse_split_reply
                                                      → N parts
    (c) JSON-array reply, split OFF                → extract returns the SAME raw
                                                      array string (extraction is
                                                      split-agnostic); only the
                                                      ROUTE decides whether to call
                                                      parse_split_reply
    plain (non-array) reply                        → parse_split_reply → 1 part

Discipline: ADDITIVE only. We do NOT change app/source and do NOT "fix" behavior.
Likely bugs are CAPTURED and flagged with ``# CHARACTERIZED BUG:`` comments so
Wave 2 sees them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agno.models.message import Message

from agent.agno_engine import _extract_reply
from server.helpers import parse_split_reply
from tests.golden import normalize, golden_assert


# ── synthetic run_output helpers ─────────────────────────────────────────────

def _run_output(messages=None, content=None):
    """A minimal stand-in for AGNO's RunOutput.

    ``_extract_reply`` only ever reads ``run_output.messages`` and
    ``run_output.content`` (and, per-message, ``role`` / ``tool_calls`` /
    ``content``). A SimpleNamespace with those two attributes is faithful and
    avoids constructing a full AGNO RunOutput. Messages are REAL
    ``agno.models.message.Message`` objects so the characterization rides the
    exact attribute shapes the engine sees in production.
    """
    return SimpleNamespace(messages=messages, content=content)


def _tool_call(name: str = "create_reminder") -> list[dict]:
    return [{"id": "call_1", "type": "function",
             "function": {"name": name, "arguments": "{}"}}]


# ── (a) chatter → tool_call → final assistant ────────────────────────────────

def test_extract_picks_final_after_tool_chatter():
    """(a) assistant chatter (with tool_calls) → tool result → final assistant.

    The model emits a pre-tool preamble ("vou criar... um minuto") ALONGSIDE the
    tool call, the tool runs, then the model writes the genuine post-tool answer.
    ``_extract_reply`` must return the FINAL clean assistant turn — never the
    preamble — so split-array output isn't corrupted by concatenation."""
    out = _run_output(messages=[
        Message(role="user", content="preciso criar um lembrete"),
        Message(role="assistant", content="vou criar... só um minuto",
                tool_calls=_tool_call()),
        Message(role="tool", content="lembrete criado com id 7"),
        Message(role="assistant", content="Pronto! Lembrete criado."),
    ])
    golden_assert("agno_reply_a_final_after_chatter",
                  normalize({"reply": _extract_reply(out)}))


# ── (b) only one assistant ───────────────────────────────────────────────────

def test_extract_single_assistant():
    """(b) a single clean assistant turn → returned as-is (stripped)."""
    out = _run_output(messages=[
        Message(role="user", content="oi"),
        Message(role="assistant", content="  Olá, tudo bem?  "),
    ])
    # Leading/trailing whitespace is stripped by _extract_reply.
    golden_assert("agno_reply_b_single_assistant",
                  normalize({"reply": _extract_reply(out)}))


def test_extract_multi_clean_picks_last():
    """multi clean assistants → the LAST one wins (reverse-scan stops at first
    clean turn from the end)."""
    out = _run_output(messages=[
        Message(role="assistant", content="primeira resposta"),
        Message(role="user", content="e depois?"),
        Message(role="assistant", content="segunda resposta final"),
    ])
    golden_assert("agno_reply_multi_clean_last",
                  normalize({"reply": _extract_reply(out)}))


def test_extract_empty_tool_calls_is_clean():
    """An assistant turn whose ``tool_calls`` is an EMPTY list ([] is falsy) is
    treated as a clean final turn — its content is returned."""
    out = _run_output(messages=[
        Message(role="assistant",
                content="resposta com tool_calls vazio", tool_calls=[]),
    ], content="conteúdo agregado ignorado")
    golden_assert("agno_reply_empty_toolcalls_clean",
                  normalize({"reply": _extract_reply(out)}))


# ── fallback to run_output.content ───────────────────────────────────────────

def test_extract_fallback_content_when_only_toolcall_assistant():
    """No clean assistant turn (the only assistant carries tool_calls) → falls
    back to ``run_output.content``, the AGNO-aggregated text."""
    out = _run_output(messages=[
        Message(role="user", content="faça X"),
        Message(role="assistant", content="vou fazer", tool_calls=_tool_call("do_x")),
        Message(role="tool", content="ok"),
    ], content="conteúdo agregado pelo AGNO")
    golden_assert("agno_reply_fallback_only_toolcall",
                  normalize({"reply": _extract_reply(out)}))


def test_extract_fallback_no_messages():
    """``messages`` is None → fall straight to ``run_output.content`` (stripped)."""
    out = _run_output(messages=None, content="   só o conteúdo agregado   ")
    golden_assert("agno_reply_fallback_no_messages",
                  normalize({"reply": _extract_reply(out)}))


def test_extract_fallback_empty_messages_list():
    """``messages`` is an empty list → also falls back to ``run_output.content``."""
    out = _run_output(messages=[], content="conteúdo agregado")
    golden_assert("agno_reply_fallback_empty_list",
                  normalize({"reply": _extract_reply(out)}))


def test_extract_content_none_returns_empty():
    """No clean assistant turn AND ``content`` is None → returns the empty string
    (the engine never crashes the pipeline on a fully-empty run)."""
    out = _run_output(messages=None, content=None)
    golden_assert("agno_reply_content_none_empty",
                  normalize({"reply": _extract_reply(out)}))


def test_extract_content_non_string_stringified():
    """``content`` is non-str (e.g. structured output) → defensively stringified.

    WhatsBot's text agent never uses structured output, but a misconfigured model
    could return a dict; ``_extract_reply`` does ``str(content).strip()`` so the
    pipeline degrades to text instead of raising."""
    out = _run_output(messages=[], content={"answer": "estruturado", "n": 1})
    golden_assert("agno_reply_content_non_string",
                  normalize({"reply": _extract_reply(out)}))


def test_extract_whitespace_only_final_shadows_content_fallback():
    """A whitespace-only FINAL clean assistant turn returns "" — it SHADOWS the
    ``run_output.content`` fallback.

    Reverse-scan reaches the whitespace assistant turn; ``if content:`` is True
    for ``"   "`` (a non-empty, truthy string), so the branch is entered and
    returns ``"   ".strip()`` → ``""``. The non-empty ``run_output.content`` is
    NEVER consulted, so the caller sees an empty reply.

    # CHARACTERIZED BUG: a whitespace-only final assistant turn makes
    # _extract_reply return "" even when run_output.content carries a usable
    # aggregated answer — the truthiness guard checks ``if content:`` (true for
    # whitespace) but then strips to empty, so the content fallback is bypassed.
    # On the live pipeline this surfaces as the bot "going silent" after a turn
    # whose final message was whitespace-only. Wave 2 should guard on
    # ``content.strip()`` (or fall through on empty-after-strip) instead of the
    # raw truthiness of ``content``.
    """
    out = _run_output(messages=[
        Message(role="user", content="oi"),
        Message(role="assistant", content="   "),
    ], content="esta resposta agregada NÃO será usada")
    golden_assert("agno_reply_whitespace_final_shadows_fallback",
                  normalize({"reply": _extract_reply(out)}))


# ── (c) split_messages interaction (extract + parse_split_reply) ──────────────

# The JSON-array reply the model emits when the split_messages prompt is active.
_SPLIT_ARRAY_REPLY = '["primeira parte", "segunda parte", "terceira parte"]'


def test_split_on_extract_then_parse_yields_parts():
    """(c) split ON: the model's final assistant turn IS a JSON array string.

    ``_extract_reply`` returns that raw array string unchanged (it does NOT parse
    — extraction is split-agnostic). The handler/route then calls
    ``parse_split_reply`` which decodes it into N parts. We capture BOTH facets so
    the extract→split chain is locked end to end.

    Critically, ``_extract_reply`` must pick the LAST (array) turn, NOT concatenate
    a preceding chatter turn — otherwise the leading text would break ``json.loads``
    and the whole array would collapse to a single literal message."""
    out = _run_output(messages=[
        Message(role="user", content="quero falar com a IA"),
        Message(role="assistant", content="deixa eu organizar...",
                tool_calls=_tool_call("lookup")),
        Message(role="tool", content="dados encontrados"),
        Message(role="assistant", content=_SPLIT_ARRAY_REPLY),
    ])
    extracted = _extract_reply(out)
    golden_assert("agno_reply_split_on", normalize({
        "extracted_reply": extracted,
        "parse_split_reply": parse_split_reply(extracted),
        "num_parts": len(parse_split_reply(extracted)),
    }))


def test_split_off_extraction_is_identical():
    """(c) split OFF: extraction returns the SAME raw array string.

    ``_extract_reply`` has no knowledge of the split_messages setting — given the
    identical final assistant turn it returns the identical string. The ONLY
    difference between split ON and OFF lives in the ROUTE: with split OFF the
    route sends the raw string as ONE message (it does not call
    ``parse_split_reply``); with split ON it calls ``parse_split_reply`` and sends
    N messages.

    # CHARACTERIZED BEHAVIOR: parse_split_reply itself is split-agnostic — if it
    # IS called on an array string it always decodes to N parts regardless of the
    # split_messages flag. So a model that emits a JSON array while split is OFF
    # would be sent verbatim (brackets and all) by the route, because the route
    # gates the parse_split_reply call on the flag, not the parser. We capture
    # that here: same extraction, and what parse_split_reply WOULD do if invoked.
    """
    out = _run_output(messages=[
        Message(role="user", content="quero falar com a IA"),
        Message(role="assistant", content=_SPLIT_ARRAY_REPLY),
    ])
    extracted = _extract_reply(out)
    golden_assert("agno_reply_split_off", normalize({
        "extracted_reply": extracted,
        # What the route sends with split OFF: the raw string as a single message.
        "route_sends_split_off": [extracted],
        # What parse_split_reply WOULD return if called (proving the parser is
        # split-agnostic — the route's flag is the only gate).
        "parse_split_reply_if_called": parse_split_reply(extracted),
    }))


def test_split_on_plain_reply_single_part():
    """plain (non-array) reply with split ON → ``parse_split_reply`` falls back to
    a single message (the text doesn't start with ``[``, so no JSON decode)."""
    out = _run_output(messages=[
        Message(role="user", content="oi, tudo bem?"),
        Message(role="assistant", content="Claro, posso ajudar!"),
    ])
    extracted = _extract_reply(out)
    golden_assert("agno_reply_split_on_plain", normalize({
        "extracted_reply": extracted,
        "parse_split_reply": parse_split_reply(extracted),
        "num_parts": len(parse_split_reply(extracted)),
    }))
