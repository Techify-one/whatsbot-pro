"""Shared row→dict mapping helpers for the repositories (plano 23 Fase E1, R10b/R11).

Centralizes three patterns that were copy-pasted across the data-access layer:

- ``coerce_json`` — the repeated ``json.loads(x) if isinstance(x, str) else x``
  dance (a JSON column comes back as a ``dict``/``list`` on Postgres but as a raw
  ``str`` on SQLite), with a fallback when the value is missing or malformed.
- ``row_to_dict`` — turn a SQLAlchemy mapping row into a plain ``dict``, decoding
  the named JSON columns via ``coerce_json``.
- ``_PREVIEW_EXCLUDED`` + ``media_preview`` — the last-visible-message preview
  label (roles hidden from the sidebar preview + the image/audio short labels),
  shared by the contact-centric and conversation-centric list queries.

These helpers must not change any observable output shape — they only remove
duplication. Each repo keeps owning its exact JSON-field list and key set.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

# Max unwrap iterations for a double/triple-encoded value (plano 34 F1). A guard
# against a pathological input that keeps re-parsing to another JSON string; real
# corruption seen in the wild is one extra layer (a JSON string of a JSON object).
_MAX_JSON_UNWRAP = 5


def coerce_json(value: Any, default: Any = None) -> Any:
    """Decode a JSON column value into a Python object, tolerating both backends.

    Postgres returns native ``dict``/``list`` for JSON/JSONB columns; SQLite (and
    the hand-serialized TEXT columns) store the same data as a string. A missing or
    malformed value yields ``default``. Non-string, non-None values (already-decoded
    dict/list/number/bool) pass through untouched.

    Plano 34 F1 — **N-layer tolerance:** a *double/triple-encoded* value (a JSON
    string whose content is itself JSON, e.g. ``'"{\\"model\\": 1}"'``) is peeled
    layer by layer until it stabilizes into a real object. If, after peeling, the
    result is *still* a ``str`` (either the input was never valid JSON, or it
    unwrapped down to a plain non-JSON string), we log a warning and return
    ``default`` — no consumer of ``coerce_json`` expects a final ``str``, so
    letting one through is what used to blow up ``build_for_contact`` on a dict().
    """
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    if value == "":
        return default

    result: Any = value
    for _ in range(_MAX_JSON_UNWRAP):
        if not isinstance(result, str):
            break
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, ValueError, TypeError):
            break

    if isinstance(result, str):
        # Never hand a raw string to a caller that expects dict/list/number.
        snippet = value if len(value) <= 80 else value[:77] + "..."
        logger.warning(
            "coerce_json: value did not decode to an object (returning default): %r",
            snippet,
        )
        return default
    return result


def row_to_dict(row: Mapping, json_fields: Iterable[str] = ()) -> dict:
    """Convert a SQLAlchemy mapping row to a plain ``dict``.

    Each name in ``json_fields`` is decoded via :func:`coerce_json` (default
    ``None`` — pass a per-repo default by decoding the field yourself if you need
    ``{}``/``[]``). Other columns are copied verbatim.
    """
    d = dict(row)
    for field in json_fields:
        if field in d:
            d[field] = coerce_json(d[field])
    return d


# ── Last-message preview (R11) ─────────────────────────────────────────────

# Canonical set of PANEL-ONLY message roles (plano 28 Fase 0). These never leave
# the panel (they are not sent to WhatsApp and render as centered cards), so they
# must NEVER become a conversation's "last message" preview NOR trigger a list
# (``conversation_upsert``) broadcast. Single source of truth: the DB preview
# subqueries (``conversation_query`` + ``contact_search``), the ``conversation_upsert``
# emit gate (``agent.memory``) and the frontend list-preview skip
# (``useConversationWsEvents.js``) must all agree on this set.
#
# Before plano 28 there were THREE divergent sets: the DB preview excluded only 4
# (missing ``tool_call``/``private_note``/``error`` → those leaked into the preview),
# the LLM-context exclusion 5, and the frontend 6. This is their UNION (7), which
# also fixes the pre-existing preview leak.
LIST_PANEL_ONLY_ROLES = (
    "transcription", "system_notice", "conversation_event", "system",
    "tool_call", "private_note", "error",
)

# Backwards-compatible alias: existing importers (``conversation_query``,
# ``contact_search``) keep the old name, now pointing at the canonical set.
_PREVIEW_EXCLUDED = LIST_PANEL_ONLY_ROLES


def media_preview(content: str | None, media_type: str | None) -> str:
    """Short sidebar preview label for the last visible message.

    Mirrors the behavior shared by the contact-centric and conversation-centric
    list queries: ``None`` content (no visible message) → empty; an ``image`` →
    the caption trimmed to 80 chars, or "📷 Imagem" when there is none; an
    ``audio`` → "🎤 Áudio"; anything else → the content trimmed to 80 chars.
    """
    if content is None:
        return ""
    if media_type == "image":
        return (content or "")[:80] or "\U0001f4f7 Imagem"
    if media_type == "audio":
        return "\U0001f3a4 Áudio"
    return (content or "")[:80]
