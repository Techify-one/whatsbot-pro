"""Golden-master characterization of the two CURRENT SYNC-LLM callers that block
the Plano 23 · Fase A2b C-1 sync-caller migration (BLOCKING).

Two callers reach the SYNCHRONOUS LLM path today and both must keep their exact
observable behavior when Wave-2 step B5 migrates them to ``async``:

  1. ``server/routes/sandbox.py`` ``/api/sandbox/send`` → ``_sandbox_reply`` →
     ``agent_handler.process_message(phone, "", save_user_message=False,
     save_response=False)`` (sync). The endpoint splits/persists/broadcasts the
     reply and returns ``{reply, replies, phone}``.

  2. ``agent/handler.py`` ``generate_improvement(phone, target, feedback)`` →
     a DIRECT, non-agentic ``self._get_client().chat.completions.create(...)``
     (sync OpenAI client). Exposed via ``POST /api/contacts/{phone}/improve``,
     which saves the analysis as a panel-only ``system`` message.

We characterize each with the LLM stubbed at a CLEAN, deterministic seam:
  * sandbox → patch ``process_message`` to return a canned ``ProcessResult``
    (the suite's sync seam, same as ``fake_agent_reply`` / test_endpoints.py);
  * generate_improvement → patch the handler's ``_get_client`` to return a fake
    OpenAI client whose ``chat.completions.create`` returns a canned response
    object (``choices[0].message.content`` + ``usage``). No network, no AGNO.

Each golden snapshots the NORMALIZED outcome:
  (a) ``response`` — the endpoint JSON body (or the ``generate_improvement``
      return string), and
  (b) ``messages`` — the rows persisted for that contact (role / content /
      media_type / status; ts/msg_id normalized away).

These goldens prove behavior is preserved across the B5 sync→async migration.

Discipline: ADDITIVE only. We do NOT change app/source and do NOT "fix" behavior;
likely bugs are CAPTURED with ``# CHARACTERIZED BUG:`` comments.

Distinct phone numbers per test (the suite shares one process DB). All goldens +
this file are prefixed ``sandbox_improve`` to avoid sibling collisions.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.characterization.golden import normalize, golden_assert


# ── Shared helpers ───────────────────────────────────────────────────────────

# Panel-only lifecycle chatter that depends on global system-notice toggles, not
# on the caller under test — kept out of the message projection so the golden
# characterizes the SANDBOX / IMPROVE behavior, not the notice config.
_NOISE_ROLES = {"conversation_event"}


def _project_messages(phone: str) -> list[dict]:
    """Persisted message rows for ``phone``, reduced to the load-bearing columns
    and stripped of panel-only event chatter, ordered by ts."""
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
            "status": r.get("status"),
            "msg_id": r.get("msg_id"),
        })
    return out


def _stub_process(reply: str, *, tool_calls=None, contact_info=None):
    """Return an ``aprocess_message`` replacement yielding a canned ProcessResult.

    Fase B5/C-1: the sandbox migrated from the SYNC ``process_message`` to the
    ASYNC ``aprocess_message`` (the sync entry point was removed). This stub is an
    ASYNC coroutine function so it patches the new seam directly. Records each
    invocation in ``.calls`` so a test can assert the seam was (or was not)
    reached; lets us vary tool_calls/contact_info per cell."""
    from agent.handler import ProcessResult

    result = ProcessResult(reply=reply, tool_calls=list(tool_calls or []),
                           contact_info=contact_info)
    calls: list[tuple] = []

    async def _fake(sender: str, msg_text: str = "", *args, **kwargs):
        calls.append((sender, msg_text, kwargs))
        return result

    _fake.calls = calls  # type: ignore[attr-defined]
    return _fake


def _fake_llm_response(content: str, *, prompt_tokens=120, completion_tokens=80):
    """Build an OpenAI-compatible response object for the direct sync client.

    ``generate_improvement`` reads ``response.choices[0].message.content`` and
    ``_record_usage`` reads ``response.usage.{prompt,completion,total}_tokens``."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class _FakeOpenAIClient:
    """Stand-in for ``OpenAI`` whose ``chat.completions.create`` returns a canned
    response (or raises). Records the kwargs of every create() call."""

    def __init__(self, *, response=None, raise_exc: Exception | None = None):
        self.create_calls: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.create_calls.append(kwargs)
                if raise_exc is not None:
                    raise raise_exc
                return response

        self.chat = SimpleNamespace(completions=_Completions())


