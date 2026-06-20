"""Runtime foundation validation plugin (plano 09 Fase 6).

Exercises (i) the awaited plugin lifecycle and (ii) the task supervisor — without
any subprocess — exactly as docs-pesquisa 02 §3.4.8 recommends ("validate (i)+(ii)
on a cheap provider before touching GOWA").

On setup it:
  - registers an ``on_unload`` cleanup (logs "teardown ok" — proves teardown runs
    and is awaited, even with an intentional small delay), and
  - spawns a supervised ``heartbeat`` task that broadcasts ``runtime_probe_tick``
    every few seconds (proves spawn_task + the supervisor restart loop).

Manual validation:
  - Enable → setup runs; task shows as ``running`` in GET /api/runtime/tasks; ticks
    arrive on the WS.
  - Raise inside the loop → supervisor relaunches with backoff (restarts++).
  - Disable → teardown runs ("teardown ok"); task leaves the supervisor
    (stop_owner) with no orphan.
"""

import asyncio
import logging

logger = logging.getLogger("plugin.runtime_probe")

TICK_INTERVAL_SECONDS = 5.0


def setup(ctx):
    """Called + awaited by the host once the bus/loop are live."""
    logger.info("runtime_probe: setup() running")

    async def _teardown_marker():
        # A small intentional delay so an observer can SEE that shutdown waits
        # for teardown (plano 09 Fase 1 critério de pronto).
        await asyncio.sleep(0.2)
        logger.info("runtime_probe: teardown ok")

    ctx.on_unload(_teardown_marker)

    tick = {"n": 0}

    async def _heartbeat():
        while True:
            tick["n"] += 1
            try:
                if ctx.broadcast:
                    ctx.broadcast("runtime_probe_tick", {"n": tick["n"]})
            except Exception as e:  # never let a bad broadcast kill the loop
                logger.debug("runtime_probe tick broadcast failed: %s", e)
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    ctx.spawn_task("heartbeat", _heartbeat)
    logger.info("runtime_probe: heartbeat task spawned")


def teardown(ctx):
    """Optional explicit teardown (on_unload already covers cleanup)."""
    logger.info("runtime_probe: teardown() called")
