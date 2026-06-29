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

# Imported at MODULE level (not inside plugin_permission) so the ``request: Request``
# annotation on its inner dependency resolves under ``from __future__ import
# annotations`` — otherwise FastAPI can't evaluate the forward ref and treats
# ``request`` as a required QUERY param (HTTP 422 on every gated plugin route).
from fastapi import Depends, HTTPException, Request

if TYPE_CHECKING:
    from agent.handler import AgentHandler
    from agent.memory import ContactMemory, TagRegistry

logger = logging.getLogger(__name__)


# ── PluginRuntime: the single object that holds the wired services ─────────
#
# Everything the core wires into the plugin layer at server startup lives on ONE
# ``PluginRuntime`` instance (the process-wide ``_runtime`` singleton) instead of
# ~8 scattered module globals. The ``set_*`` functions mutate ``_runtime``;
# ``broadcast`` / ``spawn_*`` / the getters read from it. The public surface is
# unchanged — ``from plugins.context import broadcast, make_plugin_db, set_runtime,
# set_deps, get_deps, get_channel_runtime`` all still resolve and behave the same.
#
# Backwards-compat: a few consumers (this module's ``spawn_*``, and
# ``plugins.lifecycle._stop_owned`` via ``getattr(context, "_supervisor", None)``)
# historically read the old module-level names (``_loop``, ``_ws_manager``,
# ``_supervisor``, ``_subprocess_service``, ``_channel_registry``, …). A module
# ``__getattr__`` (PEP 562) maps those names onto the live ``_runtime`` fields, so
# no stale copies exist and the legacy reads keep working.
#
# Field meanings (unchanged from the old globals):
# * ws_manager / loop — the thread-safe ``broadcast`` bridge (a plugin tool runs
#   in ``asyncio.to_thread`` and schedules a WS push on the main loop).
# * supervisor / subprocess_service — plano 09 Fase 5: ``PluginContext.spawn_*``
#   delegate here.
# * channel_registry / outbound_router / ingest_event — plano 13 Fase 1.1: a
#   channel-provider plugin registers/looks up live channels and pushes inbound
#   through the SAME orchestrator the GOWA webhook uses.
# * deps — plano 13 Fase 1.2: the ServerDeps a first-party lifecycle plugin (GOWA)
#   reads to own its subprocess + polling loops. None in the test harness.


@dataclasses.dataclass
class PluginRuntime:
    """Single container for every service the core wires into the plugin layer."""

    ws_manager: Optional[Any] = None
    loop: Optional[asyncio.AbstractEventLoop] = None
    supervisor: Optional[Any] = None
    subprocess_service: Optional[Any] = None
    channel_registry: Optional[Any] = None
    outbound_router: Optional[Any] = None
    ingest_event: Optional[Any] = None
    deps: Optional[Any] = None


# Process-wide singleton. Mutated in place by the ``set_*`` wiring functions so a
# late import of ``broadcast``/``spawn_*`` always sees the current wiring.
_runtime = PluginRuntime()


# Map the legacy private module-global names onto the live runtime fields so any
# consumer still doing ``context._supervisor`` / ``getattr(context, "_loop", …)``
# reads the current value (PEP 562 module __getattr__).
_LEGACY_RUNTIME_ALIASES = {
    "_ws_manager": "ws_manager",
    "_loop": "loop",
    "_supervisor": "supervisor",
    "_subprocess_service": "subprocess_service",
    "_channel_registry": "channel_registry",
    "_outbound_router": "outbound_router",
    "_ingest_event": "ingest_event",
    "_deps": "deps",
}


