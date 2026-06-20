"""Telegram plugin lifecycle — long-poll inbound (plano 13 Fase 3).

When ``inbound_mode == "poll"`` (the default — works on desktop/EXE with no
public host), ``setup(ctx)`` registers ONE supervised background task that drives
Telegram's ``getUpdates`` long-poll and feeds each update into the core's
provider-agnostic funnel via ``ctx.ingest_event`` (wired by plano 13 Fase 1.1).
No core code is touched: this is the same supervised-task + ingest path any third
party channel plugin would use.

When ``inbound_mode == "webhook"`` nothing is started here — the core's generic
``POST /api/webhook/telegram/{channel_id}`` route already parses + dispatches; the
user registers the webhook URL from the config screen.

Robustness:
- The loop re-scans the channel registry each cycle, so a channel created/edited
  after boot is picked up WITHOUT a restart (and self-healed into the registry so
  inbound media download works).
- The first poll of a channel DRAINS the backlog (sets the offset past the last
  pending update without ingesting), so a restart never replays — and re-answers —
  a flood of messages that arrived while the bot was down.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# Long-poll server-side hold; the AsyncClient read timeout must exceed it.
_LONGPOLL_TIMEOUT = 25
_ALLOWED_UPDATES = ["message", "edited_message", "channel_post",
                    "message_reaction", "callback_query"]


def _get_setting(key: str, default):
    """Read a persisted plugin setting (``plugin.telegram.<key>``), best-effort."""
    try:
        from db.repositories import config_repo
        val = config_repo.get(f"plugin.telegram.{key}")
        return default if val is None else val
    except Exception:  # noqa: BLE001
        return default


def _telegram_channels(registry):
    try:
        return [c for c in registry.list_channels()
                if c.get("provider") == "telegram" and c.get("enabled", 1)]
    except Exception:  # noqa: BLE001
        return []


def _ensure_live(registry, cid):
    """Return the live channel instance for ``cid``, self-healing the registry.

    Boot materialization + register-on-create normally put the instance in the
    registry already; this rebuilds it if a channel was created mid-cycle so the
    media-download path (which looks the instance up in the registry) works."""
    inst = registry.get(cid)
    if inst is None:
        try:
            from whatsbot_plugins.telegram.channels import TelegramChannel
        except Exception:  # noqa: BLE001
            logger.debug("telegram: TelegramChannel import unavailable for self-heal")
            return None
        inst = TelegramChannel(cid, registry)
        registry.add_channel(cid, inst)
    return inst


async def _get_updates(client, base, token, offset, *, timeout):
    params = {"timeout": timeout, "allowed_updates": _ALLOWED_UPDATES}
    if offset is not None:
        params["offset"] = offset
    resp = await client.post(f"{base}/bot{token}/getUpdates", json=params)
    data = resp.json() if resp.content else {}
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"http_{resp.status_code}")
    return data.get("result") or []


async def _poll_loop(ctx) -> None:
    """Supervised forever-loop: long-poll every Telegram channel → ingest_event."""
    from whatsbot_plugins.telegram.channels import api_base
    registry = ctx.channel_registry
    ingest = ctx.ingest_event
    base = api_base()
    offsets: dict[str, int] = {}
    drained: set[str] = set()
    logger.info("telegram long-poll loop started")
    # Read timeout must comfortably exceed the long-poll hold.
    timeout = httpx.Timeout(_LONGPOLL_TIMEOUT + 10)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                interval = float(_get_setting("poll_interval", 2.0) or 2.0)
                for ch in _telegram_channels(registry):
                    cid = ch["id"]
                    token = registry.get_credential(cid, "bot_token")
                    if not token:
                        continue
                    inst = _ensure_live(registry, cid)
                    if inst is None:
                        continue
                    # First poll: drain the backlog so a restart doesn't replay.
                    if cid not in drained:
                        try:
                            backlog = await _get_updates(client, base, token, -1, timeout=0)
                            if backlog:
                                offsets[cid] = backlog[-1].get("update_id", 0) + 1
                            drained.add(cid)
                        except Exception as e:  # noqa: BLE001
                            logger.debug("telegram drain failed for %s: %s", cid, e)
                            continue
                        continue
                    try:
                        updates = await _get_updates(
                            client, base, token, offsets.get(cid),
                            timeout=_LONGPOLL_TIMEOUT)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("telegram getUpdates failed for %s: %s", cid, e)
                        continue
                    for upd in updates:
                        offsets[cid] = upd.get("update_id", 0) + 1
                        try:
                            for ev in inst.parse_inbound(upd):
                                await ingest(ev)
                        except Exception:  # noqa: BLE001
                            logger.warning("telegram update ingest failed (%s)", cid,
                                           exc_info=True)
                await asyncio.sleep(max(0.2, interval))
    except asyncio.CancelledError:
        logger.info("telegram long-poll loop cancelled cleanly")
        raise


async def setup(ctx) -> None:
    mode = str(_get_setting("inbound_mode", "poll") or "poll").lower()
    if mode != "poll":
        logger.info("telegram: inbound_mode=%s — long-poll not started "
                    "(register the webhook URL from the config screen)", mode)
        return
    if ctx.ingest_event is None or ctx.channel_registry is None:
        logger.warning("telegram: channel runtime not wired (ingest_event/registry "
                       "missing) — cannot long-poll. Webhook mode still works.")
        return
    try:
        full = ctx.spawn_task("poll", lambda: _poll_loop(ctx))
        logger.info("telegram: registered supervised long-poll task %r", full)
    except RuntimeError as e:
        logger.warning("telegram: supervisor not wired (%s); long-poll disabled", e)


async def teardown(ctx) -> None:
    # The supervised long-poll task is auto-cancelled by the lifecycle manager
    # (stop_owner(plugin_id)); nothing to do here but log.
    logger.info("telegram: teardown (plugin_id=%s)", ctx.plugin_id)