# ═══════════════════════════════════════════════════════════════════════════
# SANDBOX  ·  /api/sandbox/send → process_message (SYNC seam)
# ═══════════════════════════════════════════════════════════════════════════

def _sandbox_case(build_app, *, golden, phone, reply, split=True,
                  tool_calls=None, contact_info=None, message="Oi sandbox"):
    """Drive one ``/api/sandbox/send`` with ``aprocess_message`` stubbed and
    snapshot the endpoint response + persisted messages.

    Fase B5/C-1: stubs the ASYNC ``aprocess_message`` (the sandbox's new seam);
    the golden facet ``process_message_called`` is preserved as the name of the
    "agent seam reached" flag so the BEFORE→AFTER goldens stay byte-identical —
    the endpoint output (reply/replies/messages) is unchanged by the sync→async
    migration."""
    built = build_app(["gowa"], settings_overrides={
        "split_messages": split,
    })
    fake = _stub_process(reply, tool_calls=tool_calls, contact_info=contact_info)
    with patch.object(built.agent_handler, "aprocess_message", new=fake):
        r = built.client.post("/api/sandbox/send",
                              json={"phone": phone, "message": message})
    assert r.status_code == 200, r.text
    body = r.json()
    golden_assert(golden, normalize({
        "status_code": r.status_code,
        "response": body,
        "messages": _project_messages(phone),
        "process_message_called": bool(fake.calls),  # agent seam reached
    }))
    return built, r


def test_sandbox_send_plain(build_app):
    """Plain (non-array) reply, split ON → parse_split falls back to ONE part.

    Persisted: a ``user`` row (the inbound) + one ``assistant`` row (status=sent).
    Response: ``{reply, replies:[part], phone}``. This is the common happy path
    and the exact contract B5 must preserve when this caller goes async."""
    _sandbox_case(build_app, golden="sandbox_improve_send_plain",
                  phone="5511975000001", reply="claro, posso ajudar!", split=True)


def test_sandbox_send_split_on(build_app):
    """JSON-array reply + split ON → parse_split_reply yields N parts, each saved
    as its own ``assistant`` row and joined with ``\\n`` in ``reply``."""
    _sandbox_case(build_app, golden="sandbox_improve_send_split_on",
                  phone="5511975000002",
                  reply='["primeira parte", "segunda parte", "terceira parte"]',
                  split=True)


def test_sandbox_send_split_off(build_app):
    """JSON-array reply + split OFF → the raw array string is sent/saved as ONE
    ``assistant`` row (no parsing)."""
    _sandbox_case(build_app, golden="sandbox_improve_send_split_off",
                  phone="5511975000003",
                  reply='["primeira parte", "segunda parte"]',
                  split=False)


def test_sandbox_send_system_notice(build_app):
    """Reply starting with ``[WhatsBot]`` → shown verbatim, saved as a
    ``system_notice`` row, and ``replies`` comes back EMPTY (the notice is not a
    deliverable part). Characterizes the verbatim-notice branch of _sandbox_reply."""
    _sandbox_case(build_app, golden="sandbox_improve_send_system_notice",
                  phone="5511975000004",
                  reply="[WhatsBot] API key não configurada.", split=True)


def test_sandbox_send_empty_reply(build_app):
    """Empty reply → ``_sandbox_reply`` returns ``[]`` early; only the inbound
    ``user`` row persists, response has empty ``reply``/``replies``."""
    _sandbox_case(build_app, golden="sandbox_improve_send_empty_reply",
                  phone="5511975000005", reply="   ", split=True)


def test_sandbox_send_with_tool_calls(build_app):
    """tool_calls present in the ProcessResult → ``broadcast_tool_calls`` runs and
    persists a ``tool_call`` card BEFORE the assistant reply rows.

    The card content is ``"🔧 <tool>\\n<key>: <value>\\n→ <result>"``. This locks
    the sandbox's tool-card side effect that B5 must keep across the async move."""
    _sandbox_case(
        build_app, golden="sandbox_improve_send_with_tool_calls",
        phone="5511975000006", reply="anotei seus dados!", split=True,
        tool_calls=[{
            "tool": "save_contact_info",
            "args": {"name": "Maria", "email": "maria@example.com"},
            "result": "Dados salvos.",
        }],
        contact_info={"name": "Maria", "email": "maria@example.com",
                      "observations": []},
    )


