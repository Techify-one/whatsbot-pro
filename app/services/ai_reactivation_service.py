"""Automatic re-enabling of the AI on conversations it stopped answering (plano 178).

The AI stops answering a conversation by several roads — ``transfer_to_human``,
assigning it to a person, "pausar a IA" in the panel, being born off
(``default_ai_enabled``), team routing — and only a human action brings it back. A
conversation handed over and forgotten stays without the AI forever. This module turns
the AI back on later, by one of two rules chosen in Configurações de IA → Automações:

* ``minutes`` / ``hours`` / ``days``: the conversation has been quiet for that long,
  counted from the LATER of the last human message and the moment the AI was
  switched off (``atendimentos.ai_disabled_at``);
* ``schedule``: on the chosen weekdays, at the chosen time of day.

Either way only conversations whose last human message came from the chosen side
(customer, attendant or both) are touched. The unit is the CONVERSATION per channel
(``atendimentos``), never the contact: the same customer can be answered by the AI on
one channel and by a person on another.

Two halves, so the rule is testable without a clock, a database or a loop:

* PURE — :func:`clean`, :func:`read_config`, :func:`resolve_tz`, :func:`owner_allows`,
  :func:`reference_ts`, :func:`latest_slot`, :func:`schedule_due`, :func:`pick_rows`;
* EFFECTS — :func:`run_once`. The act of turning the AI back on is NOT rewritten
  here: it is ``conversation_service.set_ai(deps, conv, 1)``, the very function behind
  the panel button, which re-binds the channel's default agent, clears the human
  assignee and the ``transferido_atendente`` tag, broadcasts, emits the bus events and
  writes the "🤖 SISTEMA reativou a IA" card.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from channels import ai_settings
from config.settings import TECHIFY_PROVISION_NUMBER
from db.repositories import config_repo, conversation_repo
from plugins.events import emit_with_filter

logger = logging.getLogger(__name__)

UNITS = ("minutes", "hours", "days", "schedule")
OWNERS = ("both", "customer", "attendant")
UNIT_SECONDS = {"minutes": 60, "hours": 3600, "days": 86400}
MAX_VALUE = 100_000

# A scheduled run is still honoured this long after its time. It covers a server that
# was down or restarting at 08:00, without turning the AI on at 23:00 for a slot that
# passed in the morning.
SCHEDULE_GRACE = 3600  # seconds

# One pass turns back on at most this many conversations. A backlog (hundreds parked
# for days when the option is first enabled) would otherwise burst out cards, sockets
# and bus events at once; the remainder is picked up by the next pass, a minute later.
MAX_PER_PASS = 200

KEY_PREFIX = "ai_reactivation_"
INVALID = object()
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_TZ_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}$")

# ── pure half ───────────────────────────────────────────────────────────────

def clean(key: str, value):
    """Validate one ``ai_reactivation_*`` value coming from the panel.

    Returns the value to store, or :data:`INVALID` when it must be ignored, so junk
    never reaches the config and the loop never has to defend against it. Keys that are
    not user-editable (``last_run``) are INVALID here on purpose.
    """
    if key in ("ai_reactivation_enabled", "ai_reactivation_include_groups_archived"):
        return value if isinstance(value, bool) else INVALID
    if key == "ai_reactivation_unit":
        return value if value in UNITS else INVALID
    if key == "ai_reactivation_last_sender":
        return value if value in OWNERS else INVALID
    if key == "ai_reactivation_value":
        if isinstance(value, bool):
            return INVALID
        try:
            number = int(value)
        except (TypeError, ValueError):
            return INVALID
        return min(max(number, 1), MAX_VALUE)
    if key == "ai_reactivation_weekdays":
        if not isinstance(value, list):
            return INVALID
        days = {d for d in value
                if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6}
        return sorted(days)
    if key == "ai_reactivation_time":
        return value if isinstance(value, str) and _HHMM.match(value) else INVALID
    if key == "ai_reactivation_timezone":
        # The browser's IANA name. Not checked against the tz database on purpose:
        # whether the server can resolve it is decided at run time.
        if value == "" or (isinstance(value, str) and len(value) <= 64
                           and _TZ_NAME.match(value)):
            return value
        return INVALID
    return INVALID

def read_config(settings) -> dict:
    """The effective rule, always well-formed (falls back per key)."""
    def pick(key: str, default):
        value = clean(key, settings.get(key, default))
        return default if value is INVALID else value

    return {
        "enabled": pick("ai_reactivation_enabled", False),
        "unit": pick("ai_reactivation_unit", "hours"),
        "value": pick("ai_reactivation_value", 24),
        "weekdays": pick("ai_reactivation_weekdays", [0, 1, 2, 3, 4]),
        "time": pick("ai_reactivation_time", "08:00"),
        "last_sender": pick("ai_reactivation_last_sender", "both"),
        "include_groups_archived": pick("ai_reactivation_include_groups_archived", False),
        "timezone": pick("ai_reactivation_timezone", ""),
    }

def resolve_tz(name: str) -> tzinfo:
    """The zone the schedule is read in: the browser's, else the server's own."""
    if name:
        try:
            return ZoneInfo(name)
        except Exception:  # unknown name, or no tz database on this host
            logger.warning("[AI reactivation] timezone %r unavailable; using the server's",
                           name)
    return datetime.now().astimezone().tzinfo

def owner_allows(owner: str, last_role: str | None) -> bool:
    """Does the side that spoke last match the rule?

    ``last_role`` is ``'user'`` (customer), ``'assistant'`` (attendant) or ``None`` (no
    human message yet — only "both" accepts that).
    """
    if owner == "both":
        return True
    if last_role == "user":
        return owner == "customer"
    if last_role == "assistant":
        return owner == "attendant"
    return False

def reference_ts(row: dict) -> float:
    """Where the quiet period is counted from.

    The later of the last human message and the moment the AI was switched off: a
    conversation switched off by hand a minute ago is not turned back on just because
    its last message is old. Rows from before ``ai_disabled_at`` existed (or whose AI
    was never switched off, only a human assigned) fall back to the last message, and
    conversations with neither to creation.
    """
    ts = max(row.get("last_ts") or 0.0, row.get("ai_disabled_at") or 0.0)
    return ts or row.get("created_at") or 0.0

def latest_slot(weekdays: list[int], hhmm: str, now: float, tz: tzinfo) -> float | None:
    """Epoch of the most recent scheduled time at or before ``now`` (Mon = 0)."""
    if not weekdays:
        return None
    hour, minute = int(hhmm[:2]), int(hhmm[3:])
    local_now = datetime.fromtimestamp(now, tz)
    for back in range(8):
        day = local_now.date() - timedelta(days=back)
        if day.weekday() not in weekdays:
            continue
        slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        if slot <= local_now:
            return slot.timestamp()
    return None

def schedule_due(cfg: dict, now: float, last_run: float, tz: tzinfo) -> float | None:
    """The slot to serve now, or ``None``: a scheduled time passed since the last run
    and is still within the grace."""
    slot = latest_slot(cfg["weekdays"], cfg["time"], now, tz)
    if slot is not None and slot > last_run and now - slot <= SCHEDULE_GRACE:
        return slot
    return None

def pick_rows(rows: list[dict], cfg: dict, now: float) -> list[dict]:
    """Of the conversations the AI is not in charge of, the ones to turn back on now.

    For ``schedule`` the caller has already established that a slot is due, so every
    row matching the sender filter qualifies; for minutes/hours/days it must also have
    been quiet long enough. Oldest wait first, so a backlog drains in a fair order.
    """
    matching = [r for r in rows if owner_allows(cfg["last_sender"], r.get("last_role"))]
    if cfg["unit"] != "schedule":
        wait = cfg["value"] * UNIT_SECONDS[cfg["unit"]]
        matching = [r for r in matching if now - reference_ts(r) >= wait]
    return sorted(matching, key=reference_ts)

# ── effects ─────────────────────────────────────────────────────────────────

def _excluded_phones(settings) -> list[str]:
    """Contacts whose AI is off on purpose and must never come back."""
    return [TECHIFY_PROVISION_NUMBER, *(settings.get("provisioning_phones", []) or [])]

def _channel_allows_ai(row: dict) -> bool:
    """A channel with its AI off never answers, so a "AI reactivated" card there would
    be a lie. Mirrors the webhook's master gate for the channel half (the global
    ``auto_reply`` half is checked once per pass)."""
    if not bool(row.get("agent_bot_enabled", 1)):
        return False
    return bool(ai_settings.value(str(row["channel_id"]), "ai_enabled", True))

async def run_once(deps, now: float | None = None) -> int:
    """One pass of the rule. Returns how many conversations were turned back on."""
    settings = deps.settings
    cfg = read_config(settings)
    if not cfg["enabled"]:
        return 0
    # The panel-wide switch: with it off no channel ever answers.
    if not settings.get("auto_reply", True):
        return 0

    from app.services import conversation_service

    now = time.time() if now is None else now
    if cfg["unit"] == "schedule":
        last_run = float(settings.get("ai_reactivation_last_run", 0.0) or 0.0)
        slot = schedule_due(cfg, now, last_run, resolve_tz(cfg["timezone"]))
        if slot is None:
            return 0
        # Claimed BEFORE acting, atomically: a failure below must not re-fire every
        # minute, and two instances sharing one database must not both serve the slot.
        won = await asyncio.to_thread(
            config_repo.advance_if_older, "ai_reactivation_last_run", slot, now)
        if not won:
            return 0

    rows = await asyncio.to_thread(
        conversation_repo.ai_off_for_reactivation,
        cfg["include_groups_archived"], _excluded_phones(settings))
    due = pick_rows(rows, cfg, now)[:MAX_PER_PASS]

    turned_on = 0
    for row in due:
        try:
            if not _channel_allows_ai(row):
                continue
            conv = await asyncio.to_thread(conversation_repo.get, row["id"])
            # Re-read: the row came from a query that may be seconds old, and a person
            # may have acted on it since (took it back, closed it).
            if not conv or conv.get("status") != "open":
                continue
            if conv.get("ai_active") and conv.get("assignee_user_id") is None:
                continue  # somebody already turned it back on
            updated = await conversation_service.set_ai(deps, conv, 1)
            if not updated:
                continue
            turned_on += 1
            await emit_with_filter("contact.ai_toggled", {
                "phone": row["phone"], "ai_enabled": True,
                "source": "auto_reactivation", "ts": time.time(),
            })
        except Exception as e:  # one bad conversation must not stop the others
            logger.error("[AI reactivation] failed for conversation %s: %s",
                         row.get("id"), e)

    if turned_on:
        logger.info("[AI reactivation] AI turned back on for %d conversation(s)", turned_on)
    return turned_on
