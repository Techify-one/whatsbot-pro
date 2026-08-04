"""Golden-master support for the characterization suite (Plano 23 · A2/A2b kit).

Three pieces a golden test composes:

* :func:`normalize` — replace non-deterministic fields with stable placeholders
  so a captured value is byte-comparable across runs: timestamps → ``"<TS>"``,
  message ids → ``"<MSGID>"`` (stable per distinct id, so identity within a
  capture is preserved), latency / ``*_ms`` → ``"<MS>"``. Lists whose order is
  non-deterministic should be passed through :func:`sort_events` (or captured
  via :attr:`EventRecorder.name_set`) — the bus fans out with
  ``asyncio.create_task``, so for events the SET is deterministic but the
  SEQUENCE within a single emit() is not. A2's "sequence vs set" checklist
  asserts each appropriately: use ``.names`` (ordered, for cross-emit ordering
  that IS deterministic) and ``.name_set`` / ``sort_events`` (for intra-emit
  fan-out where only membership is guaranteed).

* :class:`EventRecorder` — generalizes the C0 capture filter. Inside a
  ``with EventRecorder() as rec:`` block it records ``(event_name, payload)`` for
  every bus emit (BOTH sync and async paths) via a ``filter.event.before_emit``
  filter wired onto a private background loop. Exposes ``.records``, ``.names``
  (ordered), ``.name_set``, and ``.by_name(event)``.

* :func:`golden_assert` — serialize ``value`` (json, sorted keys) and compare
  against ``tests/goldens/<name>.json``; (re)write it when
  ``UPDATE_GOLDENS=1``. Goldens are checked-in files, regenerable on intentional
  change.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

GOLDENS_DIR = Path(__file__).resolve().parent / "goldens"

# ── Non-determinism patterns ────────────────────────────────────────────────

# Keys whose VALUES are non-deterministic regardless of content.
_TS_KEYS = {"ts", "emitted_at", "timestamp", "created_at", "updated_at",
            "installed_at", "sent_at", "emitted"}
_MS_KEYS_RE = re.compile(r"(_ms|latency|duration_ms|elapsed)$")
_MSGID_KEYS = {"msg_id", "external_msg_id", "message_id", "reacted_message_id",
               "reply_to_msg_id", "id", "execution_id"}

# A 10+ digit epoch (seconds or millis), with optional fraction → "<TS>".
_EPOCH_RE = re.compile(r"^\d{10}(\.\d+)?$")


def normalize(obj: Any, *, _msgids: Optional[dict] = None) -> Any:
    """Recursively replace non-deterministic fields with stable placeholders.

    * timestamp-ish keys → ``"<TS>"``
    * ``*_ms`` / latency / duration keys → ``"<MS>"``
    * message-id keys → ``"<MSGID>"`` or ``"<MSGID:n>"`` (stable per distinct id
      WITHIN one normalize() call, so identity is preserved but the literal id
      isn't asserted). Auto-generated uuid4 ids and ``FAKE_SENT*`` ids collapse;
      a deterministic test id like ``"gw_1"`` also collapses (we never assert the
      literal). Pass the SAME ``_msgids`` map across related captures to keep the
      mapping consistent.

    Returns a NEW structure; the input is not mutated.
    """
    if _msgids is None:
        _msgids = {}

    def _msgid_placeholder(value: Any) -> str:
        key = repr(value)
        if key not in _msgids:
            _msgids[key] = ("<MSGID>" if not _msgids
                            else f"<MSGID:{len(_msgids)}>")
        return _msgids[key]

    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if k in _TS_KEYS:
                out[k] = "<TS>" if v is not None else None
            elif _MS_KEYS_RE.search(str(k)):
                out[k] = "<MS>" if v is not None else None
            elif k in _MSGID_KEYS:
                out[k] = _msgid_placeholder(v) if v not in (None, "") else v
            else:
                out[k] = normalize(v, _msgids=_msgids)
        return out
    if isinstance(obj, (list, tuple)):
        return [normalize(v, _msgids=_msgids) for v in obj]
    if isinstance(obj, float) and _EPOCH_RE.match(repr(obj)):
        return "<TS>"
    if isinstance(obj, str) and _EPOCH_RE.match(obj):
        return "<TS>"
    return obj


def sort_events(names: list[str]) -> list[str]:
    """Return the event names sorted — the deterministic SET view of a fan-out."""
    return sorted(names)


# ── EventRecorder ───────────────────────────────────────────────────────────

class EventRecorder:
    """Capture every bus event emitted inside a ``with`` block.

    Generalizes the C0 capture filter (``test_conversation_events_c0``): wires a
    real background event loop via ``bus.set_runtime`` so SYNC emit paths
    (tool/memory threads, ``emit_with_filter_sync``) also reach the filter, then
    registers a ``filter.event.before_emit`` filter that records
    ``(event_name, payload)`` and returns the payload unchanged (never
    suppresses).

    Coexists with an already-running app's plugins: it does NOT call
    ``bus.reset()`` (which would drop the app's own handlers/filters) — it
    registers its capture filter under a unique plugin id and removes EXACTLY
    that entry on exit, and it restores whatever ``set_runtime`` was in effect.

    Exposes after/while recording:
        ``.records``   — ordered list of ``(event_name, payload)``
        ``.names``     — ordered list of event names (cross-emit order, which IS
                         deterministic for serial route steps)
        ``.name_set``  — set of event names (the fan-out-safe membership view)
        ``.by_name(n)``— list of payloads for event ``n``

    ``drain(seconds=0.05)`` lets the background loop flush scheduled emit() tasks.
    """

    _counter = 0

    def __init__(self) -> None:
        EventRecorder._counter += 1
        self._plugin_id = f"_charz_recorder_{EventRecorder._counter}"
        self.records: list[tuple[str, dict]] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._own_loop = False
        self._prev_loop = None
        self._prev_handler = None

    # ── context management ──────────────────────────────────────────────────

    def __enter__(self) -> "EventRecorder":
        from plugins import events as bus

        # Reuse an existing bus loop if one is wired & running; else spin our own.
        existing = getattr(bus, "_loop", None)
        self._prev_loop = existing
        self._prev_handler = getattr(bus, "_agent_handler", None)
        if existing is not None and existing.is_running():
            self._loop = existing
            self._own_loop = False
        else:
            self._loop = asyncio.new_event_loop()
            started = threading.Event()

            def _run() -> None:
                asyncio.set_event_loop(self._loop)
                started.set()
                self._loop.run_forever()

            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
            started.wait()
            self._own_loop = True
            bus.set_runtime(self._loop, self._prev_handler)

        def _capture(ctx, payload):
            self.records.append((ctx.extras.get("event_name"), payload))
            return payload

        # Priority 1 → runs first; returns payload unchanged so nothing breaks.
        bus.register_filter(self._plugin_id, "filter.event.before_emit",
                            _capture, priority=1)
        return self

    def __exit__(self, *exc) -> None:
        from plugins import events as bus

        self.drain()
        # Remove ONLY our capture filter (leave the app's filters intact).
        bucket = bus._filters.get("filter.event.before_emit")
        if bucket:
            bus._filters["filter.event.before_emit"] = [
                t for t in bucket if t[1] != self._plugin_id
            ]

        if self._own_loop and self._loop is not None:
            # Restore whatever runtime was wired before us, then tear down.
            bus.set_runtime(self._prev_loop, self._prev_handler)
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=2.0)

    # ── flush + views ───────────────────────────────────────────────────────

    def drain(self, seconds: float = 0.05) -> None:
        """Let the background loop flush scheduled emit() fan-out tasks."""
        time.sleep(seconds)

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.records if n]

    @property
    def name_set(self) -> set[str]:
        return {n for n, _ in self.records if n}

    def by_name(self, event_name: str) -> list[dict]:
        return [p for n, p in self.records if n == event_name]

    def names_with_prefix(self, prefix: str) -> list[str]:
        return [n for n in self.names if n.startswith(prefix)]

    def clear(self) -> None:
        self.records.clear()


# ── golden_assert ───────────────────────────────────────────────────────────

def golden_assert(name: str, value: Any, *,
                  update: Optional[str] = None) -> None:
    """Compare ``value`` against the checked-in golden ``<name>.json``.

    Serializes ``value`` as canonical JSON (``sort_keys=True``, 2-space indent)
    and compares against ``tests/goldens/<name>.json``. When
    ``update`` is truthy (defaults to ``$UPDATE_GOLDENS``), (re)writes the golden
    instead of asserting — so an intentional change is a one-command refresh:

        UPDATE_GOLDENS=1 ./venv/bin/pytest tests/core/characterization \
            tests/integration/characterization

    ``value`` should already be normalized (see :func:`normalize`).
    """
    if update is None:
        update = os.environ.get("UPDATE_GOLDENS")

    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    path = GOLDENS_DIR / f"{name}.json"
    serialized = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)

    if update:
        path.write_text(serialized + "\n", encoding="utf-8")
        return

    if not path.exists():
        raise AssertionError(
            f"golden {name!r} missing ({path}). Generate it with "
            f"UPDATE_GOLDENS=1 after verifying the captured value is correct:\n"
            f"{serialized}")

    expected = path.read_text(encoding="utf-8").rstrip("\n")
    actual = serialized
    if expected != actual:
        raise AssertionError(
            f"golden {name!r} mismatch.\n"
            f"--- expected ({path}) ---\n{expected}\n"
            f"--- actual ---\n{actual}\n"
            f"If this change is intentional, regenerate with UPDATE_GOLDENS=1.")
