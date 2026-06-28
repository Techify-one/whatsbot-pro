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
from typing import Any, Iterable, Mapping


def coerce_json(value: Any, default: Any = None) -> Any:
    """Decode a JSON column value into a Python object, tolerating both backends.

    Postgres returns native ``dict``/``list`` for JSON/JSONB columns; SQLite stores
    the same data as a TEXT string. A missing or malformed value yields ``default``.
    Non-string, non-None values (already-decoded dict/list/number/bool) pass through.
    """
    if value is None:
        return default
    if isinstance(value, str):
        if value == "":
            return default
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError, TypeError):
            return default
    return value


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

# Roles excluded from the last-message preview of a conversation/contact row.
# Purely internal/painel-only roles never become the sidebar "last message".
# Shared by contact_repo.list_contacts (raw SQL) and conversation_repo (Core).
_PREVIEW_EXCLUDED = ("transcription", "system_notice", "conversation_event", "system")


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
