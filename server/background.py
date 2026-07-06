"""Background tasks for the WhatsBot server (GOWA startup, status/QR polling)."""

import asyncio
import logging
import time
from pathlib import Path

from db.repositories import contact_repo
from agent import group_mentions
from plugins.events import emit as emit_event, emit_with_filter
from server.avatars import refresh_and_broadcast

# How often the background sweep re-checks every contact's avatar for changes.
AVATAR_REFRESH_INTERVAL = 1800  # seconds (30 min)

# How often the audit retention purge runs (plano 07 Fase 2).
AUDIT_PURGE_INTERVAL = 86400  # seconds (1 day)
AUDIT_RETENTION_DAYS_DEFAULT = 365

# How often the empty-inbound-ghost sweep runs, and the default TTL (plano 28 Fase 5).
GHOST_SWEEP_INTERVAL = 600  # seconds (10 min)
EMPTY_CONV_TTL_MINUTES_DEFAULT = 30

# How often the account-identity sweep persists per-channel identity + refuses
# duplicates connected post-QR (plano 32 F4).
CHANNEL_IDENTITY_SWEEP_INTERVAL = 15  # seconds

logger = logging.getLogger(__name__)


# NOTE: GOWA bring-up (subprocess start + device register) is owned EXCLUSIVELY by
# the gowa plugin (assets/plugin_examples/gowa/lifecycle.py → ctx.spawn_subprocess +
# _post_spawn_init). The old core ``start_gowa_task`` was removed (plano 13): it
# called ``gowa_manager.start()`` which builds a SECOND ManagedProcess named "gowa"
# — re-attaching it anywhere would double-spawn GOWA over the same pid/session. The
# 3 polling loops below ARE imported by the plugin (it owns them too).


async def status_poll_loop(deps):
    """Poll WhatsApp connection status every 5 seconds."""
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings
    last_connected: bool | None = None
    last_qr_required: bool | None = None
    while not state.stop_event.is_set():
        try:
            connected = await asyncio.to_thread(gowa_client.is_connected)
            state.connected = connected
            await ws_manager.broadcast("status", {
                "connected": state.connected,
                "msg_count": state.msg_count,
                "auto_reply_running": state.auto_reply_running,
                "bot_phone": state.bot_phone,
                "bot_name": state.bot_name,
            })
            # Plugin event: connection state transitions only (not every poll).
            qr_required = bool(state.qr_data is not None and not connected)
            if (last_connected, last_qr_required) != (connected, qr_required):
                await emit_with_filter("connection.changed", {
                    "is_connected": bool(connected),
                    "is_logged_in": bool(connected),  # GOWA conflates these
                    "qr_required": qr_required,
                    "bot_phone": state.bot_phone,
                    "bot_name": state.bot_name,
                    "ts": time.time(),
                })
                last_connected = connected
                last_qr_required = qr_required
            # Auto-reply is handled via GOWA webhook (POST /api/webhook)
            state.auto_reply_running = connected and settings.get("auto_reply", True)
            # Bot's display name = real WhatsApp profile name from GOWA (/devices
            # display_name), not a configured value. Fetched independently of the
            # phone capture below, which may happen first via the webhook echo path.
            if connected and not state.bot_name:
                name = await asyncio.to_thread(gowa_client.get_own_display_name)
                if name:
                    state.bot_name = name
                    if state.bot_phone:
                        group_mentions.set_bot_identity(state.bot_phone, state.bot_name)
            # Populate bot identity for @mention detection in groups
            if connected and not state.bot_phone:
                # Try config first
                state.bot_phone = settings.get("bot_phone", "").split(":")[0]
                # Auto-detect: fetch recent chat messages to find our own JID
                if not state.bot_phone:
                    try:
                        chats = await asyncio.to_thread(gowa_client.get_chats, 5)
                        for chat in chats:
                            cjid = chat.get("jid", "")
                            if "@s.whatsapp.net" in cjid:
                                msgs = await asyncio.to_thread(
                                    gowa_client.get_chat_messages, cjid, 5)
                                for m in msgs:
                                    if m.get("is_from_me") or m.get("from_me"):
                                        own_jid = (m.get("sender_jid", "")
                                                   or m.get("sender", "")
                                                   or m.get("from", ""))
                                        if own_jid and "@s.whatsapp.net" in own_jid:
                                            state.bot_phone = own_jid.split("@")[0].split(":")[0]
                                            logger.info("[Status] Bot phone auto-detected: %s",
                                                        state.bot_phone)
                                            break
                                if state.bot_phone:
                                    break
                    except Exception:
                        pass
                if state.bot_phone:
                    logger.info("[Status] Bot phone: %s, name: %s",
                                state.bot_phone, state.bot_name or "(empty)")
                    # Persist so it survives restarts and @mention detection in
                    # groups works immediately on the next boot (loaded above from
                    # config before the auto-detect path).
                    if settings.get("bot_phone", "") != state.bot_phone:
                        try:
                            settings.set("bot_phone", state.bot_phone)
                        except Exception as e:
                            logger.warning("[Status] Failed to persist bot_phone: %s", e)
                    # Register bot identity so mentions of the bot resolve to
                    # its configured panel name (settings > GOWA pushName).
                    group_mentions.set_bot_identity(state.bot_phone, state.bot_name)
        except Exception as e:
            logger.error("Status poll error: %s", e)
        await asyncio.sleep(5)


