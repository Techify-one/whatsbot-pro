"""Plugin lifecycle manager (plano 09, Fase 1).

Gives every plugin a guaranteed initialization and — crucially — clean
finalization point, in the style of VS Code ``activate/deactivate``
(``context.subscriptions`` → Disposables) and Home Assistant
``async_setup_entry``/``async_unload_entry`` + ``entry.async_on_unload(...)``.

The loader captures ``setup``/``teardown`` from ``entry.lifecycle`` but does NOT
call them (import is synchronous, no event loop). The host (server lifespan)
calls and AWAITS them through this manager, where the loop exists.

Key guarantees:
- ``setup`` runs once; a failing ``setup`` does NOT bring down the app — it is
  logged, the plugin is marked with ``load_error``, and any ``on_unload``
  cleanups already registered before the failure still run.
- ``teardown`` runs (and is awaited, with a per-plugin timeout) before the
  process is allowed to die — see plano 09 Fase 2.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from plugins.context import PluginContext, broadcast, make_plugin_db

logger = logging.getLogger(__name__)

# Per-plugin timeout so one slow teardown cannot hang shutdown (P31).
TEARDOWN_TIMEOUT_SEC = 10.0


class PluginLifecycleManager:
    """Holds the per-plugin ``PluginContext`` and runs setup/teardown."""

    def __init__(self) -> None:
        self._contexts: dict[str, PluginContext] = {}
        self._setup_ok: dict[str, bool] = {}

    def get_context(self, plugin_id: str) -> Optional[PluginContext]:
        return self._contexts.get(plugin_id)

    def _ensure_context(self, plugin_id: str, handler: Any,
                        loop: asyncio.AbstractEventLoop) -> PluginContext:
        ctx = self._contexts.get(plugin_id)
        if ctx is None:
            ctx = PluginContext(
                plugin_id=plugin_id, handler=handler, loop=loop,
                plugin_db=make_plugin_db, broadcast=broadcast,
            )
            self._contexts[plugin_id] = ctx
        return ctx

    async def run_setup(self, loaded: Any, handler: Any,
                        loop: asyncio.AbstractEventLoop) -> Optional[str]:
        """Run a plugin's ``setup``. Returns an error string on failure, else None.

        A failing setup still runs any cleanups registered before the failure.
        """
        setup_fn = getattr(loaded, "setup_fn", None)
        teardown_fn = getattr(loaded, "teardown_fn", None)
        if setup_fn is None and teardown_fn is None:
            return None
        ctx = self._ensure_context(loaded.id, handler, loop)
        ctx._teardown_fn = teardown_fn  # type: ignore[attr-defined]
        if setup_fn is None:
            return None  # teardown-only plugin (uses on_unload elsewhere): nothing to run now
        try:
            result = setup_fn(ctx)
            if asyncio.iscoroutine(result):
                await result
            else:
                # Sync setup may block — keep it off the loop.
                await asyncio.to_thread(lambda: None)
            self._setup_ok[loaded.id] = True
            logger.info("Plugin %s: setup() completed", loaded.id)
            return None
        except Exception as e:  # noqa: BLE001
            self._setup_ok[loaded.id] = False
            logger.exception("Plugin %s: setup() failed", loaded.id)
            # Home Assistant principle: cleanups registered before the failure run.
            await ctx._run_disposables()
            return f"{type(e).__name__}: {e}"

    async def run_teardown(self, plugin_id: str) -> None:
        """Run a plugin's ``teardown`` + disposables, awaited with a timeout."""
        ctx = self._contexts.get(plugin_id)
        if ctx is None:
            return
        teardown_fn = getattr(ctx, "_teardown_fn", None)
        try:
            await asyncio.wait_for(self._do_teardown(ctx, teardown_fn),
                                   timeout=TEARDOWN_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.warning("Plugin %s: teardown timed out after %.0fs",
                           plugin_id, TEARDOWN_TIMEOUT_SEC)
        except Exception:  # noqa: BLE001
            logger.exception("Plugin %s: teardown error", plugin_id)
        finally:
            self._contexts.pop(plugin_id, None)
            self._setup_ok.pop(plugin_id, None)

    @staticmethod
    async def _do_teardown(ctx: PluginContext, teardown_fn) -> None:
        if teardown_fn is not None:
            try:
                result = teardown_fn(ctx)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 — still run disposables below
                logger.exception("Plugin %s: teardown() raised", ctx.plugin_id)
        await ctx._run_disposables()
        logger.info("Plugin %s: teardown completed", ctx.plugin_id)


# Process-wide singleton (one manager per server process).
manager = PluginLifecycleManager()
