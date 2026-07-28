"""Telegram plugin lifecycle — long-poll inbound (plano 13 Fase 3).

When ``inbound_mode == "poll"`` (the default — works on desktop/EXE with no
public host), ``setup(ctx)`` registers ONE supervised background task that drives
Telegram's ``getUpdates`` long-poll and feeds each update into the core's
provider-agnostic funnel via ``ctx.ingest_event`` (wired by plano 13 Fase 1.1).
No core code is touched: this is the same supervised-task + ingest path any third
party channel plugin would use.

The mode is PER-CHANNEL (``channels.config["telegram"]["inbound_mode"]`` — see
``mode.py``): the loop runs whenever the runtime is wired and, each cycle, SKIPS
channels in ``webhook`` mode (their updates arrive via the core's generic
``POST /api/webhook/telegram/{channel_id}`` route). So a channel can flip between
webhook and long-poll WITHOUT a restart, and two inboxes can use different modes.

Robustness:
- The loop re-scans the channel registry each cycle, so a channel created/edited
  (or whose mode changed) after boot is picked up WITHOUT a restart (and self-healed
  into the registry so inbound media download works).
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
# Teto do backoff exponencial por canal em falha consecutiva (assets).
_ERROR_BACKOFF_MAX = 30.0
_ALLOWED_UPDATES = ["message", "edited_message", "channel_post",
                    "message_reaction", "callback_query"]


def _is_conflict(err: str) -> bool:
    e = (err or "").lower()
    return "conflict" in e or "webhook" in e or "terminated by other" in e


def _note_error(errors: dict, cid: str, where: str, exc: Exception) -> float:
    """Record a consecutive failure, log it VISIBLY (WARNING shows even when the
    dev server runs at --log-level warning) and return the backoff delay to apply.

    Logs the first failure then sparsely (every 5th) to avoid flooding a persistent
    outage. A 409/conflict gets an explicit hint — the usual root causes for a
    silent long-poll are a leftover webhook or a second instance polling the bot."""
    n = errors[cid] = errors.get(cid, 0) + 1
    delay = min(_ERROR_BACKOFF_MAX, 2.0 * (2 ** min(n - 1, 4)))  # 2,4,8,16,32→cap 30
    msg = str(exc)
    if n == 1 or n % 5 == 0:
        hint = (" — possível webhook ainda registrado OU outra instância fazendo "
                "getUpdates (o Telegram só permite um long-poll por bot)"
                if _is_conflict(msg) else "")
        logger.warning("telegram %s falhou para %s (tentativa %d, backoff %.0fs)%s: %s",
                       where, cid, n, delay, hint, msg)
    return delay


def _get_setting(key: str, default):
    """Read a persisted plugin setting (``plugin.telegram.<key>``), best-effort."""
    try:
        from db.repositories import config_repo
        val = config_repo.get(f"plugin.telegram.{key}")
        return default if val is None else val
    except Exception:  # noqa: BLE001
        return default


# Stashed in setup() so the routes can re-arm the poll task when a channel flips
# to long-poll at runtime (the task is only alive while ≥1 channel needs it).
_CTX = None


def _telegram_channels(registry):
    try:
        return [c for c in registry.list_channels()
                if c.get("provider") == "telegram" and c.get("enabled", 1)]
    except Exception:  # noqa: BLE001
        return []


def _poll_channels(registry):
    """Enabled Telegram channels in long-poll mode (the ones the loop must serve).

    Webhook channels are excluded — they're delivered by the core webhook route."""
    from whatsbot_plugins.telegram.mode import mode_from_row
    return [c for c in _telegram_channels(registry) if mode_from_row(c) != "webhook"]


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
    """Supervised loop: long-poll every poll-mode Telegram channel.

    Exits cleanly (→ task state ``completed``, not relaunched — the task is
    TRANSIENT) once NO channel is in long-poll mode, so a webhook-only install
    shows no running poll task. The routes re-arm it via ``ensure_poll_running``
    when a channel flips back to long-poll."""
    from whatsbot_plugins.telegram.channels import api_base
    registry = ctx.channel_registry
    ingest = ctx.ingest_event
    base = api_base()
    offsets: dict[str, int] = {}
    drained: set[str] = set()
    # Falhas consecutivas por canal → backoff + dica de 409 (vinha do assets;
    # preservado ao adotar o loop por-canal da loja).
    errors: dict[str, int] = {}
    logger.info("telegram long-poll loop started")
    # Read timeout must comfortably exceed the long-poll hold.
    timeout = httpx.Timeout(_LONGPOLL_TIMEOUT + 10)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                poll_chans = _poll_channels(registry)
                if not poll_chans:
                    logger.info("telegram: no long-poll channels left — stopping "
                                "poll loop (webhook channels unaffected)")
                    return
                interval = float(_get_setting("poll_interval", 2.0) or 2.0)
                backoff_needed = 0.0
                for ch in poll_chans:
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
                            backoff_needed = max(backoff_needed,
                                                 _note_error(errors, cid, "drain", e))
                            continue
                        continue
                    try:
                        updates = await _get_updates(
                            client, base, token, offsets.get(cid),
                            timeout=_LONGPOLL_TIMEOUT)
                        errors[cid] = 0
                    except Exception as e:  # noqa: BLE001
                        backoff_needed = max(backoff_needed,
                                             _note_error(errors, cid, "getUpdates", e))
                        continue
                    for upd in updates:
                        offsets[cid] = upd.get("update_id", 0) + 1
                        try:
                            for ev in inst.parse_inbound(upd):
                                await ingest(ev)
                        except Exception:  # noqa: BLE001
                            logger.warning("telegram update ingest failed (%s)", cid,
                                           exc_info=True)
                await asyncio.sleep(max(0.2, interval, backoff_needed))
    except asyncio.CancelledError:
        logger.info("telegram long-poll loop cancelled cleanly")
        raise