def test_sandbox_send_no_phone(build_app):
    """Validation: empty phone → 400 ``_err`` (process_message never reached)."""
    built = build_app(["gowa"])
    r = built.client.post("/api/sandbox/send", json={"phone": "", "message": "Oi"})
    golden_assert("sandbox_improve_send_no_phone", normalize({
        "status_code": r.status_code, "response": r.json()}))
    assert r.status_code == 400


def test_sandbox_send_no_message(build_app):
    """Validation: empty message → 400 ``_err``."""
    built = build_app(["gowa"])
    r = built.client.post("/api/sandbox/send",
                          json={"phone": "5511975000007", "message": ""})
    golden_assert("sandbox_improve_send_no_message", normalize({
        "status_code": r.status_code, "response": r.json()}))
    assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# GENERATE_IMPROVEMENT  ·  handler.generate_improvement (DIRECT sync client)
# ═══════════════════════════════════════════════════════════════════════════

_IMPROVE_ANALYSIS = (
    "**Diagnóstico**\nA IA respondeu fora do escopo.\n\n"
    "**Recomendações**\n- Ajustar o prompt para focar no produto."
)


def test_improve_happy_unit(build_app):
    """``generate_improvement`` happy path at the UNIT seam: ``_get_client``
    stubbed → ``chat.completions.create`` returns a canned analysis; the method
    returns the trimmed content. We also assert the create() call carried the
    expected static params (temperature/max_tokens) so B5's async rewrite keeps
    the same request shape.

    Golden captures the returned analysis string."""
    phone = "5511975100001"
    handler = build_app(["gowa"]).agent_handler
    # Seed a tiny history so the prompt assembly has something to mark.
    contact = handler._get_contact(phone)
    contact.add_message("user", "qual o preço?")
    contact.add_message("assistant", "não sei dizer")

    fake_client = _FakeOpenAIClient(response=_fake_llm_response(_IMPROVE_ANALYSIS))
    target = {"content": "não sei dizer", "ts": 0}
    with patch.object(handler, "_get_client", return_value=fake_client):
        out = handler.generate_improvement(phone, target, "saiu errado")

    # Request shape the async migration must preserve.
    assert len(fake_client.create_calls) == 1
    call = fake_client.create_calls[0]
    assert call.get("temperature") == 0.4
    assert call.get("max_tokens") == 1600
    assert [m["role"] for m in call["messages"]] == ["system", "user"]

    golden_assert("sandbox_improve_generate_happy",
                  normalize({"analysis": out}))


def test_improve_no_api_key(build_app):
    """No API key → early return ``[WhatsBot] API key não configurada.`` BEFORE
    any client/network. The sync client is never constructed."""
    phone = "5511975100002"
    handler = build_app(["gowa"]).agent_handler
    saved_key = handler.api_key
    handler.api_key = ""
    try:
        # Guard: if the early-return regressed, this would explode (no real net).
        with patch.object(handler, "_get_client",
                          side_effect=AssertionError("client must not be used")):
            out = handler.generate_improvement(
                phone, {"content": "x", "ts": 0}, "")
    finally:
        handler.api_key = saved_key
    golden_assert("sandbox_improve_generate_no_api_key",
                  normalize({"analysis": out}))


def test_improve_llm_error(build_app):
    """LLM raises → ``generate_improvement`` swallows it and returns the
    ``[WhatsBot] Falha ao gerar a análise de melhoria: <e>`` fallback string.

    The error text is the stringified exception; we use a fixed message so the
    golden is stable."""
    phone = "5511975100003"
    handler = build_app(["gowa"]).agent_handler
    contact = handler._get_contact(phone)
    contact.add_message("user", "oi")
    contact.add_message("assistant", "resposta ruim")

    fake_client = _FakeOpenAIClient(raise_exc=RuntimeError("boom-determinístico"))
    with patch.object(handler, "_get_client", return_value=fake_client):
        out = handler.generate_improvement(
            phone, {"content": "resposta ruim", "ts": 0}, "feedback")
    golden_assert("sandbox_improve_generate_llm_error",
                  normalize({"analysis": out}))


