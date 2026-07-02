"""Pure helper functions for the WhatsBot server."""

import json
import re
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse


def _get_web_dir() -> Path:
    """Locate the web/ directory."""
    return Path(__file__).resolve().parent.parent / "web"


def _ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}


def _err(message: str, status: int = 400, data: Any = None) -> JSONResponse:
    body = {"ok": False, "error": message}
    if data is not None:
        body["data"] = data
    return JSONResponse(body, status_code=status)


def _mask_key(key: str) -> str:
    """Mask an API key for display (show first 8 + last 4 chars)."""
    if len(key) <= 12:
        return "*" * len(key)
    return key[:8] + "*" * (len(key) - 12) + key[-4:]


_JSON_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_JSON_TAIL_RE = re.compile(r'"((?:[^"\\]|\\.)*)$')


def _salvage_split_array(text: str) -> list[str] | None:
    """Best-effort recovery of a malformed/truncated split_messages JSON array.

    The model sometimes starts emitting the ``["..."]`` split format and gets cut
    off mid-string — e.g. a tool call interrupts the text, or ``max_tokens`` is
    hit — leaving ``["Olá! Como pos`` with no closing quote/bracket. ``json.loads``
    then fails and the raw ``["`` markup would leak to the user. Here we pull out
    the readable string content instead. Only kicks in for text that clearly began
    as a JSON string array (``["``); returns ``None`` when nothing readable is
    recovered so the caller keeps its plain fallback.
    """
    if not text.startswith('["'):
        return None
    parts: list[str] = []
    last = None
    for match in _JSON_STR_RE.finditer(text):
        last = match
        frag = match.group(1)
        try:
            parts.append(json.loads('"' + frag + '"'))
        except (json.JSONDecodeError, ValueError):
            parts.append(frag)
    # Recover a trailing, unterminated string (the truncation case) from whatever
    # comes after the last complete "..." literal.
    rest = text[last.end():] if last else text
    tail = _JSON_TAIL_RE.search(rest)
    if tail and tail.group(1).strip():
        frag = tail.group(1)
        try:
            parts.append(json.loads('"' + frag + '"'))
        except (json.JSONDecodeError, ValueError):
            parts.append(frag)
    cleaned = [p.strip() for p in parts if p and p.strip()]
    return cleaned or None


def parse_split_reply(reply: str) -> list[str]:
    """Parse LLM reply as JSON array of strings. Fallback to single message."""
    text = reply.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()
    if text.startswith("["):
        try:
            parts = json.loads(text)
            if isinstance(parts, list) and all(isinstance(p, str) for p in parts):
                # Valid split array — send exactly its non-empty parts. An empty or
                # whitespace-only array (e.g. "[]" emitted by a pure-handoff hop
                # with no user-facing text) yields [] so the caller sends and saves
                # nothing, instead of leaking a literal "[]" message.
                return [p.strip() for p in parts if p.strip()]
        except (json.JSONDecodeError, TypeError):
            # Malformed/truncated array — recover the text instead of leaking
            # raw JSON markup (``["``) to the user.
            salvaged = _salvage_split_array(text)
            if salvaged:
                return salvaged
    return [reply]
