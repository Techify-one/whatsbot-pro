"""Background task supervisor (plano 09, Fase 3).

Generalizes the four background coroutines that used to live as a hardcoded list
in the server lifespan into a registry where core and plugins register
long-running coroutines, with classified restart (Erlang/OTP-style
permanent/transient/temporary) and rate-limited exponential backoff.

Stop mechanism is ``task.cancel()`` + ``await`` (P27) — the supervisor cancels
the guard task and waits for it, fixing the old gap where the lifespan cancelled
tasks without awaiting them. Legacy coroutines may still observe
``state.stop_event`` during the transition; the supervisor does not rely on it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class RestartPolicy(Enum):
    PERMANENT = "permanent"   # always relaunch (default for provider loops)
    TRANSIENT = "transient"   # relaunch only if it exited via exception, not normal return
    TEMPORARY = "temporary"   # never relaunch


@dataclass
class TaskSpec:
    name: str
    coro_factory: Callable[[], Awaitable[None]]   # builds a NEW coroutine on each (re)start
    policy: RestartPolicy = RestartPolicy.PERMANENT
    max_restarts: int = 3
    window_sec: float = 60.0
    backoff_base: float = 2.0       # exponential backoff between restarts
    backoff_cap: float = 30.0       # ceiling for a single backoff sleep
    owner: str = "core"             # "core" or plugin_id


@dataclass
class _TaskState:
    spec: TaskSpec
    task: Optional[asyncio.Task] = None
    state: str = "registered"       # registered|running|crashed|stopped|completed
    restarts: int = 0
    last_error: str = ""
    _restart_times: deque = field(default_factory=deque)


class TaskSupervisor:
    """Owns long-running coroutines and keeps them alive per their restart policy."""

    def __init__(self) -> None:
        self._tasks: dict[str, _TaskState] = {}
        self._stopping = False

    # ── Registration ────────────────────────────────────────────────
    def register(self, spec: TaskSpec) -> None:
        if spec.name in self._tasks:
            logger.warning("TaskSupervisor: task %r already registered; replacing", spec.name)
        self._tasks[spec.name] = _TaskState(spec=spec)

    # ── Start ───────────────────────────────────────────────────────
    async def start_all(self) -> None:
        self._stopping = False
        for name in list(self._tasks):
            await self.start(name)

    async def start(self, name: str) -> None:
        ts = self._tasks.get(name)
        if ts is None:
            raise KeyError(f"unknown task {name!r}")
        if ts.task is not None and not ts.task.done():
            return  # already running
        ts.state = "running"
        ts.task = asyncio.create_task(self._guard(ts), name=f"supervised:{name}")

    # ── Guard loop ──────────────────────────────────────────────────
    async def _guard(self, ts: _TaskState) -> None:
        spec = ts.spec
        while True:
            try:
                await spec.coro_factory()
                # Normal completion.
                if self._stopping or spec.policy in (RestartPolicy.TRANSIENT, RestartPolicy.TEMPORARY):
                    ts.state = "completed"
                    return
                # PERMANENT: a provider loop that returned is relaunched.
            except asyncio.CancelledError:
                ts.state = "stopped"
                raise
            except Exception as e:  # noqa: BLE001 — supervisor is the safety net
                ts.last_error = f"{type(e).__name__}: {e}"
                logger.warning("Supervised task %r crashed: %s", spec.name, ts.last_error)
                if spec.policy is RestartPolicy.TEMPORARY:
                    ts.state = "crashed"
                    return

            if self._stopping:
                ts.state = "stopped"
                return

            # Rate-limit: more than max_restarts within window_sec → give up.
            now = time.monotonic()
            ts._restart_times.append(now)
            while ts._restart_times and now - ts._restart_times[0] > spec.window_sec:
                ts._restart_times.popleft()
            if len(ts._restart_times) > spec.max_restarts:
                ts.state = "crashed"
                logger.error(
                    "Supervised task %r exceeded %d restarts/%.0fs — giving up",
                    spec.name, spec.max_restarts, spec.window_sec,
                )
                self._emit_crashed(spec, ts.last_error)
                return

            ts.restarts += 1
            backoff = min(spec.backoff_base ** len(ts._restart_times), spec.backoff_cap)
            logger.info("Restarting supervised task %r in %.1fs (restart #%d)",
                        spec.name, backoff, ts.restarts)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                ts.state = "stopped"
                raise
            ts.state = "running"

    @staticmethod
    def _emit_crashed(spec: TaskSpec, error: str) -> None:
        try:
            from plugins.events import emit
            emit("task.crashed", {"name": spec.name, "owner": spec.owner, "error": error})
        except Exception as e:  # never let observability break the supervisor
            logger.debug("emit task.crashed failed: %s", e)

    # ── Stop ────────────────────────────────────────────────────────
    async def stop(self, name: str) -> None:
        ts = self._tasks.get(name)
        if ts is None or ts.task is None:
            return
        await self._cancel_and_wait(ts)

    async def stop_owner(self, owner: str) -> None:
        for ts in list(self._tasks.values()):
            if ts.spec.owner == owner:
                await self._cancel_and_wait(ts)

    async def stop_all(self) -> None:
        self._stopping = True
        for ts in list(self._tasks.values()):
            await self._cancel_and_wait(ts)

    @staticmethod
    async def _cancel_and_wait(ts: _TaskState) -> None:
        task = ts.task
        if task is None or task.done():
            ts.state = "stopped"
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        ts.state = "stopped"

    # ── Introspection ───────────────────────────────────────────────
    def status(self) -> list[dict]:
        return [
            {
                "name": ts.spec.name,
                "owner": ts.spec.owner,
                "state": ts.state,
                "policy": ts.spec.policy.value,
                "restarts": ts.restarts,
                "last_error": ts.last_error,
            }
            for ts in self._tasks.values()
        ]