async def channel_identity_sweep_loop(deps):
    """Persist per-channel account identity + refuse post-QR duplicates (plano 32 F4).

    Generic over providers: iterates the live channel instances in the registry and
    lets :mod:`app.services.channel_identity` read each one's ``status()`` +
    ``account_identity()``, persisting only what changed and refusing a channel that
    paired to an account already owned by another channel. Defensive per-channel so
    one bad channel never stalls the sweep."""
    from app.services import channel_identity as ci
    registry = getattr(deps, "channel_registry", None)
    state = deps.state
    while not state.stop_event.is_set():
        try:
            if registry is not None:
                for cid, inst in list(registry.all_channels().items()):
                    await asyncio.to_thread(ci.sweep_channel, cid, inst)
        except Exception as e:  # noqa: BLE001
            logger.error("Channel identity sweep error: %s", e)
        await asyncio.sleep(CHANNEL_IDENTITY_SWEEP_INTERVAL)


async def qr_poll_loop(deps):
    """Poll QR availability and cache QR image.

    QR codes from WhatsApp are valid for ~20s. We fetch a new one
    only when the cache is older than QR_CACHE_TTL, so the frontend
    always shows a stable image the user can actually scan.
    """
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    QR_CACHE_TTL = 120  # seconds — WhatsApp QRs valid ~160s; refresh conservatively
    while not state.stop_event.is_set():
        try:
            if not state.connected:
                age = time.time() - state.qr_fetched_at
                if state.qr_data is None or age >= QR_CACHE_TTL:
                    qr_data = await asyncio.to_thread(gowa_client.get_qr_code)
                    if qr_data and isinstance(qr_data, bytes):
                        state.qr_data = qr_data
                        state.qr_fetched_at = time.time()
                        state.qr_version += 1
                        await ws_manager.broadcast("qr_update", {
                            "available": True,
                            "version": state.qr_version,
                        })
                    elif state.qr_data is None:
                        await ws_manager.broadcast("qr_update", {"available": False})
            else:
                if state.qr_data is not None:
                    state.qr_data = None
                    state.qr_fetched_at = 0
                await ws_manager.broadcast("qr_update", {"available": False})
        except Exception as e:
            logger.error("QR poll error: %s", e)
        # Poll faster when we don't have a QR yet (waiting for first one)
        await asyncio.sleep(2 if state.qr_data is None and not state.connected else 5)


