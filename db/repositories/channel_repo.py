"""Repository for ``channels`` (plano 02 Fase 0).

Core access layer for channel rows. Providers read/write via the
``ChannelRegistry`` (P24), not by importing this directly.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import channels

_STATUS_FIELDS = ("connected", "logged_in", "own_phone", "last_error",
                  "enabled", "display_name", "config", "gowa_device_id",
                  "gowa_isolation", "provider")

# Cached device→channel index for inbound routing (see get_gowa_channel_for_device).
# Channels change rarely; a short TTL keeps the per-message webhook lookup off the DB.
# Invalidated eagerly on any channel mutation below.
_DEVICE_CACHE: dict = {"map": None, "ts": 0.0}
_DEVICE_TTL = 30.0


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(channels).order_by(channels.c.id)).mappings().all()
    return [dict(r) for r in rows]


def get(channel_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(channels).where(channels.c.id == channel_id)
        ).mappings().first()
    return dict(row) if row else None


def create(*, id: str, provider: str, display_name: str = "", enabled: int = 1,
           gowa_device_id: str | None = None, gowa_isolation: str = "shared",
           config: str | None = None) -> dict:
    now = time.time()
    with get_engine().begin() as conn:
        conn.execute(sa_insert(channels).values(
            id=id, provider=provider, display_name=display_name, enabled=enabled,
            gowa_device_id=gowa_device_id, gowa_isolation=gowa_isolation,
            config=config, connected=0, logged_in=0, created_at=now, updated_at=now,
        ))
    invalidate_device_cache()
    return get(id)


def update(channel_id: str, **fields) -> dict | None:
    return set_status(channel_id, **fields)


def set_status(channel_id: str, **fields) -> dict | None:
    values = {k: v for k, v in fields.items() if k in _STATUS_FIELDS}
    if not values:
        return get(channel_id)
    values["updated_at"] = time.time()
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(channels).where(channels.c.id == channel_id).values(**values)
        )
    # own_phone / gowa_device_id / enabled feed the device→channel index.
    invalidate_device_cache()
    return get(channel_id)


def delete(channel_id: str) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(channels).where(channels.c.id == channel_id))
    invalidate_device_cache()
    return (result.rowcount or 0) > 0


# ── Inbound device → channel routing (plano 02 §0.5 / Decisão P13) ──────────
# GOWA runs N WhatsApp numbers as N devices on ONE process with ONE webhook URL
# (.../api/webhook/gowa/default), so the URL alone can't tell which number got the
# message. The GOWA v8 webhook envelope carries the receiving identity at the top
# level: ``device_id`` = the receiving WhatsApp JID (the number), ``session_id`` =
# the id we registered via POST /devices (== a channel's ``gowa_device_id`` or, when
# unset, its channel id — mirrors build_gowa_channel's device fallback). We map that
# back to the owning channel so a second number lands in its own inbox/conversation
# and replies go out its own device — instead of everything collapsing to "default".

def _digits(s: str | None) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _device_map() -> dict:
    cached = _DEVICE_CACHE["map"]
    if cached is not None and (time.time() - _DEVICE_CACHE["ts"]) < _DEVICE_TTL:
        return cached
    by_dev: dict[str, str] = {}
    by_phone: dict[str, str] = {}
    for c in list_all():
        if c.get("provider") != "gowa" or not c.get("enabled"):
            continue
        dev = c.get("gowa_device_id") or c["id"]
        if dev:
            by_dev[str(dev)] = c["id"]
        op = _digits(c.get("own_phone"))
        if op:
            by_phone[op] = c["id"]
    m = {"by_dev": by_dev, "by_phone": by_phone}
    _DEVICE_CACHE["map"] = m
    _DEVICE_CACHE["ts"] = time.time()
    return m


def invalidate_device_cache() -> None:
    """Drop the device→channel index (call after any channel create/connect/edit)."""
    _DEVICE_CACHE["map"] = None
    _DEVICE_CACHE["ts"] = 0.0


def get_gowa_channel_for_device(session_id: str | None = None,
                                device_jid: str | None = None) -> str | None:
    """Channel id that owns an inbound GOWA event, or ``None`` if undeterminable.

    Prefers ``session_id`` (the registered device string — stable and available even
    before login); falls back to the receiving number (``device_jid`` → digits)
    matched against the channel's ``own_phone`` (set by the status poll once logged
    in). Best-effort: ``None`` lets the caller keep the URL's channel (legacy
    behaviour), so this never makes routing worse.
    """
    m = _device_map()
    sid = str(session_id or "").strip()
    if sid and sid in m["by_dev"]:
        return m["by_dev"][sid]
    phone = _digits((str(device_jid or "").split("@")[0]).split(":")[0])
    if phone:
        if phone in m["by_phone"]:
            return m["by_phone"][phone]
        # Tolerate country-code/format differences between own_phone and the JID.
        for op, cid in m["by_phone"].items():
            if op.endswith(phone) or phone.endswith(op):
                return cid
    return None
