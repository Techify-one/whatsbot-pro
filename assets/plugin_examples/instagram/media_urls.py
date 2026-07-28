"""Public URL for a locally-stored media file (plano 46 · 01-B, D4).

Most providers take a LOCAL path when sending media (GOWA reads the file, the
WhatsApp Cloud API uploads it first). The Meta Messenger/Instagram send API takes
neither: it wants a **publicly reachable URL** that Meta's servers fetch
themselves (``{"attachment":{"payload":{"url": …}}}``).

The WhatsBot already serves ``statics/`` at ``/statics`` and already knows the
URL the operator uses to reach the panel — the global config key
``public_base_url``, captured/self-healed on ``GET /api/config`` (see
``server/routes/config.py``). This module is the tiny bridge between the two, so
a provider never re-implements it and the core never gains a provider branch.

Pure except for one config read (cached by the caller's own usage pattern — the
lookup is a single indexed row).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def public_base_url() -> str:
    """The panel's public base URL (no trailing slash), or ``""`` when unknown.

    Reads the global config key ``public_base_url``. Empty on a fresh install that
    was never opened in a browser — callers must degrade instead of building a
    broken link.
    """
    try:
        from db.repositories import config_repo

        return (config_repo.get("public_base_url", "") or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001
        logger.debug("public_base_url lookup failed", exc_info=True)
        return ""


def public_media_url(path_or_url: str, *, base: str = "") -> str:
    """Turn a local media path into a URL Meta (or any third party) can fetch.

    * An input that is ALREADY an ``http(s)://`` URL is returned untouched.
    * A local path is anchored at its ``statics/`` component (the only directory
      the app serves publicly), e.g. ``statics/outbox/x.jpg`` or an absolute
      ``/app/statics/outbox/x.jpg`` → ``{base}/statics/outbox/x.jpg``.
    * Returns ``""`` when there is no ``public_base_url`` configured or the path
      is outside ``statics/`` — the caller must then fail with a clear message
      instead of sending a link nobody can open.

    ``base`` overrides the configured public base URL (tests / callers that
    already resolved it).
    """
    raw = (path_or_url or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return raw

    normalized = raw.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    try:
        idx = len(parts) - 1 - parts[::-1].index("statics")
    except ValueError:
        logger.warning("public_media_url: caminho fora de statics/: %r", path_or_url)
        return ""
    rel = "/".join(parts[idx:])

    root = (base or "").strip().rstrip("/") or public_base_url()
    if not root:
        logger.warning("public_media_url: public_base_url não configurada — não é "
                       "possível montar link público para %r", path_or_url)
        return ""
    return f"{root}/{rel}"


def local_media_exists(path_or_url: str) -> bool:
    """Whether the input is an existing local file (vs. a URL / missing path)."""
    raw = (path_or_url or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return False
    try:
        return os.path.isfile(raw)
    except OSError:
        return False
