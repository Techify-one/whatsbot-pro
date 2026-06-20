"""WhatsBot — FastAPI backend with REST API, WebSocket and background tasks."""

import asyncio
import dataclasses
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.auth import auth_required, verify_token, rbac_enforced, resolve_request_token
from server.helpers import _get_web_dir
from server.audit_listener import register_audit_listener
from server.audit_context import ActorCtx, set_current_actor, reset_current_actor
from server.state import MemoryLogHandler, ConnectionManager, AppState
from server.background import start_gowa_task, status_poll_loop, qr_poll_loop, avatar_fetch_task, audit_purge_loop
from server.routes import logs, sandbox, config, whatsapp, websocket, usage, contacts, webhook, auth, tags, executions, update, setup as setup_routes, plugins as plugins_routes, tools as tools_routes, admin as admin_routes, ai_engine as ai_engine_routes, quick_replies as quick_replies_routes, custom_attributes as custom_attributes_routes, runtime as runtime_routes, channels as channels_routes, channel_webhook as channel_webhook_routes, inboxes as inboxes_routes, users as users_routes, conversations as conversations_routes, audit as audit_routes
from db.repositories import tool_override_repo
from agent import group_mentions, agent_factory
from agent import ai_tool_installer
from plugins.loader import bootstrap_initial_plugins, discover_and_load, PluginRegistry
from plugins.context import set_runtime as _set_plugin_runtime, set_runtime_services as _set_runtime_services
from plugins.lifecycle import manager as _lifecycle_manager
from runtime.supervisor import TaskSupervisor, TaskSpec, RestartPolicy
from runtime.subprocess_service import SubprocessService
from channels.registry import ChannelRegistry
from channels.providers.gowa_channel import GOWAChannel
from db.repositories import channel_repo
from plugins.events import (
    set_runtime as _set_events_runtime,
    register_plugin_events,
    register_plugin_filters,
    emit as emit_event,
)
from server.balance_monitor import set_runtime as _set_balance_runtime

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
    plugins_dir: Path = None
    plugins_registry: PluginRegistry = None
    channel_registry: object = None
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

    # Plugin discovery + load. Runs synchronously before route registration so
    # plugin routes/tools/prompts are wired into the app before the first request.
    plugins_dir = settings.data_dir / "storages" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_initial_plugins(
        plugins_dir,
        settings.data_dir / "assets" / "plugin_examples",
    )
    registry = discover_and_load(plugins_dir)

    # Channel registry (plano 02 Fase 0). Core registers the internal GOWA
    # provider; plugins contribute providers via entry.channels. The live
    # webhook/send flow is NOT yet rerouted through this (incremental — §0.5/0.7).
    channel_registry = ChannelRegistry()
    channel_registry.register_provider(GOWAChannel)

    for loaded in registry.loaded.values():
        if loaded.tools:
            agent_handler.register_plugin_tools(loaded.id, loaded.tools)
        if loaded.prompt_fragments:
            agent_handler.register_plugin_prompts(loaded.id, loaded.prompt_fragments)
        if loaded.event_handlers:
            register_plugin_events(loaded.id, loaded.event_handlers)
        if loaded.filters:
            register_plugin_filters(loaded.id, loaded.filters)
        for provider_cls in getattr(loaded, "channel_providers", []):
            channel_registry.register_provider(provider_cls)

    # Instantiate the "default" gowa channel from its DB row (created by the
    # 0011_channels migration), wrapping the existing client/manager.
    try:
        default_row = channel_repo.get("default")
        if default_row is not None:
            channel_registry.add_channel(
                "default", GOWAChannel("default", gowa_client, gowa_manager))
    except Exception as e:
        logger.warning("Could not instantiate default channel: %s", e)

    # AI engine (config-in-DB + code-in-DB). Seed the default agent/prompt from
    # the current config (idempotent), then materialise/install/register the
    # DB-defined tools. Tools are registered AFTER core + plugin tools so the
    # registry's collision no-op gives precedence to code over the DB. Both
    # steps are best-effort: a failure never blocks the app from booting.
    try:
        agent_factory.seed_default_agent(settings)
    except Exception as e:
        logger.warning("AI engine seed failed: %s", e)
    # ⚠️ Security gate: code-in-DB tools. RBAC (plano 03) e o runner isolado (P62/P67)
    # já existem — o código do banco roda num SUBPROCESSO one-shot isolado, NÃO mais
    # in-process. Mesmo assim a feature fica OFF por default; só roda com opt-in explícito.
    if settings.get("ai_tools_code_enabled", False):
        try:
            ai_tool_installer.install_and_register(agent_handler, settings.data_dir)
        except Exception as e:
            logger.warning("AI tool installer failed: %s", e)
    else:
        logger.info(
            "AI engine: code-in-DB tools disabled (ai_tools_code_enabled=False) — "
            "skipping installer. Set WHATSBOT_AI_TOOLS_CODE=1 to enable on a trusted host."
        )

    # Tool override cleanup: drop rows for tools that no longer exist (renamed
    # in core, or belonging to a plugin that was removed). Then build the
    # effective tool list applied to the LLM.
    try:
        dropped = tool_override_repo.delete_orphans(agent_handler.known_tool_names())
        if dropped:
            logger.info("Removed %d orphan tool_overrides rows", dropped)
    except Exception as e:
        logger.warning("tool_overrides orphan cleanup failed: %s", e)
    agent_handler.refresh_tool_overrides()

    deps = ServerDeps(
        settings=settings,
        gowa_manager=gowa_manager,
        gowa_client=gowa_client,
        agent_handler=agent_handler,
        ws_manager=ws_manager,
        state=state,
        memory_log_handler=_memory_log_handler,
        statics_senditems_dir=statics_senditems_dir,
        plugins_dir=plugins_dir,
        plugins_registry=registry,
        channel_registry=channel_registry,
    )

    # Group @mention resolution service (members lookup + name/number mapping).
    group_mentions.init(gowa_client)

    # ── GOWA restart callback ──────────────────────────────────────────
    def _on_gowa_restart():
        gowa_client.reset()
        state.qr_data = None
        state.qr_fetched_at = 0
        state.bot_phone = ""
        state.bot_name = ""

    gowa_manager._on_restart = _on_gowa_restart

    # ── Lifespan ──────────────────────────────────────────────────────

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import time as _time
        state.stop_event.clear()
        _loop = asyncio.get_running_loop()
        _set_plugin_runtime(ws_manager, _loop)
        _set_events_runtime(_loop, agent_handler)
        register_audit_listener()  # plano 07: core "*" listener for the audit trail
        _set_balance_runtime(ws_manager, _loop, settings)
        # Lifecycle: plugins finished loading + bus is live, now broadcast
        for loaded in registry.loaded.values():
            emit_event("plugin.loaded", {
                "plugin_id": loaded.id,
                "version": loaded.manifest.version,
                "events": list(loaded.event_handlers.keys()),
                "filters": list(loaded.filters.keys()),
                "ts": _time.time(),
            })
        emit_event("app.startup", {
            "plugin_ids": list(registry.loaded.keys()),
            "ts": _time.time(),
        })

        # Background task supervisor (plano 09 Fase 3): the 4 core tasks now run
        # under classified restart + backoff instead of a bare create_task list.
        # Built + wired into the plugin runtime BEFORE plugin setup() runs, so a
        # plugin can register a supervised task via ctx.spawn_task() during setup
        # (plano 02 Fase 1 — channel providers register their polling loop there).
        supervisor = TaskSupervisor()
        supervisor.register(TaskSpec(
            "gowa_start", lambda: start_gowa_task(deps), policy=RestartPolicy.TRANSIENT))
        supervisor.register(TaskSpec(
            "status_poll", lambda: status_poll_loop(deps), policy=RestartPolicy.PERMANENT))
        supervisor.register(TaskSpec(
            "qr_poll", lambda: qr_poll_loop(deps), policy=RestartPolicy.PERMANENT))
        supervisor.register(TaskSpec(
            "avatar_fetch", lambda: avatar_fetch_task(deps), policy=RestartPolicy.PERMANENT))
        supervisor.register(TaskSpec(
            "audit_purge", lambda: audit_purge_loop(deps), policy=RestartPolicy.PERMANENT))
        state.task_supervisor = supervisor
        # Shared subprocess service for plugins (plano 09 Fase 5). GOWA keeps its
        # own ManagedProcess; this one tracks plugin-spawned subprocesses.
        subprocess_service = SubprocessService()
        state.subprocess_service = subprocess_service
        _set_runtime_services(supervisor, subprocess_service)

        # Plugin lifecycle (plano 09 Fase 1): call+await setup() for plugins that
        # declared entry.lifecycle. A failing setup() does not bring down the app.
        # Runs AFTER the supervisor is wired (above) so ctx.spawn_task works.
        for loaded in registry.loaded.values():
            if getattr(loaded, "setup_fn", None) or getattr(loaded, "teardown_fn", None):
                err = await _lifecycle_manager.run_setup(loaded, agent_handler, _loop)
                if err:
                    try:
                        from db.repositories import plugin_repo
                        plugin_repo.set_load_error(loaded.id, f"setup() failed: {err}")
                    except Exception:
                        pass

        await supervisor.start_all()
        yield
        # Shutdown — ordered (plano 09 Fase 2): app.shutdown → plugin teardown →
        # stop tasks (awaited) → save → stop subprocess.
        emit_event("app.shutdown", {"ts": _time.time()})
        state.stop_event.set()  # legacy compat: loops checking stop_event exit
        for loaded in list(registry.loaded.values()):
            await _lifecycle_manager.run_teardown(loaded.id)
        try:
            await supervisor.stop_all()  # cancel + await (fixes the old no-await gap)
        except Exception:
            pass
        try:
            subprocess_service.stop_all()  # stop plugin subprocesses (GOWA stopped below)
        except Exception:
            pass
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

    _docs_enabled = os.getenv("WHATSBOT_ENABLE_DOCS", "0") == "1"
    app = FastAPI(
        title="WhatsBot",
        lifespan=lifespan,
        docs_url="/docs" if _docs_enabled else None,
        redoc_url="/redoc" if _docs_enabled else None,
        openapi_url="/openapi.json" if _docs_enabled else None,
    )

    # Mount static files (frontend assets)
    static_dir = web_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount statics/ for GOWA media files (auto-downloaded images, audio, etc.)
    app.mount("/statics", StaticFiles(directory=str(statics_dir)), name="statics")

    # ── Auth middleware ────────────────────────────────────────────────

    # Paths exempt from authentication
    # NOTE: "/api/webhook/" (trailing slash) exempts per-provider webhooks
    # (Cloud API/Telegram authenticate themselves). The EXACT "/api/webhook"
    # (GOWA, no slash) and "/health" stay in _AUTH_EXEMPT_EXACT — INTOCÁVEIS.
    _AUTH_EXEMPT_PREFIXES = ("/static/", "/statics/", "/plugins/", "/api/auth/", "/api/webhook/")
    _AUTH_EXEMPT_EXACT = {"/api/webhook", "/health"}
    _PLUGIN_SPA_PATHS = {
        s["path"]
        for loaded in registry.loaded.values()
        for s in loaded.manifest.screens
        if s.get("path", "").startswith("/")
    }
    _SPA_PATHS = (
        {"/", "/painel", "/sandbox", "/costs", "/executions", "/plugins", "/quick-replies", "/custom-attributes", "/runtime", "/users", "/conversations", "/ai", "/channels", "/auditoria", "/wizard"}
        | _PLUGIN_SPA_PATHS
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path

        # SPA pages, static assets, webhook, and auth endpoints are always open
        if path in _SPA_PATHS or path.startswith(("/contacts/", "/executions/")):
            return await call_next(request)
        if path in _AUTH_EXEMPT_EXACT:
            return await call_next(request)
        for prefix in _AUTH_EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Only protect /api/* paths. RBAC is additive (plano 03): a valid USER
        # session OR the legacy single-password token both authenticate. When
        # rbac_enforce is on, ONLY a user session is accepted. Default off so a
        # live single-password / open install keeps working.
        request.state.user = None
        if path.startswith("/api/"):
            enforce = rbac_enforced(settings)
            auth_req = auth_required(settings)
            auth_header = request.headers.get("authorization", "")
            token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            # Resolve a present token even in open mode so per-permission gating
            # applies to voluntarily-logged-in users; only hit the DB when there's
            # a token or auth is actually required/enforced.
            if token or enforce or auth_req:
                kind, user = await asyncio.to_thread(
                    resolve_request_token, token, settings)
                request.state.user = user
                if enforce:
                    denied = kind != "user"
                elif auth_req:
                    denied = kind is None
                else:
                    denied = False  # open mode — token (if any) attached for gating
                if denied:
                    return JSONResponse(
                        {"ok": False, "error": "Não autenticado."},
                        status_code=401,
                    )

        # Audit actor (plano 07): real user when logged in, else system. The bus
        # `*` listener reads this via contextvar (snapshotted into create_task).
        import uuid as _uuid
        _u = request.state.user
        request.state.request_id = _uuid.uuid4().hex
        _xff = request.headers.get("x-forwarded-for", "")
        _ip = (_xff.split(",")[0].strip() if _xff
               else (request.client.host if request.client else None))
        _actor_token = set_current_actor(ActorCtx(
            id=(_u.get("id") if _u else None),
            type=("user" if _u else "system"),
            label=(_u.get("name") or _u.get("email") if _u else None),
            ip=_ip, request_id=request.state.request_id,
        ))
        try:
            return await call_next(request)
        finally:
            reset_current_actor(_actor_token)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
            "worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' data: blob:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'"
        )
        return resp

    # ── Health endpoint (always open, used by Docker healthcheck) ──────

    @app.get("/health")
    async def healthcheck():
        return JSONResponse({"ok": True})

    # ── Frontend routes ────────────────────────────────────────────────

    @app.get("/")
    @app.get("/painel")
    @app.get("/sandbox")
    @app.get("/costs")
    @app.get("/executions")
    @app.get("/plugins")
    @app.get("/quick-replies")
    @app.get("/custom-attributes")
    @app.get("/runtime")
    @app.get("/users")
    @app.get("/conversations")
    @app.get("/ai")
    @app.get("/channels")
    @app.get("/wizard")
    @app.get("/contacts/{contact_id:int}")
    @app.get("/executions/{execution_id:int}")
    async def index(contact_id: int | None = None, execution_id: int | None = None):
        index_file = web_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"error": "Frontend not found"}, status_code=404)

    # Register dynamic SPA paths declared by plugin manifests so the frontend
    # router gets the same index.html on hard reload of those URLs.
    async def _plugin_spa_index():
        index_file = web_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"error": "Frontend not found"}, status_code=404)

    for _spa_path in _PLUGIN_SPA_PATHS:
        app.add_api_route(_spa_path, _plugin_spa_index, methods=["GET"])

    # ── Register route modules ─────────────────────────────────────────
    # Order matters: webhook must be registered before sandbox so
    # broadcast_tool_calls is available via deps.
    auth.register_routes(app, deps)
    users_routes.register_routes(app, deps)
    conversations_routes.register_routes(app, deps)
    webhook.register_routes(app, deps)
    logs.register_routes(app, deps)
    sandbox.register_routes(app, deps)
    config.register_routes(app, deps)
    whatsapp.register_routes(app, deps)
    setup_routes.register_routes(app, deps)
    websocket.register_routes(app, deps)
    usage.register_routes(app, deps)
    contacts.register_routes(app, deps)
    tags.register_routes(app, deps)
    quick_replies_routes.register_routes(app, deps)
    custom_attributes_routes.register_routes(app, deps)
    runtime_routes.register_routes(app, deps)
    channels_routes.register_routes(app, deps)
    channel_webhook_routes.register_routes(app, deps)
    inboxes_routes.register_routes(app, deps)
    executions.register_routes(app, deps)
    update.register_routes(app, deps)
    plugins_routes.register_routes(app, deps)
    tools_routes.register_routes(app, deps)
    admin_routes.register_routes(app, deps)
    ai_engine_routes.register_routes(app, deps)
    audit_routes.register_routes(app, deps)

    # ── Plugin routers and static assets ──────────────────────────────
    for loaded in registry.loaded.values():
        if loaded.router is not None:
            app.include_router(loaded.router, prefix=f"/api/plugins/{loaded.id}")
        if loaded.static_dir is not None:
            app.mount(
                f"/plugins/{loaded.id}/static",
                StaticFiles(directory=str(loaded.static_dir)),
                name=f"plugin_{loaded.id}_static",
            )

    return app
