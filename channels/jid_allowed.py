"""Leaf cache for a GOWA channel's ``config.allowed_jid_types`` (plano 173).

Extracted from ``app.services.message_ingest_service`` so the provider layer
(``channels/*``) can read the same TTL-cached decision WITHOUT importing
``app.services`` — that would invert the dependency direction (providers are
leaves; services own orchestration). ``message_ingest_service`` now delegates
its per-channel read-through here instead of keeping a private copy.

Two read shapes:

* :func:`peek` — memory-only, NEVER touches the DB. For the hot webhook route
  (plano 173 F3), which must decide to drop a request in microseconds, with no
  thread and no socket. A cold/expired cache returns ``None`` — the caller
  MUST fail OPEN (do not drop) in that case.
* :func:`get_sync` — read-through: fills the cache on a miss with a blocking
  ``channel_repo.get``. Callers already run this off the event loop
  (``asyncio.to_thread``) — see ``GOWAChannel.parse_inbound``.

Same TTL (30s) and same fallback (``channels.jid.DEFAULT_ALLOWED_JID_TYPES``)
as the pre-existing per-channel cache. ⚠️ Do NOT change the fallback (plano 103
D2): a channel with no saved key must keep seeing ``group`` as allowed, or
every legacy GOWA channel would silently stop surfacing group chats.
"""

from __future__ import annotations

import json
import logging
import time

from channels import jid as jid_classifier

logger = logging.getLogger(__name__)

_TTL = 30.0
_CACHE: dict[str, tuple[list[str], float]] = {}


def _read_from_db(channel_id: str) -> list[str]:
    """Blocking DB read — same shape as the pre-plano-173 per-channel reader."""
    from db.repositories import channel_repo

    try:
        row = channel_repo.get(channel_id)
        cfg = row.get("config") if row else None
        if isinstance(cfg, str) and cfg:
            cfg = json.loads(cfg)
        if isinstance(cfg, dict) and "allowed_jid_types" in cfg:
            return jid_classifier.normalize_allowed_types(cfg.get("allowed_jid_types"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[jid_allowed] read failed for %s: %s", channel_id, e)
    return list(jid_classifier.DEFAULT_ALLOWED_JID_TYPES)


def peek(channel_id: str) -> list[str] | None:
    """Memory-only lookup — never does I/O. ``None`` on a cold/expired cache.

    The caller (``Channel.should_drop_inbound``) MUST fail open on ``None``: it
    means "no decision available yet", never "nothing is allowed".
    """
    cached = _CACHE.get(channel_id)
    if cached is None:
        return None
    types, ts = cached
    if (time.time() - ts) >= _TTL:
        return None
    return types


def get_sync(channel_id: str) -> list[str]:
    """Return the cached value, or fill it with one blocking read on a miss.

    Call off the event loop (``asyncio.to_thread``) — it may hit the DB.
    """
    types = peek(channel_id)
    if types is not None:
        return types
    types = _read_from_db(channel_id)
    _CACHE[channel_id] = (types, time.time())
    return types


def reset() -> None:
    """Invalidate every cached channel (call after a channel config edit)."""
    _CACHE.clear()
