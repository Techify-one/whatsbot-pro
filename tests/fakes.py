"""Clean, reusable test doubles for the characterization suite (Plano 23 · A2/A2b kit).

These are PURPOSE-BUILT public fakes — NOT a mechanical copy of the script-coupled
``_Fake*`` classes at the top of ``tests/test_endpoints.py`` (those are entangled
with module-level state and will be converged later in the full A1 split). Keep
them small, documented, and dependency-free so any pytest function can import
them:

    from tests.fakes import FakeGowaClient, fake_agent_reply

The two pieces:

* :class:`FakeGowaClient` — records every outbound ``send_message`` /
  ``send_image`` / ``send_audio`` / ``send_file`` call into ``.sent`` (and the
  per-method lists) and returns concrete values for the lookup methods used by
  inbound parsing (``get_own_number`` etc.). This is what A2 asserts outbound
  args against. Tests can override any canned return value.
* :func:`fake_agent_reply` — a context manager that stubs the LLM/agent so a
  turn produces a deterministic reply (+ optional tool_calls / usage) WITHOUT a
  network call. It patches the SAME seam the legacy suite uses,
  ``AgentHandler.aprocess_message`` (and, for the few sync callers,
  ``process_message``), returning a real ``ProcessResult``. The AGNO engine and
  the OpenAI client are never reached.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator, Optional
from unittest.mock import AsyncMock, patch


# ── FakeGowaClient ──────────────────────────────────────────────────────────

class FakeGowaClient:
    """An inspectable stand-in for ``gowa.client.GOWAClient``.

    Outbound sends are recorded, never performed. ``.sent`` is the unified,
    ordered log of *all* outbound calls as ``(kind, args_dict)`` tuples so a
    golden can assert the full outbound sequence; the per-method lists
    (``.messages`` / ``.images`` / ``.audios`` / ``.files``) are convenience
    views for targeted asserts.

    Lookup methods return concrete values (matching ``conftest.mock_gowa_client``)
    so inbound parsing / contact persistence never tries to save a Mock. Any of
    them can be overridden per-test by setting the corresponding attribute, e.g.
    ``client.own_number = "5599..."`` or ``client.archived = True``.
    """

    # Canned lookup return values (override on the instance in a test).
    own_number: str = "5511999990001"
    group_name: str = "Grupo Teste"
    can_send_in_group: bool = True
    archived: bool = False
    message_filename: str = ""
    port: int = 3000

    def __init__(self) -> None:
        # Unified, ordered outbound log: (kind, {**args}).
        self.sent: list[tuple[str, dict]] = []
        # Per-kind convenience views.
        self.messages: list[dict] = []
        self.images: list[dict] = []
        self.audios: list[dict] = []
        self.files: list[dict] = []
        # No-op side-effect calls also recorded for completeness.
        self.calls: list[tuple[str, tuple, dict]] = []

    # ── Outbound (recorded) ────────────────────────────────────────────────

    def send_message(self, phone: str, text: str,
                     mentions: Optional[list[str]] = None,
                     reply_message_id: Optional[str] = None) -> dict:
        rec = {"phone": phone, "text": text,
               "mentions": mentions, "reply_message_id": reply_message_id}
        self.messages.append(rec)
        self.sent.append(("message", rec))
        return {"results": {"message_id": "FAKE_SENT"}}

    def send_image(self, phone: str, image_path: str, caption: str = "") -> dict:
        rec = {"phone": phone, "image_path": image_path, "caption": caption}
        self.images.append(rec)
        self.sent.append(("image", rec))
        return {"results": {"message_id": "FAKE_SENT_IMG"}}

    def send_audio(self, phone: str, audio_path: str) -> dict:
        rec = {"phone": phone, "audio_path": audio_path}
        self.audios.append(rec)
        self.sent.append(("audio", rec))
        return {"results": {"message_id": "FAKE_SENT_AUDIO"}}

    def send_file(self, phone: str, file_path: str, caption: str = "",
                  filename: Optional[str] = None) -> dict:
        rec = {"phone": phone, "file_path": file_path,
               "caption": caption, "filename": filename}
        self.files.append(rec)
        self.sent.append(("file", rec))
        return {"results": {"message_id": "FAKE_SENT_FILE"}}

    # ── Side-effect no-ops (recorded, return None) ─────────────────────────

    def _noop(self, name: str):
        def _fn(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))
            return None
        return _fn

    def __getattr__(self, name: str):
        # Any not-explicitly-defined method is a recorded no-op returning None.
        # (Lookup methods below are real attributes, so they take precedence.)
        if name.startswith("_"):
            raise AttributeError(name)
        return self._noop(name)

    # ── Lookups (concrete return values) ───────────────────────────────────

    def get_own_number(self) -> str:
        return self.own_number

    def get_group_name(self, group_jid: str) -> str:
        return self.group_name

    def can_bot_send_in_group(self, group_jid: str, bot_phone: str = "") -> bool:
        return self.can_send_in_group

    def is_chat_archived(self, jid: str) -> bool:
        return self.archived

    def get_message_filename(self, chat_jid: str, msg_id: str) -> str:
        return self.message_filename

    # ── Helpers ────────────────────────────────────────────────────────────

    @property
    def sent_texts(self) -> list[str]:
        """Just the text bodies of every ``send_message`` (ordered)."""
        return [r["text"] for r in self.messages]

    def reset(self) -> None:
        self.sent.clear()
        self.messages.clear()
        self.images.clear()
        self.audios.clear()
        self.files.clear()
        self.calls.clear()


# ── fake_agent_reply ────────────────────────────────────────────────────────

def _make_process_result(text: str, tool_calls: Optional[list[dict]],
                         contact_info: Optional[dict]):
    """Build a real ``ProcessResult`` (the handler's public return type)."""
    from agent.handler import ProcessResult
    return ProcessResult(
        reply=text,
        tool_calls=list(tool_calls or []),
        contact_info=contact_info,
    )


@contextlib.contextmanager
def fake_agent_reply(text: str = "resposta de teste", *,
                     tool_calls: Optional[list[dict]] = None,
                     contact_info: Optional[dict] = None,
                     handler: Any = None) -> Iterator[Any]:
    """Stub the agent so a turn yields a deterministic reply, no network call.

    Patches ``AgentHandler.aprocess_message`` (the async seam the live webhook /
    ingest pipeline awaits) AND ``AgentHandler.process_message`` (the sync seam a
    few callers like the sandbox use) to return a canned ``ProcessResult``. The
    AGNO engine (``agno_engine.run_async`` / ``run_sync``) and the OpenAI client
    are therefore never reached — same approach as ``test_endpoints.py``'s
    ``patch.object(agent_handler, "aprocess_message", AsyncMock(...))``, but
    reusable and covering both seams.

    Why this seam (and not ``agno_engine.run_async``): the handler owns usage
    accounting, system-prompt assembly, the ``ai_takeover`` dedupe and the reply
    save; A2/A2b want to characterize THAT orchestration with a fixed model
    output, so the cleanest cut is the handler's own public result boundary.

    Usage::

        with fake_agent_reply("oi!") as rec:
            client.post("/api/webhook/gowa/default", json=...)
        assert rec.calls  # (sender, text, kwargs) per invocation

    If ``handler`` is given, patches that instance's bound methods; otherwise
    patches the class methods (applies to every ``AgentHandler``).

    The yielded recorder exposes ``.calls`` — a list of ``(sender, text, kwargs)``
    for each invocation across either seam.
    """
    result = _make_process_result(text, tool_calls, contact_info)

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.result = result

    rec = _Recorder()

    async def _afake(sender: str, msg_text: str = "", *args: Any, **kwargs: Any):
        rec.calls.append((sender, msg_text, kwargs))
        return result

    def _sfake(sender: str, msg_text: str = "", *args: Any, **kwargs: Any):
        rec.calls.append((sender, msg_text, kwargs))
        return result

    if handler is not None:
        patches = [
            patch.object(handler, "aprocess_message", new=AsyncMock(side_effect=_afake)),
            patch.object(handler, "process_message", new=_sfake),
        ]
    else:
        from agent.handler import AgentHandler
        patches = [
            patch.object(AgentHandler, "aprocess_message", new=AsyncMock(side_effect=_afake)),
            patch.object(AgentHandler, "process_message", new=_sfake),
        ]

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield rec
