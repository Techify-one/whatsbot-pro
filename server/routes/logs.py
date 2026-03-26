"""Log endpoints."""

from server.helpers import _ok


def register_routes(app, deps):
    memory_log_handler = deps.memory_log_handler

    @app.get("/api/logs")
    async def get_logs(limit: int = 200):
        """Return recent log entries from the in-memory buffer."""
        return _ok(memory_log_handler.get_logs(limit))

    @app.delete("/api/logs")
    async def clear_logs():
        memory_log_handler.clear()
        return _ok({"message": "Logs limpos."})
