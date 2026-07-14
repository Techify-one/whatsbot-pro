"""Session identity, channel lookup, allowed-domains and rate-limit (plano 46 · 05.2).

Token model (mirrors Chatwoot's semantics, not its code):
  * ``widget_token``  — PUBLIC id that lives in every site's HTML. Non-secret; it
    only SELECTS the channel. Stored in ``channels.config.widget_token`` (a
    ``generated`` config field, auto-minted at create).
  * ``session_token`` — opaque per-visitor id minted at ``GET config`` and stored
    in the widget cookie/localStorage. Authorises that visitor's REST + WS. It is
    the conversation ``chat_id`` (the "phone" analogue).
  * ``hmac_token``    — optional shared secret (channel credential). Validates a
    ``setUser`` identity so history follows a logged-in user across devices.

The pure helpers (domain parsing / frame-ancestors / HMAC compare) carry no DB
import so they unit-test in isolation; the DB-backed functions use the plugin's
own ``plugin_website_sessions`` table via the shared engine.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import time
from hashlib import sha256
from typing import Optional

logger = logging.getLogger(__name__)

PROVIDER = "website"


# ── Pure helpers (no DB — unit-testable) ──────────────────────────────────

def parse_allowed_domains(raw) -> list[str]:
    """Split the ``allowed_domains`` config value into a clean origin list.

    Accepts a comma / newline / whitespace separated string (or an already-split
    list). Trailing slashes and blanks are dropped. Empty result means "no
    restriction" (embed anywhere)."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        parts = str(raw).replace("\n", ",").replace(" ", ",").split(",")
    out: list[str] = []
    for p in parts:
        d = str(p).strip().rstrip("/")
        if d and d not in out:
            out.append(d)
    return out


def _origin_variants(domain: str) -> list[str]:
    """A CSP host-source list for one entry. A bare host (no scheme) becomes both
    http+https variants so ``localhost:5500`` and ``meusite.com`` both work."""
    d = domain.strip().rstrip("/")
    if not d:
        return []
    if "://" in d:
        return [d]
    return [f"http://{d}", f"https://{d}"]


def frame_ancestors_value(domains: list[str]) -> str:
    """Build the CSP ``frame-ancestors`` source list from allowed domains.

    Empty list → ``*`` (embed anywhere — the convenient default; restriction is
    opt-in). Otherwise ``'self'`` + each domain's origin variants."""
    if not domains:
        return "*"
    srcs = ["'self'"]
    for d in domains:
        for v in _origin_variants(d):
            if v not in srcs:
                srcs.append(v)
    return " ".join(srcs)


def content_security_policy(domains: list[str]) -> str:
    """Full CSP for the embeddable widget page: self-contained app + WS + the
    per-channel ``frame-ancestors`` gate that decides which sites may embed it."""
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' data: blob:; "
        "connect-src 'self' ws: wss:; "
        f"frame-ancestors {frame_ancestors_value(domains)}"
    )


def compute_identifier_hash(hmac_token: str, identifier: str) -> str:
    """HMAC-SHA256(hmac_token, identifier) hex — the ``setUser`` proof."""
    return hmac.new((hmac_token or "").encode("utf-8"),
                    (identifier or "").encode("utf-8"), sha256).hexdigest()


def verify_identifier_hash(hmac_token: str, identifier: str, provided: str) -> bool:
    """Constant-time check of a client-provided ``identifier_hash``."""
    if not hmac_token or not identifier or not provided:
        return False
    expected = compute_identifier_hash(hmac_token, identifier)
    return hmac.compare_digest(expected, str(provided).strip())


def new_session_token() -> str:
    return "wsess_" + secrets.token_urlsafe(24)


# ── Config / channel lookup (DB) ──────────────────────────────────────────

def channel_config(row: Optional[dict]) -> dict:
    """Parse a channel row's ``config`` (the repo may return a JSON string)."""
    if not row:
        return {}
    cfg = row.get("config")
    if isinstance(cfg, str) and cfg:
        try:
            cfg = json.loads(cfg)
        except Exception:  # noqa: BLE001
            return {}
    return cfg if isinstance(cfg, dict) else {}


