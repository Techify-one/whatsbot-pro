"""Semver range parsing + matching for plugin manifests (plano 23 Fase C4).

Extracted verbatim from ``plugins.manifest`` so the WhatsBot-API-version
compatibility check (and the manifest's plain-semver validation) lives in one
place. ``manifest.py`` re-exports these names, so existing import paths keep
working.

Supports the restricted subset the manifest actually uses:

* ``_is_semver`` — a strict ``MAJOR.MINOR.PATCH`` (optional prerelease/build).
* ``parse_simple_semver`` — best-effort ``(major, minor, patch)`` tuple.
* ``check_api_compat`` — ``*`` | plain version | comma-separated comparators
  (``>=, <=, >, <, ==, !=``) such as ``">=1.0,<2.0"``.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# The running WhatsBot plugin API version. ``manifest.WHATSBOT_API_VERSION`` is
# the canonical public name; it is kept in sync with this default so the two
# modules never disagree.
# 1.1.0: ADDITIVE — the plugin→plugin service seam (``entry.services`` +
# ``uses_services``, see plugins/services.py). Every existing manifest declares
# ``>=1.0,<2.0`` and stays compatible. ⚠️ A plugin that declares ``>=1.1`` FAILS
# HARD on an older core (manifest raises ⇒ load_error), so only declare it when
# the plugin is useless without services.
WHATSBOT_API_VERSION = "1.1.0"

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].*)?$")
_COMPARATOR_RE = re.compile(r"^(>=|<=|>|<|==|!=)\s*(\d+(?:\.\d+){0,2}(?:[-+].*)?)$")


def is_semver(value: str) -> bool:
    """True for a strict ``MAJOR.MINOR.PATCH`` (optional ``-pre``/``+build``)."""
    return bool(_SEMVER_RE.match(value))


def parse_simple_semver(value: str) -> tuple[int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH`` ignoring prerelease/build.

    Missing components default to 0; non-numeric input degrades to ``(0, 0, 0)``.
    """
    core = re.split(r"[-+]", value, maxsplit=1)[0]
    parts = core.split(".")
    if len(parts) < 3:
        parts += ["0"] * (3 - len(parts))
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return 0, 0, 0


def check_api_compat(spec: str, current: str = WHATSBOT_API_VERSION) -> bool:
    """Check whether ``current`` satisfies the constraint expression ``spec``.

    Supports ``*``, plain version (``1.0.0``), and comma-separated comparators
    ``>=, <=, >, <, ==, !=`` such as ``">=1.0,<2.0"``.
    """
    spec = (spec or "*").strip()
    if spec in ("", "*"):
        return True
    cur = parse_simple_semver(current)
    # plain version → exact match on MAJOR.MINOR.PATCH
    if is_semver(spec):
        return cur == parse_simple_semver(spec)
    parts = [p.strip() for p in spec.split(",")]
    for part in parts:
        m = _COMPARATOR_RE.match(part)
        if not m:
            logger.warning("Unrecognized version constraint: %r", part)
            return False
        op, ver = m.group(1), m.group(2)
        target = parse_simple_semver(ver)
        if op == ">=" and not cur >= target: return False
        if op == "<=" and not cur <= target: return False
        if op == ">"  and not cur >  target: return False
        if op == "<"  and not cur <  target: return False
        if op == "==" and not cur == target: return False
        if op == "!=" and not cur != target: return False
    return True
