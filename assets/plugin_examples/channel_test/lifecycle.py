"""Plugin lifecycle for the ``channel_test`` fixture (plano 02 Fase 1 / plano 09).

This module exists to PROVE the awaited plugin lifecycle + the task supervisor
work end to end:

- ``setup(ctx)`` registers ONE supervised background task (a periodic heartbeat
  loop) through the REAL context API ``ctx.spawn_task(name, coro_factory, ...)``
  (see ``plugins/context.py`` :class:`PluginContext.spawn_task`, which delegates
  to ``runtime.supervisor.TaskSupervisor`` exactly like the four core tasks in
  ``server/app.py``). It also registers a cleanup via ``ctx.on_unload(...)`` —
  the VS Code-style disposable registry — so we can observe the unwind.
- ``teardown(ctx)`` just logs. The supervised task is auto-cancelled by the
  lifecycle manager (``_stop_owned`` -> ``supervisor.stop_owner(plugin_id)``),
  and ``ctx.on_unload`` disposables run in reverse — no orphan tasks remain.

Discovered API (read from the core, NOT invented):
- ``ctx`` is :class:`plugins.context.PluginContext`, built at
  ``plugins/lifecycle.py`` (``_ensure_context``). Real attributes: ``plugin_id``,
  ``loop``, ``plugin_db``, ``broadcast``, ``on_unload(fn)``, ``spawn_task(...)``,
  ``spawn_subprocess(spec)``.
- There is NO ``register_task`` / ``register_cleanup`` / ``stop_event`` on the
  ctx — the real names are ``spawn_task`` and ``on_unload``.
- ``spawn_task`` requires the runtime supervisor to be wired (``set_runtime_services``
  at lifespan). If it is not wired it raises ``RuntimeError``; we degrade
  gracefully to a log-only setup so ``run_setup``/``run_teardown`` are still
  exercised.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Heartbeat cadence. Kept short relative to a human watch but long enough that
# logs are not spammy. ``asyncio.sleep`` is the cancellation point.
HEARTBEAT_INTERVAL_SEC = 30.0


async def _heartbeat_loop() -> None:
    """Supervised background coroutine: log/broadcast a heartbeat forever.

    Runs under ``TaskSupervisor._guard`` (PERMANENT policy). Cancellation
    (on disable/teardown) surfaces as ``CancelledError`` from ``asyncio.sleep``;
    we log a clean exit and re-raise so the supervisor records ``stopped`` and
    no task is left orphaned.
    """
    logger.info("channel_test heartbeat loop started")
    beats = 0
    try:
        while True:
            beats += 1
            logger.info("channel_test heartbeat (#%d)", beats)
            # Best-effort real-time signal; there is no plugin screen, so this is
            # purely to exercise the broadcast bridge from a supervised task.
            try:
                from plugins.context import broadcast
                broadcast("channel_test.heartbeat", {"beats": beats})
            except Exception:  # noqa: BLE001 — broadcast is best-effort
                pass
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
    except asyncio.CancelledError:
        logger.info("channel_test heartbeat loop cancelled cleanly (beats=%d)", beats)
        raise


async def setup(ctx) -> None:
    """Register the supervised heartbeat task via the real ctx API."""
    logger.info("channel_test setup() running (plugin_id=%s)", ctx.plugin_id)

    # Cleanup observable at teardown (runs in reverse / LIFO). Registered FIRST
    # so it still runs even if spawn_task below raises (Home Assistant principle:
    # cleanups registered before a setup failure are run by run_setup).
    def _on_unload() -> None:
        logger.info("channel_test on_unload disposable fired")
    ctx.on_unload(_on_unload)

    # Register ONE supervised background task. ``spawn_task`` mirrors the core's
    # ``supervisor.register(TaskSpec(...))`` + ``supervisor.start(...)`` pattern
    # (see server/app.py) but scoped to this plugin as ``owner`` so teardown's
    # ``stop_owner(plugin_id)`` cancels exactly this task — no orphans.
    try:
        full_name = ctx.spawn_task("heartbeat", _heartbeat_loop)
        logger.info("channel_test registered supervised task %r", full_name)
    except RuntimeError as e:
        # Runtime supervisor not wired (e.g. older host / unit test without
        # set_runtime_services). Degrade to log-only so run_setup still succeeds
        # and the lifecycle path is exercised.
        logger.warning(
            "channel_test: supervisor not wired (%s); running log-only setup. "
            "If you see this in a normally-booted server, plano 02 Fase 1 needs "
            "set_runtime_services wired before plugin setup runs.", e
        )


async def teardown(ctx) -> None:
    """Log teardown. Task cancellation + disposables are handled by the manager."""
    logger.info("channel_test teardown() running (plugin_id=%s)", ctx.plugin_id)
    # NOTE: we intentionally do NOT cancel the task here — the lifecycle manager
    # calls supervisor.stop_owner(plugin_id) (via _stop_owned) and then runs the
    # ctx.on_unload disposables. Doing it here would duplicate that work.
