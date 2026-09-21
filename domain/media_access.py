"""Short-lived public grants for providers that pull local outbound media."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit


_LOCK = threading.Lock()
_PUBLIC_UNTIL: dict[str, float] = {}
_MARKER = "statics/outbox/"


def _outbox_key(value) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        path = urlsplit(text).path
    except ValueError:
        path = text
    marker_at = path.find(_MARKER)
    if marker_at < 0:
        return None
    name = path[marker_at + len(_MARKER):]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    return _MARKER + name


def grant_public_outbox(value, *, ttl_seconds: float = 120.0) -> None:
    """Allow one local outbox path to be fetched while a provider sends it."""
    key = _outbox_key(value)
    if key is None:
        return
    deadline = time.monotonic() + max(1.0, float(ttl_seconds))
    with _LOCK:
        _PUBLIC_UNTIL[key] = max(deadline, _PUBLIC_UNTIL.get(key, 0.0))


def grant_public_outbox_payload(value) -> None:
    """Find local media references recursively in a provider payload."""
    if isinstance(value, dict):
        for item in value.values():
            grant_public_outbox_payload(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            grant_public_outbox_payload(item)
    elif isinstance(value, str):
        grant_public_outbox(value)


def public_outbox_granted(value) -> bool:
    key = _outbox_key(value)
    if key is None:
        return False
    now = time.monotonic()
    with _LOCK:
        expired = [item for item, deadline in _PUBLIC_UNTIL.items()
                   if deadline <= now]
        for item in expired:
            _PUBLIC_UNTIL.pop(item, None)
        return _PUBLIC_UNTIL.get(key, 0.0) > now
