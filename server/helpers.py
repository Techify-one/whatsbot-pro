"""Pure helper functions for the WhatsBot server."""

from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse


def _get_web_dir() -> Path:
    """Locate the web/ directory."""
    return Path(__file__).resolve().parent.parent / "web"


def _ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _mask_key(key: str) -> str:
    """Mask an API key for display (show first 8 + last 4 chars)."""
    if len(key) <= 12:
        return "*" * len(key)
    return key[:8] + "*" * (len(key) - 12) + key[-4:]
