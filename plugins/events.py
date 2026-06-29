"""Plugin event bus + filter pipeline.

Two complementary mechanisms, both populated at startup by ``plugins.loader``
and reachable from any thread or coroutine:

* **Events** — broadcast, fire-and-forget. Plugins subscribe by exporting
  ``EVENT_HANDLERS = {"message.received": fn, ...}`` in their ``events.py``.
  ``emit(name, payload)`` schedules every subscriber as an isolated
  ``asyncio.Task``; an exception in one handler never reaches the producer.

* **Filters** — interceptive, synchronous in the pipeline. Plugins export
  ``FILTERS = {"filter.reply.part": fn, ...}`` (optionally
  ``{"filter.reply.part": (fn, priority)}``). ``await apply_filter(name, value)``
  chains every filter in priority order; returning ``None`` aborts the cascade
  and the producer should skip the wrapped action.

Design references inspected before settling on this surface: Baileys
EventEmitter (Node WhatsApp), WAHA webhook taxonomy (wildcard ``*``,
``message.any`` alias), Home Assistant ``bus.async_listen``, WordPress
``do_action``/``apply_filters`` (events vs filters split), and Python libs
``pyee``/``blinker``/``fastapi-events``. We do not pull a library because
``plugins.context.broadcast`` already follows a hand-rolled, thread-safe
pattern and the EXE bundle benefits from fewer deps.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Optional, Union

logger = logging.getLogger(__name__)

EventHandler = Callable[..., Optional[Awaitable[None]]]
FilterFn = Callable[..., Any]

KNOWN_EVENTS: set[str] = {
    # GOWA inbound / outbound message lifecycle
    "message.received", "message.sent", "message.saved",
    # ``message.persisted`` (plano 23 Fase C5): emitted by ``agent.memory.add_message``
    # AFTER the DB INSERT — the single decoupling signal the lifecycle WS/notice
    # effects (created/reopened) listen on. Public payload:
    # ``{conversation_id, contact_id, role, msg_id, ts}``.
    "message.persisted",
    "message.reaction", "message.edited", "message.revoked", "message.deleted",
    # Presence / receipts
    "presence.changed", "receipt.changed",
    # Group / call / newsletter
    "group.participants_changed", "group.joined",
    "call.received",
    "newsletter.event",
    # Chat-level
    "chat.archived",
    # Channel lifecycle (plano 23 Fase B6 — minimal seam, C3 normalizes fully).
    # ``channel.updated``: a channel config/credential edit (payload
    #   ``{channel_id, keys_changed, ts}``) — cache invalidation is driven off it.
    # ``channel.status_changed``: a live status read (payload
    #   ``{channel_id, status, is_connected, is_logged_in, ts}``).
    "channel.updated", "channel.status_changed",
    # Connection / lifecycle
    "connection.changed",
    "app.startup", "app.shutdown",
    "plugin.loaded", "plugin.enabled", "plugin.disabled",
    "plugin.settings.changed",
    # LLM / tools
    "llm.before", "llm.after",
    "tool.before", "tool.after",
    # Internal CRUD
    "contact.updated", "contact.ai_toggled",
    "contact.tagged", "contact.untagged",
    "tag.created", "tag.updated", "tag.deleted",
    # Conversations (plano 01 / plano 10 RT)
    "conversation.created", "conversation.status_changed", "conversation.assigned",
    "conversation.archived", "conversation.ai_toggled", "conversation.updated",
    "conversation.deleted",
    # Conversation lifecycle verbs (plano 23 Fase C0 — Expand antecipado)
    "conversation.reopened", "conversation.unassigned",
    "conversation.transferred_to_human", "conversation.agent_changed",
    "conversation.attribute_set", "conversation.ai_takeover",
    "config.changed",
    "tool_override.changed",
    "execution.started", "execution.ended",
    # Runtime supervisor (plano 09 Fase 3)
    "task.crashed",
    # Managed subprocess service (plano 09 Fase 4)
    "subprocess.crashed", "subprocess.restarted",
}

# ── Filter contract (plano 23 Fase C2) ───────────────────────────────────────
#
# ``KNOWN_FILTERS`` is the validated catalogue of every filter name the CORE
# applies via ``apply_filter`` / ``apply_filter_sync`` (mirror of
# ``KNOWN_EVENTS``). It exists to kill the silent typo (e.g. a plugin exporting
# ``FILTERS = {"filter.replay.part": fn}`` would never fire and never warn).
# When a plugin registers a filter whose name is NOT here, ``register_filter``
# logs a WARNING — same severity ``register`` uses for an unknown EVENT name, and
# informative ONLY, NEVER blocking: plugins MAY define their own filter names (a
# plugin can publish a filter another plugin consumes), so an unknown name is
# legal, just worth flagging.
#
# Versioning contract (``WHATSBOT_API_VERSION`` semver): adding a filter name is
# ADDITIVE → a MINOR bump. Removing or renaming a filter, or changing the type of
# its piped value / abort (``None``) semantics, is a breaking change → MAJOR.
# Seams that are still in flux are marked ``experimental`` below and may move
# without a MAJOR until they graduate; depend on them at your own risk.
#
# To document a NEW core filter: add its name here in the same commit that adds
# the ``apply_filter(<name>, ...)`` call site (filters are seams — they are born
# with their producer, not registered after the fact).
KNOWN_FILTERS: set[str] = {
    # Inbound webhook / message ingest
    "filter.webhook.payload",
    "filter.message.before_save", "filter.message.outgoing",
    # Transcription / media (``filter.media.unknown`` is the last-resort hook for
    # a plugin to claim an otherwise-unrecognised media type).
    "filter.transcription.should_run", "filter.transcription.result",
    "filter.media.unknown",
    # Contact / tags
    "filter.contact.tags",
    # Event bus self-interception
    "filter.event.before_emit",
    # LLM turn (system prompt, history, tool schemas)
    "filter.system_prompt", "filter.llm.messages", "filter.llm.tools",
    # Tool dispatch (args in, result out)
    "filter.tool.args", "filter.tool.result",
    # Outbound reply (raw → parts → each part)
    "filter.reply.raw", "filter.reply.parts", "filter.reply.part",
    # AuthZ ABAC seam
    "filter.authz.decision",
    # Plano 23 Fase B4 — conversation lifecycle/ownership pre-action filters.
    # ``filter.conversation.before_status`` was RELOCATED from the route into
    # ``conversation_service``; ``filter.conversation.before_assign`` is new.
    "filter.conversation.before_status", "filter.conversation.before_assign",
    # Plano 23 Fase B5 — agent-turn seams (§4.2, experimental — may change while
    # the attendance plugin firms up):
    # ``filter.agent.resolve``: swap the resolved AgentSpec for a turn
    #   (None ⇒ keep default) — in ``agent_run_service``.
    # ``filter.conversation.assignment``: rewrite the round-robin destination of
    #   ``transfer_to_human`` (None ⇒ default assignment) — in the tool.
    "filter.agent.resolve", "filter.conversation.assignment",
}

# Filter seams that are still in flux (``experimental:true`` — see the semver note
# above). Subset of KNOWN_FILTERS; not an enforcement gate, purely informative.
EXPERIMENTAL_FILTERS: set[str] = {
    "filter.agent.resolve", "filter.conversation.assignment",
}

# Backwards-compat alias for the pre-C2 informational catalogue name. ``KNOWN_FILTERS``
# is the canonical registry now; keep the old name pointing at it so any importer
# (or external tooling) still resolves.
_CORE_FILTER_NAMES = KNOWN_FILTERS

# Subscription keys that are dispatch targets, not emission sources.
# ``message.any`` is re-dispatched automatically by :func:`emit` whenever
# ``message.received`` or ``message.sent`` fires, with ``direction`` added
# to the payload — plugins should subscribe to it rather than emit it.
# ``*`` is the wildcard catch-all that receives every emitted event.
_DISPATCH_ONLY_KEYS: set[str] = {"*", "message.any"}

# Lifecycle events that must NOT be interceptable via
# ``filter.event.before_emit`` — plugins should not be able to block
# their own load/disable or the app's startup/shutdown.
_LIFECYCLE_EVENTS: set[str] = {
    "app.startup", "app.shutdown",
    "plugin.loaded", "plugin.enabled", "plugin.disabled",
    "plugin.settings.changed",
}

# name -> [(plugin_id, handler), ...] in registration order
_handlers: dict[str, list[tuple[str, EventHandler]]] = {}
# name -> [(priority, plugin_id, fn), ...] sorted ascending by priority
_filters: dict[str, list[tuple[int, str, FilterFn]]] = {}

_loop: Optional[asyncio.AbstractEventLoop] = None
_agent_handler: Optional[Any] = None

# ── Synchronous CORE subscribers (plano 23 Fase C5) ──────────────────────────
#
# A core WS-projection listener (``app.services.ws_projections``) must turn each
# lifecycle DOMAIN event into the WS broadcast the panel expects (the Contract
# step of the Parallel Change: the broadcast becomes a LISTENER of the event, so
# the event is the SINGLE trigger). The plugin fan-out (:func:`emit`) schedules
# handlers as ``create_task`` — deferred — which would RACE the characterization /
# 876 suites that perform an action then immediately assert ``ws_manager.broadcast``
# was called. So the WS projection is registered HERE, as a SYNCHRONOUS in-process
# core subscriber that runs INSIDE the emit (``await``-ed in the async path, called
# directly in the sync path), BEFORE the deferred plugin fan-out — giving identical
# observable timing to the old inline ``ws_manager.broadcast`` call.
#
# These are core-only (registered at server startup, cleared by ``reset()``), run
# after ``filter.event.before_emit`` (they see the same payload subscribers/audit
# see), and are isolated: an exception in one never reaches the producer or the
# other subscribers. They are NOT the plugin bus — plugins still use
# ``EVENT_HANDLERS``.
_core_sync_listeners: list[Callable[[str, dict], Any]] = []


def register_core_sync_listener(fn: Callable[[str, dict], Any]) -> None:
    """Register a CORE listener invoked synchronously inside every (filtered) emit.

    ``fn(event_name, payload)`` runs in-line within ``emit_with_filter`` /
    ``emit_with_filter_sync`` (awaited if it returns a coroutine in the async path),
    so any side effect it performs (the WS broadcast in ``ws_projections``) is
    observable with the SAME timing the old inline broadcast had. Lifecycle events
    (which bypass ``filter.event.before_emit``) also reach core listeners. Idempotent
    against the same callable object.
    """
    if fn not in _core_sync_listeners:
        _core_sync_listeners.append(fn)


async def _run_core_sync_listeners(event_name: str, payload: dict) -> None:
    """Invoke every core sync listener for ``event_name`` (async emit path).

    Each listener is isolated — a raising listener logs and never blocks the others
    or the producer. Coroutine results are awaited so the WS broadcast completes
    before the emit returns (preserving the old inline-await timing)."""
    for fn in list(_core_sync_listeners):
        try:
            result = fn(event_name, payload)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.debug("core sync listener for %s failed: %s", event_name, e)


def _run_core_sync_listeners_sync(event_name: str, payload: dict) -> None:
    """Invoke every core sync listener for ``event_name`` (sync emit path).

    Mirrors :func:`_run_core_sync_listeners` for ``emit_with_filter_sync``. If a
    listener returns a coroutine it is scheduled on the bus loop (best-effort) so a
    sync call site (a tool / memory thread) still drives the WS broadcast; when no
    loop is wired (legacy script tests) it is run to completion inline."""
    for fn in list(_core_sync_listeners):
        try:
            result = fn(event_name, payload)
            if inspect.isawaitable(result):
                if _loop is not None and _loop.is_running():
                    asyncio.run_coroutine_threadsafe(result, _loop)
                else:
                    asyncio.get_event_loop().run_until_complete(result)
        except Exception as e:
            logger.debug("core sync listener for %s failed: %s", event_name, e)


def set_runtime(loop: asyncio.AbstractEventLoop, agent_handler: Any) -> None:
    """Wire the bus at server lifespan start. Idempotent."""
    global _loop, _agent_handler
    _loop = loop
    _agent_handler = agent_handler


def reset() -> None:
    """Clear all handlers and filters. For tests only."""
    _handlers.clear()
    _filters.clear()
    _core_sync_listeners.clear()


# ── Exported-dict validation (single source — plano 23 Fase C4) ────────────
#
# The shape/callable validation of a plugin's exported ``EVENT_HANDLERS`` /
# ``FILTERS`` dicts lives here, NOT in ``plugins.loader``. The loader imports
# these so it does not re-implement the same checks (the duplicate it used to
# carry). ``register_plugin_events`` / ``register_plugin_filters`` reuse them too,
# so a single function owns the rules + warning text.


def validate_event_handlers(
    plugin_id: str, raw: Any,
) -> dict[str, EventHandler]:
    """Return the valid ``{name: callable}`` subset of an exported EVENT_HANDLERS.

    Non-dict input warns and yields ``{}``; non-callable entries warn and are
    skipped. The single source of EVENT_HANDLERS validation.
    """
    if not raw:  # None / empty dict / falsy — nothing exported, no warning
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            "Plugin %s: EVENT_HANDLERS must be a dict, got %s",
            plugin_id, type(raw).__name__,
        )
        return {}
    out: dict[str, EventHandler] = {}
    for name, fn in raw.items():
        if callable(fn):
            out[str(name)] = fn
        else:
            logger.warning(
                "Plugin %s: EVENT_HANDLERS[%r] is not callable, skipped",
                plugin_id, name,
            )
    return out


def validate_filters(
    plugin_id: str, raw: Any,
) -> dict[str, Union[FilterFn, tuple]]:
    """Return the valid subset of an exported FILTERS dict.

    Each value may be a callable or a ``(callable, priority)`` tuple. Non-dict
    input warns and yields ``{}``; malformed entries warn and are skipped. The
    single source of FILTERS validation.
    """
    if not raw:  # None / empty dict / falsy — nothing exported, no warning
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            "Plugin %s: FILTERS must be a dict, got %s",
            plugin_id, type(raw).__name__,
        )
        return {}
    out: dict[str, Union[FilterFn, tuple]] = {}
    for name, entry in raw.items():
        if isinstance(entry, tuple) and len(entry) == 2 and callable(entry[0]):
            out[str(name)] = entry
        elif callable(entry):
            out[str(name)] = entry
        else:
            logger.warning(
                "Plugin %s: FILTERS[%r] must be callable or (callable, int), skipped",
                plugin_id, name,
            )
    return out


# ── Events ───────────────────────────────────────────────────────────────


def register(plugin_id: str, event_name: str, handler: EventHandler) -> None:
    if event_name not in KNOWN_EVENTS and event_name not in _DISPATCH_ONLY_KEYS:
        logger.warning(
            "Plugin %s subscribed to unknown event %r — will receive nothing unless emit() is added",
            plugin_id, event_name,
        )
    _handlers.setdefault(event_name, []).append((plugin_id, handler))


def register_plugin_events(plugin_id: str, handlers: dict[str, EventHandler]) -> None:
    """Bulk register the EVENT_HANDLERS dict exported by a plugin.

    Validation (dict shape + callable) is delegated to :func:`validate_event_handlers`
    so the rules live in one place (the loader reuses it too).
    """
    for name, fn in validate_event_handlers(plugin_id, handlers).items():
        register(plugin_id, name, fn)


def emit(event_name: str, payload: dict) -> None:
    """Fire-and-forget dispatch. Safe to call from any thread or coroutine.

    Dispatch order: subscribers of the exact event → subscribers of
    ``message.any`` (if applicable) → subscribers of ``*``. Wildcard
    receives the original ``ctx.event_name`` to allow generic handlers.
    """
    if event_name in _DISPATCH_ONLY_KEYS:
        logger.warning("emit() called with dispatch-only key %r; ignored", event_name)
        return

    targeted = list(_handlers.get(event_name, ()))
    any_subs = (
        list(_handlers.get("message.any", ()))
        if event_name in ("message.received", "message.sent")
        else []
    )
    wildcard_subs = list(_handlers.get("*", ()))
    if not (targeted or any_subs or wildcard_subs):
        return
    if _loop is None:
        logger.debug("event bus not initialized; dropping %s", event_name)
        return

    direction: Optional[str] = None
    if event_name == "message.received":
        direction = "in"
    elif event_name == "message.sent":
        direction = "out"

    async def _fanout() -> None:
        for plugin_id, handler in targeted:
            asyncio.create_task(_run_one(plugin_id, event_name, handler, payload))
        if any_subs:
            any_payload = dict(payload)
            if direction is not None:
                any_payload["direction"] = direction
            for plugin_id, handler in any_subs:
                asyncio.create_task(_run_one(plugin_id, event_name, handler, any_payload))
        for plugin_id, handler in wildcard_subs:
            asyncio.create_task(_run_one(plugin_id, event_name, handler, payload))

    try:
        asyncio.run_coroutine_threadsafe(_fanout(), _loop)
    except Exception as e:
        logger.debug("emit %s failed to schedule: %s", event_name, e)


async def emit_with_filter(event_name: str, payload: dict) -> None:
    """Emit an event after letting plugins veto/rewrite the payload.

    The payload is passed through ``filter.event.before_emit`` first.
    Any plugin can return ``None`` to suppress the event entirely or
    return a modified payload to reshape what subscribers see. Lifecycle
    events (see ``_LIFECYCLE_EVENTS``) bypass the filter — plugins are
    not allowed to block their own load/disable or app startup/shutdown.

    Use this in async paths where you want plugin interception; use
    :func:`emit_with_filter_sync` in sync paths, or :func:`emit`
    directly for lifecycle / perf-sensitive sync paths.
    """
    if event_name in _LIFECYCLE_EVENTS:
        await _run_core_sync_listeners(event_name, payload)
        emit(event_name, payload)
        return
    filtered = await apply_filter(
        "filter.event.before_emit", payload, {"event_name": event_name}
    )
    if filtered is None:
        return
    out = filtered if isinstance(filtered, dict) else payload
    # Core projection FIRST (synchronous, observable timing), then plugin fan-out.
    await _run_core_sync_listeners(event_name, out)
    emit(event_name, out)


def emit_with_filter_sync(event_name: str, payload: dict) -> None:
    """Sync sibling of :func:`emit_with_filter`.

    Use from worker threads (e.g. inside ``asyncio.to_thread`` or in
    legacy sync code like ``AgentHandler.process_message``). On the
    event-loop thread it short-circuits (filter is skipped) — same
    semantics as :func:`apply_filter_sync`.
    """
    if event_name in _LIFECYCLE_EVENTS:
        _run_core_sync_listeners_sync(event_name, payload)
        emit(event_name, payload)
        return
    filtered = apply_filter_sync(
        "filter.event.before_emit", payload, {"event_name": event_name}
    )
    if filtered is None:
        return
    out = filtered if isinstance(filtered, dict) else payload
    _run_core_sync_listeners_sync(event_name, out)
    emit(event_name, out)


async def _run_one(
    plugin_id: str, event_name: str, handler: EventHandler, payload: dict
) -> None:
    from plugins.context import EventContext, make_plugin_db  # late import to avoid cycle
    ctx = EventContext(
        handler=_agent_handler,
        plugin_id=plugin_id,
        plugin_db=make_plugin_db,
        event_name=event_name,
        emitted_at=time.time(),
    )
    try:
        if inspect.iscoroutinefunction(handler):
            await handler(ctx, payload)
        else:
            await asyncio.to_thread(handler, ctx, payload)
    except Exception as e:
        logger.warning(
            "plugin %s handler for %s raised %s: %s",
            plugin_id, event_name, type(e).__name__, e,
        )


# ── Filters ──────────────────────────────────────────────────────────────


def register_filter(
    plugin_id: str, filter_name: str, fn: FilterFn, priority: int = 100
) -> None:
    if filter_name not in KNOWN_FILTERS:
        # Mirror ``register`` for unknown EVENT names: informative warning, never
        # blocking. A plugin MAY define its own filter name (e.g. one plugin
        # publishes a seam another consumes), so an unknown name is legal — but it
        # most often means a typo (``filter.replay.part``) that would silently
        # never fire. Flag it so the author notices.
        logger.warning(
            "Plugin %s registered unknown filter %r — not a core filter name; "
            "it will only fire if some code calls apply_filter(%r)",
            plugin_id, filter_name, filter_name,
        )
    bucket = _filters.setdefault(filter_name, [])
    bucket.append((int(priority), plugin_id, fn))
    bucket.sort(key=lambda t: t[0])


def register_plugin_filters(
    plugin_id: str, filters: dict[str, Union[FilterFn, tuple[FilterFn, int]]],
) -> None:
    """Bulk register the FILTERS dict exported by a plugin.

    Each value may be a callable or a ``(callable, priority)`` tuple. Lower
    priority numbers run earlier in the chain. Validation (dict shape + entry
    shape) is delegated to :func:`validate_filters` (single source; the loader
    reuses it too).
    """
    for name, entry in validate_filters(plugin_id, filters).items():
        if isinstance(entry, tuple):
            fn, priority = entry
        else:
            fn, priority = entry, 100
        register_filter(plugin_id, name, fn, priority=priority)


async def apply_filter(
    filter_name: str, value: Any, ctx_extras: Optional[dict] = None,
) -> Any:
    """Chain every filter registered for ``filter_name``.

    Each filter receives ``(FilterContext, value)`` and returns the modified
    value, or ``None`` to abort. ``None`` short-circuits the chain and is
    returned to the caller. Exceptions log a warning and the value passes
    through unchanged to the next filter (broken filter never traps the
    pipeline).
    """
    bucket = _filters.get(filter_name)
    if not bucket:
        return value

    from plugins.context import FilterContext, make_plugin_db  # late import to avoid cycle

    current = value
    for priority, plugin_id, fn in list(bucket):
        ctx = FilterContext(
            handler=_agent_handler,
            plugin_id=plugin_id,
            plugin_db=make_plugin_db,
            filter_name=filter_name,
            emitted_at=time.time(),
        )
        if ctx_extras:
            ctx.extras = dict(ctx_extras)
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(ctx, current)
            else:
                result = fn(ctx, current)
        except Exception as e:
            logger.warning(
                "plugin %s filter %s raised %s: %s — value passed through",
                plugin_id, filter_name, type(e).__name__, e,
            )
            continue
        if result is None:
            logger.info(
                "filter %s aborted by plugin %s (priority=%d)",
                filter_name, plugin_id, priority,
            )
            return None
        current = result
    return current


def apply_filter_sync(
    filter_name: str,
    value: Any,
    ctx_extras: Optional[dict] = None,
    timeout: float = 5.0,
) -> Any:
    """Synchronous wrapper for :func:`apply_filter`.

    Only safe to call from a *non-event-loop* thread — typically code running
    inside ``asyncio.to_thread`` or in a worker thread that has no running
    asyncio loop. If invoked from the event-loop thread it would deadlock, so
    this helper falls back to returning the value unchanged in that case.
    """
    if _loop is None or not _filters.get(filter_name):
        return value
    # Detect "we're on the loop thread" to avoid deadlock.
    try:
        running = asyncio.get_running_loop()
        if running is _loop:
            logger.debug(
                "apply_filter_sync(%s) called from the event loop thread; "
                "skipping (use apply_filter directly there)",
                filter_name,
            )
            return value
    except RuntimeError:
        pass
    try:
        future = asyncio.run_coroutine_threadsafe(
            apply_filter(filter_name, value, ctx_extras), _loop
        )
        return future.result(timeout=timeout)
    except Exception as e:
        logger.warning(
            "apply_filter_sync(%s) failed: %s — value passed through",
            filter_name, e,
        )
        return value
