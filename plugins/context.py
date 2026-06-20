"""Context objects passed to plugin entry points (tools, prompts, routes).

A ``ToolContext`` is built by ``AgentHandler._dispatch_tool`` for every tool
call, regardless of whether the tool is a core tool or comes from a plugin.
Plugins receive ``plugin_id`` set; core tools receive ``None``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from agent.handler import AgentHandler
    from agent.memory import ContactMemory, TagRegistry

logger = logging.getLogger(__name__)


# ── WebSocket broadcast bridge for plugins ────────────────────────────────
#
# Plugin tool executors run synchronously inside ``asyncio.to_thread``. To let
# a plugin push a real-time event to the frontend (e.g. "novo lembrete"), we
# expose a thread-safe ``broadcast(event, data)`` helper that schedules the
# coroutine on the main event loop. The server wires the ws_manager and loop
# at startup via ``set_runtime``.

_ws_manager: Optional[Any] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
# Runtime services (plano 09 Fase 5): the task supervisor + subprocess service,
# wired at lifespan so PluginContext.spawn_* can delegate to them.
_supervisor: Optional[Any] = None
_subprocess_service: Optional[Any] = None
# Channel runtime (plano 13 Fase 1.1): the channel registry, outbound router and
# the provider-agnostic inbound funnel, wired at lifespan so a channel-provider
# plugin (GOWA, Telegram, …) can register/look up live channels and push inbound
# events through the SAME orchestrator the GOWA webhook uses — without the core
# knowing the provider. Read by PluginContext (and, when useful, tools).
_channel_registry: Optional[Any] = None
_outbound_router: Optional[Any] = None
_ingest_event: Optional[Any] = None
# Server dependencies (plano 13 Fase 1.2): the ServerDeps container, wired at
# lifespan BEFORE plugin setup() runs. A first-party lifecycle plugin (GOWA) reads
# it to own the subprocess + polling loops that the core used to register itself
# (deps already holds gowa_manager/gowa_client/ws_manager/state/settings). Optional
# — None in the test harness (lifespan is skipped) and for zero-channel boots.
_deps: Optional[Any] = None


def set_runtime(ws_manager: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Called once during server startup. Plugins read these via ``broadcast``."""
    global _ws_manager, _loop
    _ws_manager = ws_manager
    _loop = loop


def set_runtime_services(supervisor: Any, subprocess_service: Any) -> None:
    """Wire the task supervisor + subprocess service (plano 09 Fase 5)."""
    global _supervisor, _subprocess_service
    _supervisor = supervisor
    _subprocess_service = subprocess_service


def set_channel_runtime(channel_registry: Any, outbound_router: Any,
                        ingest_event: Any) -> None:
    """Wire the channel runtime for plugin providers (plano 13 Fase 1.1).

    Called once at lifespan, BEFORE plugin ``setup()`` runs, so a channel plugin
    can materialise its live channels and feed inbound through ``ctx.ingest_event``.
    """
    global _channel_registry, _outbound_router, _ingest_event
    _channel_registry = channel_registry
    _outbound_router = outbound_router
    _ingest_event = ingest_event


def get_channel_runtime() -> tuple:
    """Return the wired ``(channel_registry, outbound_router, ingest_event)``.

    Reads the live module globals (re-bound by :func:`set_channel_runtime`), so a
    consumer always sees the current wiring rather than a stale import-time copy.
    """
    return (_channel_registry, _outbound_router, _ingest_event)


def set_deps(deps: Any) -> None:
    """Wire the server ``ServerDeps`` for first-party lifecycle plugins (plano 13).

    Called once at lifespan, BEFORE plugin ``setup()`` runs, so the GOWA plugin
    can reach gowa_manager/gowa_client/ws_manager/state/settings to own its
    subprocess + polling loops. Other plugins ignore it.
    """
    global _deps
    _deps = deps


def get_deps() -> Optional[Any]:
    """Return the wired ``ServerDeps`` (None until :func:`set_deps`, e.g. in tests)."""
    return _deps


def broadcast(event: str, data: dict) -> None:
    """Best-effort WS broadcast from any thread. Never raises."""
    if _ws_manager is None or _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _ws_manager.broadcast(event, data), _loop
        )
    except Exception as e:
        logger.debug("plugin broadcast failed: %s", e)


# ── DB access for plugins ────────────────────────────────────────────────
#
# Plugins access their dedicated ``plugin_<id>_*`` tables through the shared
# SQLAlchemy engine — there is no separate per-plugin database. The
# ``plugin_db`` callable on ``ToolContext`` returns a context manager yielding
# a ``Connection`` so plugin code can do:
#
#     with ctx.plugin_db() as conn:
#         conn.execute(text("INSERT INTO plugin_foo_items ..."), {...})
#
# This replaces the legacy pattern of calling ``get_db()`` and operating on a
# raw ``sqlite3.Connection``.


def make_plugin_db():
    """Return a context manager that opens a transactional engine connection."""
    from db.engine import get_engine
    return get_engine().begin()


@dataclasses.dataclass
class ToolContext:
    """Context passed to a tool executor.

    Attributes:
        contact: ``ContactMemory`` of the contact that triggered the tool call.
        handler: The ``AgentHandler`` instance, exposes tag_registry, model, etc.
        tag_registry: Convenience pointer to ``handler.tag_registry``.
        plugin_id: Plugin id if the tool comes from a plugin, ``None`` for core.
        plugin_db: Optional callable returning a transactional ``Connection``
            context manager scoped to the shared engine.
    """

    contact: "ContactMemory"
    handler: "AgentHandler"
    tag_registry: "TagRegistry"
    plugin_id: Optional[str] = None
    plugin_db: Optional[Callable[[], Any]] = None