async def avatar_fetch_task(deps):
    """Keep WhatsApp profile photos fresh for all contacts.

    Runs an initial sweep once connected, then re-sweeps every
    ``AVATAR_REFRESH_INTERVAL`` seconds. Each pass re-fetches every contact's
    avatar and overwrites the cached file only when it actually changed (new
    photo, removed photo handled by keeping the last one), broadcasting
    ``avatar_updated`` so open clients update live.
    """
    state = deps.state
    settings = deps.settings
    avatars_dir = settings.data_dir / "statics" / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)

    # Wait until WhatsApp is connected
    while not state.stop_event.is_set():
        if state.connected:
            break
        await asyncio.sleep(3)

    if state.stop_event.is_set():
        return

    # Give GOWA a moment to stabilize after connection
    await asyncio.sleep(5)

    while not state.stop_event.is_set():
        try:
            contacts = await asyncio.to_thread(contact_repo.list_contacts, "", False)
            archived = await asyncio.to_thread(contact_repo.list_contacts, "", True)
            all_contacts = contacts + archived
        except Exception as e:
            logger.error("[Avatar] Failed to load contacts: %s", e)
            all_contacts = []

        changed = 0
        for c in all_contacts:
            if state.stop_event.is_set():
                return
            phone = c.get("phone", "")
            if not phone:
                continue
            try:
                if await refresh_and_broadcast(deps, phone):
                    changed += 1
            except Exception as e:
                logger.debug("[Avatar] refresh failed for %s: %s", phone, e)
            # Rate limit to avoid overwhelming GOWA
            await asyncio.sleep(0.5)

        logger.info("[Avatar] Sweep done: %d updated (of %d contacts)",
                    changed, len(all_contacts))

        # Sleep until the next sweep (interruptible by shutdown).
        slept = 0
        while slept < AVATAR_REFRESH_INTERVAL and not state.stop_event.is_set():
            await asyncio.sleep(3)
            slept += 3


async def audit_purge_loop(deps):
    """Enforce audit retention (plano 07 Fase 2): once a day, delete audit rows
    older than ``audit_retention_days`` (config, default 365). The purge is the
    ONLY deletion allowed on the append-only trail.
    """
    state = deps.state
    from db.repositories import config_repo, audit_repo

    while not state.stop_event.is_set():
        try:
            days = await asyncio.to_thread(config_repo.get, "audit_retention_days")
            try:
                days = int(days) if days is not None else AUDIT_RETENTION_DAYS_DEFAULT
            except (TypeError, ValueError):
                days = AUDIT_RETENTION_DAYS_DEFAULT
            if days > 0:
                cutoff = time.time() - days * 86400
                removed = await asyncio.to_thread(audit_repo.purge, cutoff)
                if removed:
                    logger.info("[Audit] Retention purge removed %d rows (older than %d days)",
                                removed, days)
        except Exception as e:
            logger.warning("[Audit] purge loop error: %s", e)

        slept = 0
        while slept < AUDIT_PURGE_INTERVAL and not state.stop_event.is_set():
            await asyncio.sleep(5)
            slept += 5


async def empty_conversation_sweep_loop(deps):
    """Sweep 'inbound' ghost conversations (plano 28 Fase 5).

    A brand-new conversation is materialized at ingest (t=0, origin='inbound') so it
    shows on the sidebar immediately; its first message is persisted ~3s later by the
    batch. If that batch never runs (shutdown/crash), the conversation lingers as an
    empty row (trade-off: "visible and empty" > "invisible until F5"). This TTL sweep
    removes inbound conversations that still have NO message after
    ``empty_conversation_ttl_minutes`` (config, default 30; <=0 disables), broadcasting
    ``conversation_deleted`` so open panels drop the row live. The TTL is far larger
    than the batch delay, so a legitimate new conversation is never swept.
    """
    state = deps.state
    from db.repositories import config_repo, conversation_repo
    from app.services import conversation_service

    while not state.stop_event.is_set():
        try:
            raw = await asyncio.to_thread(config_repo.get, "empty_conversation_ttl_minutes")
            try:
                ttl_min = int(raw) if raw is not None else EMPTY_CONV_TTL_MINUTES_DEFAULT
            except (TypeError, ValueError):
                ttl_min = EMPTY_CONV_TTL_MINUTES_DEFAULT
            if ttl_min > 0:
                cutoff = time.time() - ttl_min * 60
                ghosts = await asyncio.to_thread(
                    conversation_repo.find_empty_inbound_ghosts, cutoff)
                for g in ghosts:
                    if state.stop_event.is_set():
                        break
                    try:
                        await conversation_service.delete(deps, g)
                    except Exception:
                        logger.debug("[GhostSweep] delete failed for conv %s", g.get("id"))
                if ghosts:
                    logger.info("[GhostSweep] removed %d empty inbound ghost conversation(s)",
                                len(ghosts))
        except Exception as e:
            logger.warning("[GhostSweep] loop error: %s", e)

        slept = 0
        while slept < GHOST_SWEEP_INTERVAL and not state.stop_event.is_set():
            await asyncio.sleep(5)
            slept += 5
