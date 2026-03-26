"""WhatsBot — FastAPI backend with REST API, WebSocket and background tasks."""

import asyncio
import dataclasses
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.auth import auth_required, verify_token
from server.helpers import _get_web_dir
from server.state import MemoryLogHandler, ConnectionManager, AppState
from server.background import start_gowa_task, status_poll_loop, qr_poll_loop
from server.routes import logs, sandbox, config, whatsapp, websocket, usage, contacts, webhook, auth

logger = logging.getLogger(__name__)

# ── In-memory log capture (attach to root logger) ────────────────────────

_memory_log_handler = MemoryLogHandler()
_memory_log_handler.setLevel(logging.DEBUG)
_root = logging.getLogger()
_root.addHandler(_memory_log_handler)
# Ensure root logger level allows INFO+ through to handlers
if _root.level == logging.NOTSET or _root.level > logging.INFO:
    _root.setLevel(logging.INFO)


# ── Server Dependencies ──────────────────────────────────────────────────

@dataclasses.dataclass
class ServerDeps:
    """Container for shared dependencies passed to route modules."""
    settings: object
    gowa_manager: object
    gowa_client: object
    agent_handler: object
    ws_manager: ConnectionManager
    state: AppState
    memory_log_handler: MemoryLogHandler
    statics_senditems_dir: Path
    # Dynamically set by webhook route for cross-module access
    broadcast_tool_calls: object = None


# ── Factory ───────────────────────────────────────────────────────────────

def create_app(
    settings,
    gowa_manager,
    gowa_client,
    agent_handler,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    ws_manager = ConnectionManager()
    state = AppState()
    web_dir = _get_web_dir()

    # Prepare statics directories
    statics_dir = settings.data_dir / "statics"
    statics_media_dir = statics_dir / "media"
    statics_senditems_dir = statics_dir / "senditems"
    statics_media_dir.mkdir(parents=True, exist_ok=True)
    statics_senditems_dir.mkdir(parents=True, exist_ok=True)

    deps = ServerDeps(
        settings=settings,
        gowa_manager=gowa_manager,
        gowa_client=gowa_client,
        agent_handler=agent_handler,
        ws_manager=ws_manager,
        state=state,
        memory_log_handler=_memory_log_handler,
        statics_senditems_dir=statics_senditems_dir,
    )

    # ── GOWA restart callback ──────────────────────────────────────────
    def _on_gowa_restart():
        gowa_client.reset()
        state.qr_data = None
        state.qr_fetched_at = 0

    gowa_manager._on_restart = _on_gowa_restart

    # ── Lifespan ──────────────────────────────────────────────────────

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.stop_event.clear()
        tasks = [
            asyncio.create_task(start_gowa_task(deps)),
            asyncio.create_task(status_poll_loop(deps)),
            asyncio.create_task(qr_poll_loop(deps)),
        ]
        yield
        # Shutdown
        state.stop_event.set()
        for task in tasks:
            task.cancel()
        try:
            settings.save()
        except Exception:
            pass
        try:
            gowa_manager.stop()
        except Exception:
            pass
        logger.info("Server shutdown complete.")

    # ── FastAPI App ───────────────────────────────────────────────────

    app = FastAPI(title="WhatsBot", lifespan=lifespan)

    # Mount static files (frontend assets)
    static_dir = web_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount statics/ for GOWA media files (auto-downloaded images, audio, etc.)
    app.mount("/statics", StaticFiles(directory=str(statics_dir)), name="statics")

    # ── Auth middleware ────────────────────────────────────────────────

    # Paths exempt from authentication
    _AUTH_EXEMPT_PREFIXES = ("/static/", "/statics/", "/api/webhook", "/api/auth/")
    _SPA_PATHS = {"/", "/dashboard", "/sandbox", "/costs"}

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path

        # SPA pages, static assets, webhook, and auth endpoints are always open
        if path in _SPA_PATHS or path.startswith(("/contacts/",)):
            return await call_next(request)
        for prefix in _AUTH_EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Only protect /api/* paths
        if path.startswith("/api/") and auth_required(settings):
            auth_header = request.headers.get("authorization", "")
            token = ""
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            if not token or not verify_token(token, settings):
                return JSONResponse(
                    {"ok": False, "error": "Não autenticado."},
                    status_code=401,
                )

        return await call_next(request)

    # ── Migrate contact IDs ────────────────────────────────────────────
    agent_handler.ensure_contact_ids()

    # ── Frontend routes ────────────────────────────────────────────────

    @app.get("/")
    @app.get("/dashboard")
    @app.get("/sandbox")
    @app.get("/costs")
    @app.get("/contacts/{contact_id:int}")
    async def index(contact_id: int | None = None):
        index_file = web_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"error": "Frontend not found"}, status_code=404)

    # ── Register route modules ─────────────────────────────────────────
    # Order matters: webhook must be registered before sandbox so
    # broadcast_tool_calls is available via deps.
    auth.register_routes(app, deps)
    webhook.register_routes(app, deps)
    logs.register_routes(app, deps)
    sandbox.register_routes(app, deps)
    config.register_routes(app, deps)
    whatsapp.register_routes(app, deps)
    websocket.register_routes(app, deps)
    usage.register_routes(app, deps)
    contacts.register_routes(app, deps)

    return app
