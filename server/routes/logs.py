"""Log endpoints."""

import asyncio

from db.repositories import execution_repo
from server.helpers import _ok


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
