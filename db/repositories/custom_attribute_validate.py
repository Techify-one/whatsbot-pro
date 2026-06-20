"""Type validation/normalization for custom attribute values (plano 05).

Each definition has a ``type``; values are validated against it before being
written into the entity's ``custom_attributes`` JSON. number is stored raw as a
JSON number (P52), checkbox as a JSON bool — not strings.
"""

from __future__ import annotations

import re
from typing import Any

VALID_TYPES = {"text", "number", "date", "list", "checkbox", "link"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def validate_value(definition: dict, value: Any) -> tuple[Any, str | None]:
    """Return (normalized_value, error_message). error is None when valid.

    ``None`` clears the attribute (always allowed unless required is enforced by
    the caller at submit time).
    """
    if value is None:
        return None, None

    attr_type = (definition.get("type") or "text").lower()
    key = definition.get("attribute_key", "?")

    if attr_type == "checkbox":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "sim", "on"), None
        if isinstance(value, (int, float)):
            return bool(value), None
        return None, f"'{key}' deve ser verdadeiro/falso."

    if attr_type == "number":
        if isinstance(value, bool):  # bool is an int subclass — reject explicitly
            return None, f"'{key}' deve ser um número."
        if isinstance(value, (int, float)):
            return float(value), None
        if isinstance(value, str):
            try:
                return float(value.strip().replace(",", ".")), None
            except ValueError:
                return None, f"'{key}' deve ser um número."
        return None, f"'{key}' deve ser um número."

    if attr_type == "date":
        s = str(value).strip()
        if not _DATE_RE.match(s):
            return None, f"'{key}' deve ser uma data no formato AAAA-MM-DD."
        return s, None

    if attr_type == "list":
        options = definition.get("options") or []
        if not isinstance(options, list):
            options = []
        s = str(value).strip()
        if s not in options:
            return None, f"'{key}' deve ser uma das opções: {', '.join(map(str, options))}."
        return s, None

    if attr_type == "link":
        s = str(value).strip()
        if s and not _URL_RE.match(s):
            return None, f"'{key}' deve ser uma URL (começando com http:// ou https://)."
        return s, None

    # text (default) — optional regex_pattern
    s = str(value)
    pattern = definition.get("regex_pattern")
    if pattern:
        try:
            if not re.search(pattern, s):
                cue = definition.get("regex_cue") or f"formato esperado: {pattern}"
                return None, f"'{key}' inválido ({cue})."
        except re.error:
            pass  # invalid stored regex → don't block the value
    return s, None
