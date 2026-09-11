"""Plugin→plugin service registry (in-process, NEVER over HTTP).

Sibling of :mod:`plugins.events`: registration + validation + dispatch in a
single module. Where the bus is *broadcast* ("something happened") and filters
are *interceptive* ("rewrite this value"), services are **request/response**:
one plugin publishes a named surface of operations and another plugin calls it
and reads the answer back.

Modelled on the seam the frontend already has —
``web/static/js/plugins/api.js`` ``buildPluginApi(pluginId, pluginServicesRange)``
with its allowlist + version negotiation. This is the Python sibling, not a new
invention.

Contract, in one paragraph:

* A provider exports ``SERVICES = {"op": callable, ...}`` (plus optional
  ``SERVICES_VERSION`` / ``SERVICES_ALLOW``) from the module declared as
  ``entry.services`` in its manifest.
* A consumer calls ``services.call("provider", "op", _as="me", **kwargs)`` and
  ALWAYS gets a :class:`ServiceResult` envelope back. **Dispatch never raises**
  — a missing plugin, an unknown op, an incompatible version range, a disabled
  feature or a crashing implementation all come back as a status.
* ``get()`` returns a null-object :class:`ServiceProxy`, never ``None``, so
  feature detection is ``if services.get("trackify"):`` and a miss degrades
  cleanly instead of exploding at the call site.

**Invisible to HTTP — three guarantees** (all covered by tests): this module
does not import ``fastapi``; ``loader._entry_services`` never touches
``loaded.router``; and no provider exposes an ``/rpc`` or ``/service/{op}``
route. The security boundary is "nothing leaves the process" — see the note on
``as_plugin`` in :func:`get`.

Auditing is NOT done here: the registry knows no semantics and some ops are
high volume. The PROVIDER audits, per operation with an external effect
(``plugins.context.audit``), following the ``docs/PLUGINS_AUDITAVEIS.md`` rule
(summarised in ``CLAUDE.md`` §"Auditoria e RBAC").
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Verdict taxonomy ─────────────────────────────────────────────────────
#
# Mirrors the vocabulary ``trackify/client.py`` already publishes: a caller
# branches on a small closed set of strings, never on an exception type.
OK = "ok"                       # the op ran; ``data`` carries its return value
UNAVAILABLE = "unavailable"     # no such plugin loaded / no such surface
UNKNOWN_OP = "unknown_op"       # plugin is there, that operation is not
INCOMPATIBLE = "incompatible"   # provider version outside the caller's range
DISABLED = "disabled"           # loaded but switched off / no credential
WRONG_CONTEXT = "wrong_context"  # sync call of an async op on the loop thread
ERROR = "error"                 # the implementation raised

# Automatic op every provider answers without implementing anything.
META_OP = "__meta__"


class ServiceDisabled(RuntimeError):
    """Raised by an op meaning "I am loaded but switched off".

    Turned into a ``DISABLED`` envelope — never propagated to the caller. A
    provider that wants to stay importable on an older core may define its own
    local class with this exact NAME; dispatch also matches by class name.
    """


@dataclasses.dataclass(frozen=True)
class ServiceResult:
    """Uniform envelope returned by every service call."""

    status: str
    data: Any = None
    error: str = ""
    plugin_id: str = ""
    op: str = ""
    version: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK

    def __bool__(self) -> bool:  # ``if res:`` reads as "did it work?"
        return self.ok


@dataclasses.dataclass
class _Provider:
    plugin_id: str
    services: dict[str, Callable]
    version: str = "1.0"
    allow: tuple = ()


# plugin_id → provider record
_providers: dict[str, _Provider] = {}
# consumer plugin_id → {provider_id: version range} (from the manifest)
_uses: dict[str, dict[str, str]] = {}


# ── Validation (single source — mirrors validate_event_handlers) ─────────


def validate_services(plugin_id: str, raw: Any) -> dict[str, Callable]:
    """Return the valid ``{op: callable}`` subset of an exported SERVICES dict.

    Non-dict input warns and yields ``{}``; non-callable entries warn and are
    skipped. Never raises — a malformed export must not stop the plugin from
    loading.
    """
    if not raw:  # None / empty dict — nothing exported, no warning
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            "Plugin %s: SERVICES must be a dict, got %s",
            plugin_id, type(raw).__name__,
        )
        return {}
    out: dict[str, Callable] = {}
    for name, fn in raw.items():
        op = str(name)
        if op == META_OP:
            logger.warning(
                "Plugin %s: SERVICES[%r] is reserved (answered by the registry), skipped",
                plugin_id, op,
            )
            continue
        if callable(fn):
            out[op] = fn
        else:
            logger.warning(
                "Plugin %s: SERVICES[%r] is not callable, skipped", plugin_id, op,
            )
    return out


# ── Registration ─────────────────────────────────────────────────────────


def register_plugin_services(
    plugin_id: str,
    services: dict[str, Callable],
    *,
    version: str = "1.0",
    allow: Optional[tuple] = None,
) -> None:
    """Publish ``services`` under ``plugin_id``.

    ``version`` is the provider's own service-surface version (it lives in the
    provider's code, not in the manifest, so the two can't drift). ``allow``
    is an optional tuple of consumer plugin ids; empty/None = any loaded plugin.
    """
    valid = validate_services(plugin_id, services)
    if not valid:
        return
    _providers[plugin_id] = _Provider(
        plugin_id=plugin_id,
        services=valid,
        version=str(version or "1.0"),
        allow=tuple(allow) if allow else (),
    )


def register_plugin_uses(plugin_id: str, uses: Any) -> None:
    """Record a consumer's ``uses_services`` manifest block.

    Shape after ``manifest._parse_uses_services``: ``[{"plugin": str,
    "version": str}, ...]``. It supplies the DEFAULT version range for calls
    made with ``_as=<plugin_id>`` and no explicit ``requires=``.
    """
    ranges: dict[str, str] = {}
    for entry in uses or ():
        if not isinstance(entry, dict):
            continue
        target = str(entry.get("plugin") or "").strip()
        if not target:
            continue
        ranges[target] = str(entry.get("version") or "*")
    if ranges:
        _uses[plugin_id] = ranges
    else:
        _uses.pop(plugin_id, None)


def unregister_plugin(plugin_id: str) -> None:
    """Drop a plugin's published surface AND its consumer declarations."""
    _providers.pop(plugin_id, None)
    _uses.pop(plugin_id, None)