def __getattr__(name: str) -> Any:  # noqa: N807 — module-level PEP 562 hook
    field = _LEGACY_RUNTIME_ALIASES.get(name)
    if field is not None:
        return getattr(_runtime, field)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def set_runtime(ws_manager: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Called once during server startup. Plugins read these via ``broadcast``."""
    _runtime.ws_manager = ws_manager
    _runtime.loop = loop


def set_runtime_services(supervisor: Any, subprocess_service: Any) -> None:
    """Wire the task supervisor + subprocess service (plano 09 Fase 5)."""
    _runtime.supervisor = supervisor
    _runtime.subprocess_service = subprocess_service


def set_channel_runtime(channel_registry: Any, outbound_router: Any,
                        ingest_event: Any) -> None:
    """Wire the channel runtime for plugin providers (plano 13 Fase 1.1).

    Called once at lifespan, BEFORE plugin ``setup()`` runs, so a channel plugin
    can materialise its live channels and feed inbound through ``ctx.ingest_event``.
    """
    _runtime.channel_registry = channel_registry
    _runtime.outbound_router = outbound_router
    _runtime.ingest_event = ingest_event


def get_channel_runtime() -> tuple:
    """Return the wired ``(channel_registry, outbound_router, ingest_event)``.

    Reads the live runtime (re-bound by :func:`set_channel_runtime`), so a
    consumer always sees the current wiring rather than a stale import-time copy.
    """
    return (_runtime.channel_registry, _runtime.outbound_router,
            _runtime.ingest_event)


def set_deps(deps: Any) -> None:
    """Wire the server ``ServerDeps`` for first-party lifecycle plugins (plano 13).

    Called once at lifespan, BEFORE plugin ``setup()`` runs, so the GOWA plugin
    can reach gowa_manager/gowa_client/ws_manager/state/settings to own its
    subprocess + polling loops. Other plugins ignore it.
    """
    _runtime.deps = deps


def get_deps() -> Optional[Any]:
    """Return the wired ``ServerDeps`` (None until :func:`set_deps`, e.g. in tests)."""
    return _runtime.deps


def broadcast(event: str, data: dict) -> None:
    """Best-effort WS broadcast from any thread. Never raises."""
    ws_manager = _runtime.ws_manager
    loop = _runtime.loop
    if ws_manager is None or loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            ws_manager.broadcast(event, data), loop
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


# ── System-notice registry for plugins (plano 23 Fase C3) ─────────────────
#
# A plugin can add its OWN conversation-event notice types/groups without
# patching ``server.system_notices`` core dicts. These are thin re-exports that
# delegate to the registry there (late import — ``system_notices`` imports
# ``broadcast`` from this module, so importing it at module top would cycle).
#
#     from plugins.context import register_notice_group, register_notice
#     register_notice_group("my_plugin", "Meu plugin")
#     register_notice("my_event", "my_plugin", lambda **ctx: f"... {ctx['x']}")


def register_notice_group(key: str, label: str, *, config_key: str | None = None,
                          default: bool = True) -> None:
    """Register a system-notice GROUP (config gate). See system_notices.register_notice_group."""
    from server import system_notices
    system_notices.register_notice_group(
        key, label, config_key=config_key, default=default)


def register_notice(event_type: str, group: str, formatter: Callable[..., str]) -> None:
    """Register a system-notice TYPE → group + formatter. See system_notices.register_notice."""
    from server import system_notices
    system_notices.register_notice(event_type, group, formatter)


# ── RBAC dependency for plugin routes (plano "RBAC para Plugins" §3.5) ─────
import re as _re

_PLUGIN_PATH_RE = _re.compile(r"^/api/plugins/([a-z][a-z0-9_]{0,31})(?:/|$)")


def plugin_permission(key: str):
    """FastAPI dependency that gates a plugin route on ``plugin.<id>.<key>``.

    The plugin id is inferred from the request path (``/api/plugins/<id>/...``),
    so the plugin does not need to know its own id. Returns 403 when a logged-in
    user lacks the permission; default-allow for legacy/open installs (no user
    identity) and when RBAC isn't enforced. The ABAC seam (``filter.authz.decision``)
    is honored via :func:`server.authz.acheck`.

    Usage in a plugin ``routes.py``::

        from plugins.context import plugin_permission

        @router.delete("/items/{id}", dependencies=[plugin_permission("delete")])
        async def delete_item(id: int): ...
    """

    async def _dep(request: Request) -> None:
        from server.authz import acheck
        m = _PLUGIN_PATH_RE.match(request.url.path)
        if not m:
            return  # not a plugin-namespaced path — cannot infer id, allow.
        full_key = f"plugin.{m.group(1)}.{key}"
        if not await acheck(request, full_key):
            raise HTTPException(status_code=403, detail="Permissão negada.")

    return Depends(_dep)


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
        supervisor = _runtime.supervisor
        if supervisor is None:
            raise RuntimeError("runtime supervisor not wired (plano 09 Fase 5)")
        from runtime.supervisor import TaskSpec, RestartPolicy
        full = f"{self.plugin_id}:{name}"
        spec = TaskSpec(
            name=full, coro_factory=coro_factory,
            policy=policy or RestartPolicy.PERMANENT,
            max_restarts=max_restarts, window_sec=window_sec, owner=self.plugin_id,
        )
        supervisor.register(spec)
        loop = self.loop or _runtime.loop
        if loop is not None:
            loop.create_task(supervisor.start(full))
        return full

    def spawn_subprocess(self, spec):
        """Spawn a managed subprocess owned by this plugin (auto-stopped on teardown).

        ``spec`` is a ``runtime.subprocess_service.SubprocessSpec``; ``owner`` is
        forced to this plugin's id. May block briefly during spawn/readiness.
        """
        subprocess_service = _runtime.subprocess_service
        if subprocess_service is None:
            raise RuntimeError("subprocess service not wired (plano 09 Fase 5)")
        spec.owner = self.plugin_id
        return subprocess_service.spawn(spec)


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
