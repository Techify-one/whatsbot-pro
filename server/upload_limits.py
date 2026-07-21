"""Upload size/quantity ceilings (plano 64 · F2).

Until the media routes stream to disk (they still do ``await file.read()``, i.e.
the whole file lands in RAM — plano 64 P2), an unbounded upload is a trivial way
to take the server down. Starlette does not cap a file part and there is no
proxy-level ``client_max_body_size`` in the dev/Docker setup, so the ceiling is
enforced here.

Two layers, on purpose: the browser refuses oversized files instantly (good UX)
and this middleware refuses them again server-side (the client is bypassable).
"""

from __future__ import annotations

import re

from fastapi.responses import JSONResponse

# 50 MB/file, 10 files per drop. WhatsApp itself accepts ~16 MB of media and
# ~100 MB of document; 50 MB is the safe middle ground while uploads are still
# buffered in memory. Mirrored in web/static/js/services/uploadLimits.js.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_FILES_PER_DROP = 10

# Paths whose body is a file upload. Anchored on the route suffix so the
# per-phone prefix (and the sandbox equivalents) are covered by one pattern.
_UPLOAD_PATH_RE = re.compile(
    r"^/api/(?:"
    r"contacts/[^/]+/(?:send-image|send-audio|send-video|send-document"
    r"|private-audio|private-image|private-document)"
    r"|contacts/import"
    r"|sandbox/(?:send-image|send-audio|send-document)"
    r")/?$"
)


def is_upload_path(path: str) -> bool:
    return bool(_UPLOAD_PATH_RE.match(path))


def too_large_response(limit_bytes: int = MAX_UPLOAD_BYTES) -> JSONResponse:
    mb = limit_bytes // (1024 * 1024)
    return JSONResponse(
        {"ok": False, "error": f"Arquivo excede o limite de {mb} MB."},
        status_code=413,
    )