def test_improve_endpoint_happy(build_app):
    """END-TO-END through ``POST /api/contacts/{phone}/improve``: the analysis is
    saved as a panel-only ``system`` message ``"🔧 Análise de melhoria\\n\\n<a>"``
    and returned in ``data``. Default-allow auth (single-password suite).

    Golden captures the response + the persisted ``system`` row."""
    phone = "5511975100004"
    built = build_app(["gowa"])
    handler = built.agent_handler
    contact = handler._get_contact(phone)
    contact.add_message("user", "preciso de ajuda")
    contact.add_message("assistant", "resposta marcada")

    fake_client = _FakeOpenAIClient(response=_fake_llm_response(_IMPROVE_ANALYSIS))
    with patch.object(handler, "_get_client", return_value=fake_client):
        r = built.client.post(
            f"/api/contacts/{phone}/improve",
            json={"message": {"content": "resposta marcada", "ts": 0},
                  "feedback": "saiu errado"})
    assert r.status_code == 200, r.text
    # The saved-note row is echoed in ``data`` with autoincrement ``_id`` /
    # ``conversation_id`` (shared-DB ordinals — not behavior). Project to the
    # load-bearing fields so the golden is stable across suite order.
    data = r.json().get("data") or {}
    note = {k: data.get(k) for k in ("role", "content", "status", "msg_id", "ts")}
    golden_assert("sandbox_improve_endpoint_happy", normalize({
        "status_code": r.status_code,
        "ok": r.json().get("ok"),
        "note": note,
        "messages": _project_messages(phone),
    }))


def test_improve_endpoint_invalid_message(build_app):
    """Endpoint validation: empty target content → 400 ``_err`` BEFORE the LLM.
    ``generate_improvement`` is never called."""
    phone = "5511975100005"
    built = build_app(["gowa"])
    with patch.object(built.agent_handler, "generate_improvement",
                      side_effect=AssertionError("must not reach LLM")):
        r = built.client.post(
            f"/api/contacts/{phone}/improve",
            json={"message": {"content": "   ", "ts": 0}, "feedback": ""})
    golden_assert("sandbox_improve_endpoint_invalid_message", normalize({
        "status_code": r.status_code, "response": r.json()}))
    assert r.status_code == 400


# ── B5/C-1 migration: the sandbox caller is now ASYNC ────────────────────────

def test_sandbox_send_uses_async_aprocess_message(build_app):
    """AFTER Fase B5/C-1: the sandbox endpoint now drives the ASYNC
    ``aprocess_message`` (the sync ``process_message`` was REMOVED). This locks
    the migration end: ``/api/sandbox/send`` reaches ``aprocess_message`` and the
    sync entry point no longer exists on the handler.

    BEFORE (pre-B5): ``/api/sandbox/send`` → ``_sandbox_reply`` →
    ``asyncio.to_thread(process_message, ...)`` (sync seam).
    AFTER  (this):    ``/api/sandbox/send`` → ``_sandbox_reply`` →
    ``await aprocess_message(...)`` (async seam). The ENDPOINT OUTPUT
    (reply/replies/messages) is byte-identical — proven by the unchanged
    ``sandbox_improve_send_*`` goldens above, which now patch ``aprocess_message``.
    """
    built = build_app(["gowa"])
    # The sync seam is gone — the facade no longer exposes process_message.
    assert not hasattr(built.agent_handler, "process_message")

    fake = _stub_process("ok async!", tool_calls=None, contact_info=None)
    with patch.object(built.agent_handler, "aprocess_message", new=fake):
        r = built.client.post("/api/sandbox/send",
                              json={"phone": "5511975900001", "message": "oi"})
    assert r.status_code == 200, r.text
    # The async agent seam was reached exactly once.
    assert len(fake.calls) == 1
    # Stubbed reply flows through unchanged.
    assert r.json()["data"]["reply"] == "ok async!"


def test_improve_stays_sync_isolated():
    """AFTER Fase B5 (DECISION Q1): ``generate_improvement`` deliberately KEEPS
    the ISOLATED SYNC client — it was NOT migrated to async. It lives in
    ``app.services.improvement_service`` and calls ``handler._get_client()`` (the
    sync OpenAI client). The unit/endpoint goldens above already lock its behavior;
    this cell records the decision so a future async move is a deliberate change."""
    import inspect
    from app.services import improvement_service
    # The service entry point is a plain (sync) function, not a coroutine.
    assert not inspect.iscoroutinefunction(improvement_service.generate_improvement)