@dataclasses.dataclass
class PluginContext:
    """Context handed to a plugin's ``setup(ctx)`` / ``teardown(ctx)`` (plano 09).

    Unlike ``ToolContext`` (per tool call), this is a per-plugin, long-lived
    object created once when the plugin's lifecycle runs. It exposes the event
    loop, a DB opener, the broadcast bridge, and a VS Code-style disposable
    registry: ``ctx.on_unload(fn)`` stacks cleanups run in reverse order at
    teardown — even if ``setup`` later fails (Home Assistant principle).

    ``spawn_task`` / ``spawn_subprocess`` are reserved here (plano 09 Fase 3/5):
    they delegate to the runtime supervisor / subprocess service once wired.
    """

    plugin_id: str
    handler: Optional[Any] = None
    loop: Optional[asyncio.AbstractEventLoop] = None
    plugin_db: Optional[Callable[[], Any]] = None
    broadcast: Optional[Callable[[str, dict], None]] = None
    # Channel runtime (plano 13 Fase 1.1) — populated from the wired module
    # globals by the lifecycle manager. A channel-provider plugin uses these to
    # register/find live channels and to push inbound events through the shared
    # orchestrator. ``ingest_event`` is an ``async`` callable: ``await
    # ctx.ingest_event(InboundEvent(...))``.
    channel_registry: Optional[Any] = None
    outbound_router: Optional[Any] = None
    ingest_event: Optional[Callable[[Any], Any]] = None
    # ServerDeps (plano 13 Fase 1.2) — populated from the wired module global by the
    # lifecycle manager. A first-party lifecycle plugin (GOWA) uses it to own the
    # subprocess + polling loops. None in tests / zero-channel boots.
    deps: Optional[Any] = None
    _disposables: list = dataclasses.field(default_factory=list, repr=False)

    def on_unload(self, fn: Callable[[], Any]) -> None:
        """Register a cleanup callable (sync or async), run in reverse at teardown."""
        if callable(fn):
            self._disposables.append(fn)

    async def _run_disposables(self) -> None:
        """Run registered cleanups in reverse (LIFO). Each is isolated."""
        while self._disposables:
            fn = self._disposables.pop()
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:  # noqa: BLE001 — one bad cleanup must not block others
                logger.warning("plugin %s on_unload cleanup failed: %s", self.plugin_id, e)

    def spawn_task(self, name, coro_factory, *, policy=None, max_restarts=3,
                   window_sec=60.0):
        """Register + start a supervised background task owned by this plugin.

        Call only from inside ``setup(ctx)`` (a loop is running and ``owner`` is
        set). The task is auto-stopped on disable/teardown via ``stop_owner``.
        Returns the fully-qualified task name (``<plugin_id>:<name>``).
        """
        if _supervisor is None:
            raise RuntimeError("runtime supervisor not wired (plano 09 Fase 5)")
        from runtime.supervisor import TaskSpec, RestartPolicy
        full = f"{self.plugin_id}:{name}"
        spec = TaskSpec(
            name=full, coro_factory=coro_factory,
            policy=policy or RestartPolicy.PERMANENT,
            max_restarts=max_restarts, window_sec=window_sec, owner=self.plugin_id,
        )
        _supervisor.register(spec)
        loop = self.loop or _loop
        if loop is not None:
            loop.create_task(_supervisor.start(full))
        return full

    def spawn_subprocess(self, spec):
        """Spawn a managed subprocess owned by this plugin (auto-stopped on teardown).

        ``spec`` is a ``runtime.subprocess_service.SubprocessSpec``; ``owner`` is
        forced to this plugin's id. May block briefly during spawn/readiness.
        """
        if _subprocess_service is None:
            raise RuntimeError("subprocess service not wired (plano 09 Fase 5)")
        spec.owner = self.plugin_id
        return _subprocess_service.spawn(spec)


@dataclasses.dataclass
class PromptContext:
    """Context passed to a prompt fragment callable.

    A prompt fragment is ``Callable[[ContactMemory, PromptContext], str]``.
    Returning an empty string means "do not inject anything for this fragment".
    """

    handler: "AgentHandler"
    plugin_id: Optional[str] = None
    plugin_db: Optional[Callable[[], Any]] = None


@dataclasses.dataclass
class EventContext:
    """Context passed to a plugin event handler.

    The handler signature is ``def on_event(ctx: EventContext, payload: dict)``
    (sync or ``async``). ``event_name`` echoes the dispatched event so a single
    handler reused via ``EVENT_HANDLERS = {"*": fn}`` can branch on it.
    ``emitted_at`` is the wall time at which the bus dispatched, useful for
    end-to-end latency probing.
    """

    handler: Optional[Any] = None
    plugin_id: Optional[str] = None
    plugin_db: Optional[Callable[[], Any]] = None
    event_name: str = ""
    emitted_at: float = 0.0


@dataclasses.dataclass
class FilterContext:
    """Context passed to a plugin filter.

    A filter signature is ``def fn(ctx: FilterContext, value) -> value | None``
    (sync or ``async``). Returning ``None`` aborts the wrapped action; any
    other return becomes the input for the next filter in the chain.
    ``extras`` is filled by the producer with call-site-specific data
    (e.g. the contact phone for ``filter.message.before_save``).
    """

    handler: Optional[Any] = None
    plugin_id: Optional[str] = None
    plugin_db: Optional[Callable[[], Any]] = None
    filter_name: str = ""
    emitted_at: float = 0.0
    extras: dict = dataclasses.field(default_factory=dict)
