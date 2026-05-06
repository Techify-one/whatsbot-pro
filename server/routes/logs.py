"""Log endpoints."""

import asyncio
import os
from collections import deque
from pathlib import Path

from db.repositories import execution_repo
from server.helpers import _ok


def _gowa_log_path() -> Path:
    base = Path(__file__).resolve().parent.parent.parent
    return base / "logs" / "gowa.log"


def _read_tail(path: Path, max_lines: int) -> list[str]:
    """Return last max_lines of file (best-effort, plain decode)."""
    if not path.exists():
        return []
    try:
        with path.open("rb") as f:
            return [
                line.decode("utf-8", errors="replace").rstrip("\r\n")
                for line in deque(f, maxlen=max_lines)
            ]
    except OSError:
        return []


def register_routes(app, deps):
    memory_log_handler = deps.memory_log_handler
    state = deps.state

    @app.get("/api/logs")
    async def get_logs(limit: int = 200):
        """Return recent log entries from the in-memory buffer."""
        return _ok(memory_log_handler.get_logs(limit))

    @app.delete("/api/logs")
    async def clear_logs():
        memory_log_handler.clear()
        return _ok({"message": "Logs limpos."})

    @app.get("/api/webhook-payloads")
    async def get_webhook_payloads(limit: int = 50):
        """Return last N raw webhook payloads (from DB executions, fallback to in-memory)."""
        try:
            entries = await asyncio.to_thread(execution_repo.get_webhook_payloads, limit)
            if entries:
                return _ok(entries)
        except Exception:
            pass
        # Fallback to in-memory deque
        entries = list(state.webhook_payloads)
        return _ok(entries[-limit:])

    @app.get("/api/gowa-logs")
    async def get_gowa_logs(limit: int = 500):
        """Return last N lines of the GOWA stdout/stderr debug log.

        Active only when WHATSBOT_GOWA_DEBUG=1 is set in the environment.
        """
        limit = max(1, min(limit, 5000))
        path = _gowa_log_path()
        debug_on = os.environ.get("WHATSBOT_GOWA_DEBUG", "").strip().lower() in {
            "1", "true", "yes", "on"
        }
        lines = await asyncio.to_thread(_read_tail, path, limit)
        return _ok({
            "debug_enabled": debug_on,
            "log_path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "lines": lines,
        })