def find_channel_by_widget_token(widget_token: str) -> Optional[dict]:
    """The enabled ``website`` channel whose ``config.widget_token`` matches, or None.

    ``widget_token`` is public but still SELECTS the channel — we scan the (few)
    website channels rather than trusting a channel id from the browser."""
    if not widget_token:
        return None
    from db.repositories import channel_repo
    for row in channel_repo.list_all():
        if row.get("provider") != PROVIDER:
            continue
        if not row.get("enabled", 1) or row.get("archived"):
            continue
        if channel_config(row).get("widget_token") == widget_token:
            return row
    return None


# ── Session table (DB) ────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def get_session(session_token: str) -> Optional[dict]:
    if not session_token:
        return None
    from plugins.context import make_plugin_db
    from sqlalchemy import text
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT session_token, channel_id, chat_id, identifier, "
                 "hmac_verified, created_at, last_seen "
                 "FROM plugin_website_sessions WHERE session_token = :t"),
            {"t": session_token}).mappings().first()
    return dict(row) if row else None


def mint_session(channel_id: str, ip: str = "") -> dict:
    """Insert a fresh visitor session. ``chat_id`` starts equal to the token."""
    token = new_session_token()
    now = _now()
    from plugins.context import make_plugin_db
    from sqlalchemy import text
    with make_plugin_db() as conn:
        conn.execute(
            text("INSERT INTO plugin_website_sessions "
                 "(session_token, channel_id, chat_id, hmac_verified, ip, "
                 " created_at, last_seen) "
                 "VALUES (:t, :c, :chat, 0, :ip, :now, :now)"),
            {"t": token, "c": channel_id, "chat": token, "ip": (ip or "")[:64],
             "now": now})
    return {"session_token": token, "channel_id": channel_id, "chat_id": token,
            "identifier": None, "hmac_verified": 0, "created_at": now,
            "last_seen": now}


def touch_session(session_token: str) -> None:
    from plugins.context import make_plugin_db
    from sqlalchemy import text
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_website_sessions SET last_seen = :now "
                 "WHERE session_token = :t"),
            {"now": _now(), "t": session_token})


def mark_identified(session_token: str, identifier: str) -> None:
    """Bind a verified ``identifier`` (HMAC setUser) to the session so its
    conversation history can follow the user across devices."""
    from plugins.context import make_plugin_db
    from sqlalchemy import text
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_website_sessions "
                 "SET identifier = :id, hmac_verified = 1, last_seen = :now "
                 "WHERE session_token = :t"),
            {"id": identifier, "now": _now(), "t": session_token})


# ── Rate limit (in-memory sliding window per IP / per session) ────────────
#
# Deliberately generous defaults (public endpoints still need SOME shield), and a
# per-channel ``rate_limit_enabled`` kill-switch — a load test that simulates
# many visitors from one machine just turns it off on the test channel.

_SESSION_HITS: dict[str, list[float]] = {}   # ip -> [timestamps] (new sessions)
_MESSAGE_HITS: dict[str, list[float]] = {}   # session_token -> [timestamps]
_NEW_SESSION_PER_MIN = 60
_MESSAGES_PER_MIN = 120
_WINDOW = 60.0


def _allow(store: dict, key: str, limit: int) -> bool:
    now = _now()
    # Bound memory: an in-memory rate store keyed by IP/session would otherwise
    # accumulate a key per distinct caller forever. Sweep expired keys when it grows.
    if len(store) > 4096:
        for k in [k for k, v in store.items() if not v or (now - v[-1]) >= _WINDOW]:
            store.pop(k, None)
    hits = [t for t in store.get(key, ()) if now - t < _WINDOW]
    if len(hits) >= limit:
        store[key] = hits
        return False
    hits.append(now)
    store[key] = hits
    return True


def allow_new_session(ip: str, enabled: bool = True) -> bool:
    if not enabled:
        return True
    return _allow(_SESSION_HITS, ip or "?", _NEW_SESSION_PER_MIN)


def allow_message(session_token: str, enabled: bool = True) -> bool:
    if not enabled:
        return True
    return _allow(_MESSAGE_HITS, session_token or "?", _MESSAGES_PER_MIN)