def _spawn_poll(ctx) -> bool:
    """Register + start the TRANSIENT long-poll task. Idempotent (the supervisor
    no-ops if it's already running). Returns True if (re)armed."""
    try:
        from runtime.supervisor import RestartPolicy
        full = ctx.spawn_task("poll", lambda: _poll_loop(ctx),
                              policy=RestartPolicy.TRANSIENT)
        logger.info("telegram: armed long-poll task %r (per-channel mode)", full)
        return True
    except RuntimeError as e:
        logger.warning("telegram: supervisor not wired (%s); long-poll disabled", e)
        return False


def ensure_poll_running() -> None:
    """Re-arm the long-poll task when a channel flips to long-poll at runtime.

    Called by the routes after switching a channel to poll mode. No-op if the
    runtime isn't wired or there's no poll-mode channel to serve."""
    ctx = _CTX
    if ctx is None or ctx.ingest_event is None or ctx.channel_registry is None:
        return
    try:
        if not _poll_channels(ctx.channel_registry):
            return
        _spawn_poll(ctx)
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram: ensure_poll_running failed: %s", e)


async def setup(ctx) -> None:
    # Inbound mode is PER-CHANNEL (channels.config["telegram"]["inbound_mode"]).
    # The long-poll task only exists while ≥1 channel is in long-poll mode, so a
    # webhook-only install shows NO running poll task. It's TRANSIENT and self-exits
    # when the last poll channel goes webhook; the routes re-arm it (ensure_poll_running)
    # when a channel flips back to long-poll.
    global _CTX
    _CTX = ctx
    if ctx.ingest_event is None or ctx.channel_registry is None:
        logger.warning("telegram: channel runtime not wired (ingest_event/registry "
                       "missing) — cannot long-poll. Webhook mode still works.")
        return
    if not _poll_channels(ctx.channel_registry):
        logger.info("telegram: no long-poll channels — poll task not started "
                    "(webhook channels are served by the core webhook route)")
        return
    _spawn_poll(ctx)


async def teardown(ctx) -> None:
    # The supervised long-poll task is auto-cancelled by the lifecycle manager
    # (stop_owner(plugin_id)); nothing to do here but log.
    logger.info("telegram: teardown (plugin_id=%s)", ctx.plugin_id)