def reset() -> None:
    """Clear the whole registry. For tests only."""
    _providers.clear()
    _uses.clear()


def describe() -> dict:
    """``{plugin_id: {"version": str, "ops": [str, ...]}}`` — in-process only.

    Deliberately NOT exposed over HTTP (``/api/plugins/manifest`` is a public
    payload; this surface is internal).
    """
    return {
        pid: {"version": p.version, "ops": sorted(p.services)}
        for pid, p in _providers.items()
    }


# ── Version negotiation ──────────────────────────────────────────────────


def _resolve_range(plugin_id: str, requires: str, as_plugin: Optional[str]) -> str:
    """Explicit ``requires`` → caller's ``uses_services`` entry → ``"*"``."""
    if requires and requires != "*":
        return requires
    if as_plugin:
        declared = _uses.get(as_plugin, {}).get(plugin_id)
        if declared:
            return declared
    return requires or "*"


def _compatible(range_spec: str, current: str) -> bool:
    from plugins.semver import check_api_compat  # late import, no cycle
    try:
        return bool(check_api_compat(range_spec, current=current))
    except Exception:
        return False


# ── Proxy ────────────────────────────────────────────────────────────────


class ServiceProxy:
    """Handle on one plugin's service surface. NULL-OBJECT: never ``None``.

    ``bool(proxy)`` is the feature-detection gate: a plugin that isn't loaded,
    isn't compatible or isn't allowed yields a falsy proxy whose ``call()``
    still answers with the matching status instead of raising.

    Ops are deliberately NOT attributes — "unavailable" has to answer
    uniformly, not with ``AttributeError``.
    """

    __slots__ = ("plugin_id", "available", "version", "ops", "_status", "_error",
                 "_as_plugin")

    def __init__(self, plugin_id: str, *, provider: Optional[_Provider],
                 status: str, error: str = "", as_plugin: Optional[str] = None) -> None:
        self.plugin_id = plugin_id
        self.available = status == OK
        self.version = provider.version if provider else ""
        self.ops: tuple = tuple(sorted(provider.services)) if provider else ()
        self._status = status
        self._error = error
        self._as_plugin = as_plugin

    def __bool__(self) -> bool:
        return self.available

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return (f"<ServiceProxy {self.plugin_id} status={self._status} "
                f"version={self.version!r} ops={len(self.ops)}>")

    # -- lookup ----------------------------------------------------------

    def _lookup(self, op: str) -> tuple[Optional[Callable], Optional[ServiceResult]]:
        if not self.available:
            return None, self._envelope(self._status, op, error=self._error)
        provider = _providers.get(self.plugin_id)
        if provider is None:  # unregistered between get() and call()
            return None, self._envelope(UNAVAILABLE, op,
                                        error=f"plugin {self.plugin_id!r} is not loaded")
        if op == META_OP:
            return None, self._envelope(
                OK, op, data={"version": provider.version, "ops": sorted(provider.services)})
        fn = provider.services.get(op)
        if fn is None:
            return None, self._envelope(UNKNOWN_OP, op,
                                        error=f"{self.plugin_id!r} has no op {op!r}")
        return fn, None

    def _envelope(self, status: str, op: str, *, data: Any = None,
                  error: str = "") -> ServiceResult:
        return ServiceResult(status=status, data=data, error=error,
                             plugin_id=self.plugin_id, op=op, version=self.version)

    # -- dispatch --------------------------------------------------------

    def call(self, op: str, /, *, _timeout: float = 10.0, **kw) -> ServiceResult:
        """Synchronous call. Safe from any thread; NEVER blocks the event loop.

        An async implementation called from a WORKER thread is bridged onto the
        server loop; called from the LOOP thread it answers ``WRONG_CONTEXT``
        (the degradation ``apply_filter_sync`` already makes, for the same
        reason).
        """
        fn, early = self._lookup(op)
        if early is not None:
            return early
        try:
            if inspect.iscoroutinefunction(fn):
                bridged, result = _run_coroutine_from_sync(fn, kw, _timeout)
                if not bridged:
                    logger.warning(
                        "services: %s.%s is async and was called synchronously from the "
                        "event-loop thread; use acall() there", self.plugin_id, op,
                    )
                    return self._envelope(
                        WRONG_CONTEXT, op,
                        error="async op called synchronously on the event loop thread")
                return self._envelope(OK, op, data=result)
            return self._envelope(OK, op, data=fn(**kw))
        except BaseException as e:  # noqa: BLE001 — total isolation is the contract
            return self._failure(op, e)

    async def acall(self, op: str, /, **kw) -> ServiceResult:
        """Asynchronous call. A sync implementation runs in ``asyncio.to_thread``."""
        fn, early = self._lookup(op)
        if early is not None:
            return early
        try:
            if inspect.iscoroutinefunction(fn):
                data = await fn(**kw)
            else:
                data = await asyncio.to_thread(lambda: fn(**kw))
            return self._envelope(OK, op, data=data)
        except BaseException as e:  # noqa: BLE001
            return self._failure(op, e)

    def _failure(self, op: str, exc: BaseException) -> ServiceResult:
        # ``ServiceDisabled`` by class OR by class NAME: a provider that must stay
        # importable on an older core defines its own local fallback class.
        if isinstance(exc, ServiceDisabled) or type(exc).__name__ == "ServiceDisabled":
            return self._envelope(DISABLED, op, error=str(exc))
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise exc
        logger.warning(
            "services: %s.%s raised %s: %s", self.plugin_id, op, type(exc).__name__, exc,
        )
        return self._envelope(ERROR, op, error=f"{type(exc).__name__}: {exc}")


