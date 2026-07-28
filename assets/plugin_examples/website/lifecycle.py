"""Website plugin lifecycle (plano 46 · 05.3).

No background loop to run — inbound is browser-driven and outbound is pushed by
``WebsiteChannel.send_text``. ``setup`` just hands the WS hub the running event
loop (so ``deliver`` from a worker thread can schedule the push even before the
first socket connects), and registers a teardown that closes every open visitor
socket when the plugin is disabled (the restart requirement of the plugin system).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def setup(ctx) -> None:
    try:
        from whatsbot_plugins.website.bridge import hub
    except Exception as e:  # noqa: BLE001
        logger.warning("website: could not load WS hub (%s)", e)
        return
    loop = getattr(ctx, "loop", None)
    if loop is not None:
        hub.set_loop(loop)
    # Close every open visitor socket on disable/teardown (LIFO disposable).
    ctx.on_unload(lambda: hub.close_all())
    logger.info("website: widget hub ready (plugin_id=%s)", ctx.plugin_id)


async def teardown(ctx) -> None:
    logger.info("website: teardown (plugin_id=%s)", ctx.plugin_id)
