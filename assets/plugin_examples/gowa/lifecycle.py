"""GOWA plugin lifecycle (plano 13 Fase 2).

``setup(ctx)`` makes this plugin the OWNER of GOWA's runtime: the subprocess AND
the status/QR/avatar polling loops the core used to register in its lifespan.

- The subprocess is spawned through ``ctx.spawn_subprocess`` so it is owned by
  this plugin (``owner == plugin_id``). Disabling/uninstalling the plugin runs the
  lifecycle manager's ``stop_owner(plugin_id)``, which KILLS the subprocess (not
  just the polling tasks) — the "uninstall a channel → its subprocess dies"
  guarantee. A redundant ``ctx.on_unload(managed.stop)`` is registered as a backstop.
- The polling loops + a one-shot post-spawn init (health-check → device register →
  broadcast) run as supervised tasks via ``ctx.spawn_task`` (also owner-scoped).

The core never registers GOWA's loops once this plugin is loaded
(``'gowa' not in registry.loaded`` gate in ``server/app.py``), so there is exactly
one owner.

Resilience: with no server ``deps`` (test harness — lifespan is skipped) or no
``gowa_manager`` (a zero-channel / non-GOWA boot), ``setup`` is a clean no-op.
The GOWA binary not being present degrades to sandbox-only (logged, not fatal).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


async def _notify_degraded(deps, message: str) -> None:
    """Surface a degraded-GOWA state to the panel (StatusBar) like the old core
    start path did — set state.notification + broadcast ``gowa_status``. Without
    this a missing binary leaves the UI stuck on the default "Iniciando...".
    """
    try:
        deps.state.notification = message
        await deps.ws_manager.broadcast("gowa_status", {"message": message})
    except Exception:  # noqa: BLE001
        pass


async def _post_spawn_init(deps) -> None:
    """One-shot: wait for GOWA readiness, register the device, broadcast status.

    Ported from the core ``start_gowa_task`` MINUS the ``gowa_manager.start()``
    call — the subprocess is already spawned (and owned) via ctx.spawn_subprocess.
    """
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    try:
        for _ in range(10):
            await asyncio.sleep(1)
            if await asyncio.to_thread(gowa_client.health_check):
                break
        if await asyncio.to_thread(gowa_client.ensure_device):
            state.notification = "GOWA pronto, aguardando QR..."
        else:
            state.notification = "Erro ao registrar device no GOWA"
        await ws_manager.broadcast("gowa_status", {"message": state.notification})
    except Exception as e:  # noqa: BLE001
        state.notification = "GOWA indisponível — modo sandbox disponível"
        logger.info("GOWA post-spawn init failed (%s), sandbox-only mode.", e)
        try:
            await ws_manager.broadcast("gowa_status", {"message": state.notification})
        except Exception:  # noqa: BLE001
            pass


async def setup(ctx) -> None:
    deps = getattr(ctx, "deps", None)
    if deps is None or getattr(deps, "gowa_manager", None) is None:
        logger.info("gowa: no server deps / gowa_manager wired — lifecycle setup is a "
                    "no-op (test harness or zero-channel boot)")
        return

    from runtime.subprocess_service import SubprocessSpec
    from runtime.supervisor import RestartPolicy
    from server.background import status_poll_loop, qr_poll_loop, avatar_fetch_task

    gm = deps.gowa_manager
    # Build the exact argv the core used (binary path, --webhook .../gowa/default,
    # --webhook-events, --presence-on-connect, --os, --debug). Reusing the manager's
    # ``_build_cmd`` keeps binary-resolution (sys._MEIPASS) and the webhook-event
    # list in ONE place. ``_build_cmd`` validates the binary exists (raises
    # FileNotFoundError) and places its resolved path at argv[0] — so we read the
    # binary back from ``cmd[0]`` instead of calling a second helper. (Note:
    # ``_get_gowa_binary`` is a MODULE-level function in gowa.manager, NOT a method
    # on GOWAManager — calling ``gm._get_gowa_binary()`` raised AttributeError and
    # bricked the plugin. Deriving from cmd[0] avoids that whole footgun.)
    try:
        cmd = gm._build_cmd()
    except FileNotFoundError as e:
        logger.warning("gowa: binary not found (%s) — sandbox-only mode (no subprocess)", e)
        await _notify_degraded(deps, "GOWA não encontrado — modo sandbox disponível")
        return
    except Exception as e:  # noqa: BLE001 — interface drift must degrade, not brick the channel
        logger.error("gowa: could not build subprocess command (%s) — sandbox-only mode", e)
        await _notify_degraded(deps, "GOWA indisponível — modo sandbox disponível")
        return
    if not cmd:  # defensive: an empty argv would make the signature/spawn meaningless
        logger.error("gowa: _build_cmd() returned empty argv — sandbox-only mode")
        await _notify_degraded(deps, "GOWA indisponível — modo sandbox disponível")
        return
    binary = cmd[0]  # _build_cmd() put the resolved binary path at argv[0]

    # Debug-log parity (WHATSBOT_GOWA_DEBUG): reuse the manager's helpers so
    # /api/gowa-logs keeps working; otherwise DEVNULL (no PIPE — deadlock guard).
    stdout_target = subprocess.DEVNULL
    stderr_target = subprocess.DEVNULL
    try:
        from gowa.manager import _debug_enabled, _gowa_log_path, GOWA_LOG_MAX_BYTES
        if _debug_enabled():
            log_path = _gowa_log_path()
            if log_path.exists() and log_path.stat().st_size > GOWA_LOG_MAX_BYTES:
                log_path.unlink(missing_ok=True)
            _fh = open(log_path, "ab", buffering=0)
            ctx.on_unload(_fh.close)
            stdout_target = _fh
            stderr_target = subprocess.STDOUT
            logger.info("gowa: debug logs -> %s", log_path)
    except Exception:  # noqa: BLE001
        stdout_target = subprocess.DEVNULL
        stderr_target = subprocess.DEVNULL

    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    spec = SubprocessSpec(
        name="gowa",
        cmd=cmd,
        signature=binary,            # stale-kill guard: a leftover GOWA is killed on respawn
        readiness=None,              # readiness handled by _post_spawn_init (health_check loop)
        readiness_timeout=15.0,
        stdout=stdout_target,
        stderr=stderr_target,
        creationflags=creation_flags,
        max_restarts=3,
        window_sec=60.0,
        restart_delay=5.0,
        on_restart=getattr(gm, "_on_restart", None),
    )
    try:
        # Offload to a worker thread: spawn() runs the synchronous stale-kill, which
        # can sleep up to ~5s killing a leftover GOWA. Keeping it off the event loop
        # preserves the old non-blocking bring-up (core used asyncio.to_thread too)
        # so app readiness / other plugins' setup aren't stalled during startup.
        managed = await asyncio.to_thread(ctx.spawn_subprocess, spec)  # owner forced to 'gowa'
    except RuntimeError as e:                   # subprocess service not wired
        logger.warning("gowa: subprocess service not wired (%s); GOWA not started", e)
        await _notify_degraded(deps, "GOWA indisponível — modo sandbox disponível")
        return
    # Redundant backstop: teardown also runs stop_owner('gowa'), but on_unload makes
    # the kill explicit even if ownership bookkeeping ever changes.
    ctx.on_unload(managed.stop)

    try:
        ctx.spawn_task("gowa_init", lambda: _post_spawn_init(deps),
                       policy=RestartPolicy.TRANSIENT)
        ctx.spawn_task("status_poll", lambda: status_poll_loop(deps),
                       policy=RestartPolicy.PERMANENT)
        ctx.spawn_task("qr_poll", lambda: qr_poll_loop(deps),
                       policy=RestartPolicy.PERMANENT)
        ctx.spawn_task("avatar_fetch", lambda: avatar_fetch_task(deps),
                       policy=RestartPolicy.PERMANENT)
    except RuntimeError as e:  # supervisor not wired
        logger.warning("gowa: supervisor not wired (%s); polling loops not started", e)
    logger.info("gowa: lifecycle setup — subprocess spawned (owner=%s) + 3 polling "
                "loops registered", ctx.plugin_id)


async def teardown(ctx) -> None:
    # The owned subprocess + supervised tasks are auto-stopped by the lifecycle
    # manager (stop_owner(plugin_id)); on_unload(managed.stop) is a backstop.
    logger.info("gowa: teardown (plugin_id=%s) — subprocess + tasks auto-stopped",
                ctx.plugin_id)