def _run_coroutine_from_sync(fn: Callable, kw: dict,
                             timeout: float) -> tuple[bool, Any]:
    """Run an async op from synchronous code. ``(bridged, result)``.

    ``bridged=False`` means "we are ON an event-loop thread" — blocking there
    would deadlock the server, so the caller answers ``WRONG_CONTEXT``.
    """
    try:
        asyncio.get_running_loop()
        return False, None  # on a loop thread — refuse, never block
    except RuntimeError:
        pass
    from plugins.context import get_loop  # late import to avoid a cycle
    loop = get_loop()
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(fn(**kw), loop)
        return True, future.result(timeout=timeout)
    # No server loop wired (tests, pre-lifespan): run it to completion here.
    return True, asyncio.run(fn(**kw))


# ── Module-level API ─────────────────────────────────────────────────────


def get(plugin_id: str, *, requires: str = "*",
        as_plugin: Optional[str] = None) -> ServiceProxy:
    """Negotiate once and return a reusable :class:`ServiceProxy` (never ``None``).

    Version range order: explicit ``requires`` → the caller's ``uses_services``
    manifest entry (looked up by ``as_plugin``) → ``"*"``. An incompatible
    range yields a falsy proxy whose ``call()`` answers ``INCOMPATIBLE``.

    ⚠️ ``as_plugin`` is supplied BY THE CALLER and is therefore FALSIFIABLE. It
    is blast-radius accounting (which plugin asked for what), NOT a security
    boundary. The real boundary is the requirement that nothing leaves the
    process: this surface is never reachable over HTTP.
    """
    provider = _providers.get(plugin_id)
    if provider is None:
        return ServiceProxy(plugin_id, provider=None, status=UNAVAILABLE,
                            error=f"plugin {plugin_id!r} exposes no services",
                            as_plugin=as_plugin)
    if provider.allow and (as_plugin or "") not in provider.allow:
        return ServiceProxy(
            plugin_id, provider=provider, status=UNAVAILABLE,
            error=f"{plugin_id!r} does not allow calls from {as_plugin!r}",
            as_plugin=as_plugin)
    range_spec = _resolve_range(plugin_id, requires, as_plugin)
    if not _compatible(range_spec, provider.version):
        return ServiceProxy(
            plugin_id, provider=provider, status=INCOMPATIBLE,
            error=(f"{plugin_id} services are {provider.version}, "
                   f"caller requires {range_spec}"),
            as_plugin=as_plugin)
    return ServiceProxy(plugin_id, provider=provider, status=OK, as_plugin=as_plugin)


def call(plugin_id: str, op: str, /, *, _requires: str = "*",
         _as: Optional[str] = None, _timeout: float = 10.0, **kw) -> ServiceResult:
    """One-liner sibling of ``get(...).call(...)``. Never raises."""
    return get(plugin_id, requires=_requires, as_plugin=_as).call(
        op, _timeout=_timeout, **kw)


async def acall(plugin_id: str, op: str, /, *, _requires: str = "*",
                _as: Optional[str] = None, **kw) -> ServiceResult:
    """One-liner sibling of ``await get(...).acall(...)``. Never raises."""
    return await get(plugin_id, requires=_requires, as_plugin=_as).acall(op, **kw)


def available(plugin_id: str, *, requires: str = "*",
              as_plugin: Optional[str] = None) -> bool:
    """True when the plugin publishes a compatible, reachable surface."""
    return bool(get(plugin_id, requires=requires, as_plugin=as_plugin))
