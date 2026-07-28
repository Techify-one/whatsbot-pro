"""On-disk naming for operator-uploaded media (plano 64 · F1).

Two problems the previous ``f"{ms}{suffix}"`` scheme had:

1. **Collision** — the only entropy was the millisecond, so two uploads landing
   in the same millisecond (trivial once a drop sends N files) silently
   overwrote each other.
2. **Stored XSS** — the extension came from the *client-supplied* filename, so
   ``x.html`` / ``x.svg`` landed under ``statics/outbox/`` and was served
   same-origin as ``text/html`` (the app's CSP allows ``'unsafe-inline'``).
   ``nosniff`` does not help: the type is correctly guessed from the extension.

So the on-disk name is ``{ms}_{uuid4[:8]}{ext}`` where ``ext`` is derived from
the **validated MIME type**, never from the client's filename, and executable-
in-the-browser extensions are neutralised to ``.bin``. The original filename is
kept separately (it is what the channel shows the customer) — only the *disk*
name is rewritten.
"""

from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path

# Extensions a browser would execute/render as an active document if served
# from our own origin. Never used on disk, whatever the MIME says.
DANGEROUS_EXTENSIONS = {
    ".html", ".htm", ".xhtml", ".xht", ".shtml", ".mhtml", ".mht",
    ".svg", ".svgz", ".xml", ".xsl", ".xslt",
    ".js", ".mjs", ".cjs", ".css",
    ".php", ".phtml", ".asp", ".aspx", ".jsp", ".jspx", ".cgi", ".pl",
}

# `mimetypes` picks some unhelpful aliases (audio/ogg → .oga). Pin the ones we
# actually round-trip through the channels so the file keeps the extension the
# providers expect.
_MIME_EXT_OVERRIDE = {
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "image/jpeg": ".jpg",
    "video/quicktime": ".mov",
}

# Generic types that carry no real information — fall through to the client's
# suffix (sanitised) instead of writing everything as ".bin".
_GENERIC_MIMES = {"application/octet-stream", "binary/octet-stream", ""}

_SAFE_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def extension_for(content_type: str | None, filename: str | None,
                  default: str = ".bin") -> str:
    """Resolve the on-disk extension: MIME first, client suffix only as fallback.

    The client's suffix is consulted **only** when the MIME type is absent or
    generic, and even then it is validated (short, alphanumeric) and screened
    against :data:`DANGEROUS_EXTENSIONS`.
    """
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime and mime not in _GENERIC_MIMES:
        ext = _MIME_EXT_OVERRIDE.get(mime) or mimetypes.guess_extension(mime)
        if ext:
            return default if ext.lower() in DANGEROUS_EXTENSIONS else ext

    suffix = Path(filename or "").suffix
    if suffix and _SAFE_SUFFIX_RE.match(suffix):
        return default if suffix.lower() in DANGEROUS_EXTENSIONS else suffix
    return default


def unique_media_name(content_type: str | None, filename: str | None, *,
                      default_ext: str = ".bin", timestamp_ms: int | None = None) -> str:
    """Build a collision-free on-disk name: ``{ms}_{uuid4[:8]}{ext}``."""
    import time

    ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    ext = extension_for(content_type, filename, default=default_ext)
    return f"{ms}_{uuid.uuid4().hex[:8]}{ext}"
