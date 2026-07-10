"""Blacklist regex filter for the AI history (plano 43).

Pure, best-effort, fail-open. Reads a GLOBAL config list of regex patterns
(``ai_history_exclude_patterns``) and cuts any history row whose ``role<TAB>content``
matches one of them, BEFORE the history becomes LLM context. Default empty list =
nothing cut = byte-identical to the previous behaviour.

Motivation: automations (e.g. the ``protocolos`` plugin) write ``private_note`` rows
(``🔖 Protocolo aberto · PROT-…``) that today enter the LLM context
(``agent/memory.py`` wraps them as ``[Nota privada do operador]:``) and DUPLICATE the
compact ``tool_memory`` block. A global regex blacklist lets the operator cut anything
an automation injects — by role, by content, or both.

Design (plano 43):
- Global config key (D1), not per-channel.
- Match target ``f"{role}\t{content}"`` with ``re.search`` (D3): anchor by role
  (``^private_note\t``), by content (``Protocolo aberto``), or both
  (``^assistant\t.*PROT-``). A literal TAB in a WhatsApp message is vanishingly rare,
  so the role anchor is reliable.
- Compile cache with a 30s TTL (same pattern as ``channels.ai_settings._TTL``). An edit
  takes ≤30s to apply — and ``PUT /api/config`` resets the cache immediately.
- Fail-open at every level: a bad config, a bad regex, or any error → the history passes
  through intact so the AI is never left mute. Individual invalid patterns are skipped
  (logged), the valid ones keep working.
"""

from __future__ import annotations

import logging
import re
import time

from db.repositories import config_repo

logger = logging.getLogger(__name__)

# Global config key holding the list[str] of regex patterns.
CONFIG_KEY = "ai_history_exclude_patterns"
# Over-fetch window (D4): when patterns are set the SQL can't filter, so the repo reads
# up to this many rows, filters in Python, then keeps the newest ``limit`` survivors —
# so cutting rows never shrinks the useful context below ``max_context_messages``.
HISTORY_FETCH_CAP = 200
# Separator between role and content in the match target (D3).
_SEP = "\t"
# Compile-cache TTL, mirroring channels.ai_settings.
_TTL = 30.0

# Module-level compile cache. ``compiled is None`` ⇒ "not loaded yet"; an empty list is a
# valid loaded state (no patterns configured).
_CACHE: dict = {"compiled": None, "ts": 0.0}


def _read_patterns() -> list[str]:
    """Read the configured patterns as a list of non-empty strings (fail-open)."""
    try:
        raw = config_repo.get(CONFIG_KEY, [])
    except Exception as e:  # noqa: BLE001 — a config read must never break a turn
        logger.warning("[history_filter] config read failed (fail-open): %s", e)
        return []
    # Tolerate a single newline-separated string (legacy / hand-edited value).
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, (list, tuple)):
        return []
    return [p for p in raw if isinstance(p, str) and p.strip()]


def _compile(patterns: list[str]) -> list[re.Pattern]:
    """Compile each pattern, skipping (with a warning) the invalid ones (D6)."""
    compiled: list[re.Pattern] = []
    for p in patterns:
        try:
            compiled.append(re.compile(p))
        except re.error as e:
            logger.warning("[history_filter] invalid regex ignored: %r (%s)", p, e)
    return compiled


def load_compiled() -> list[re.Pattern]:
    """Compiled patterns for the history blacklist (cached, TTL 30s, fail-open).

    An empty list ⇒ no filtering — callers then take the byte-identical path. Never
    raises: any failure degrades to ``[]`` (history passes through).
    """
    try:
        now = time.time()
        cached = _CACHE["compiled"]
        if cached is not None and (now - _CACHE["ts"]) < _TTL:
            return cached
        compiled = _compile(_read_patterns())
        _CACHE["compiled"] = compiled
        _CACHE["ts"] = now
        return compiled
    except Exception as e:  # noqa: BLE001
        logger.warning("[history_filter] load_compiled failed (fail-open): %s", e)
        return []


def reset_cache() -> None:
    """Invalidate the compile cache. Called after the config is edited (PUT /api/config)."""
    _CACHE["compiled"] = None
    _CACHE["ts"] = 0.0


def matches(role: str, content: str, compiled: list[re.Pattern]) -> bool:
    """True if ``f"{role}\t{content}"`` matches any compiled pattern (``re.search``).

    No patterns ⇒ always False. A pathological single pattern that raises at match time
    is skipped, never propagated (fail-open).
    """
    if not compiled:
        return False
    target = f"{role or ''}{_SEP}{content or ''}"
    for pat in compiled:
        try:
            if pat.search(target):
                return True
        except Exception:  # noqa: BLE001 — one bad pattern must not break the turn
            continue
    return False


def filter_rows(rows: list[dict], compiled: list[re.Pattern]) -> list[dict]:
    """Drop the rows whose ``role``/``content`` match a pattern.

    No patterns / empty input ⇒ the input is returned intact (same object). Fail-open:
    any error returns the original ``rows``.
    """
    if not compiled or not rows:
        return rows
    try:
        return [r for r in rows
                if not matches(r.get("role", ""), r.get("content") or "", compiled)]
    except Exception as e:  # noqa: BLE001
        logger.warning("[history_filter] filter_rows failed (fail-open): %s", e)
        return rows
