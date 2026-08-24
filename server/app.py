"""WhatsBot — FastAPI backend with REST API, WebSocket and background tasks."""

import asyncio
import dataclasses
import mimetypes
import logging
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.auth import rbac_enforced, resolve_request_token
from server.api_keys import KEY_HEADER as API_KEY_HEADER, resolve_api_key
from server.helpers import _get_web_dir
from server.audit_listener import register_audit_listener
from server.webhook_dispatcher import register_webhook_listener
from server.audit_context import ActorCtx, set_current_actor, reset_current_actor
from server.client_ip import audit_ip
from server.state import MemoryLogHandler, ConnectionManager, AppState
from server.background import (audit_purge_loop, empty_conversation_sweep_loop,
                               webhook_delivery_loop)
from server.routes import logs, sandbox, config, whatsapp, websocket, usage, contacts, webhook, auth, tags, executions, setup as setup_routes, plugins as plugins_routes, tools as tools_routes, admin as admin_routes, ai_engine as ai_engine_routes, quick_replies as quick_replies_routes, custom_attributes as custom_attributes_routes, runtime as runtime_routes, channels as channels_routes, channel_webhook as channel_webhook_routes, inboxes as inboxes_routes, users as users_routes, roles as roles_routes, conversations as conversations_routes, conversation_labels as conversation_labels_routes, saved_filters as saved_filters_routes, sound_prefs as sound_prefs_routes, account as account_routes, audit as audit_routes, api_keys as api_keys_routes, webhooks_out as webhooks_out_routes
from server.routes import v1 as v1_routes
from db.repositories import tool_override_repo
from agent import group_mentions, agent_factory
from agent import ai_tool_installer
from plugins.loader import bootstrap_initial_plugins, bootstrap_gowa_upgrade, discover_and_load, PluginRegistry
from server.persistence_check import ensure_storage_persistence
from server.upload_limits import MAX_UPLOAD_BYTES, is_upload_path, too_large_response
from plugins.context import set_runtime as _set_plugin_runtime, set_runtime_services as _set_runtime_services, set_channel_runtime as _set_channel_runtime, set_deps as _set_deps
from plugins.lifecycle import manager as _lifecycle_manager
from runtime.supervisor import TaskSupervisor, TaskSpec, RestartPolicy
from runtime.subprocess_service import SubprocessService
from channels.registry import ChannelRegistry
from channels.outbound import OutboundRouter
from channels.providers.gowa_channel import GOWAChannel
from db.repositories import channel_repo, user_repo
from plugins.events import (
    set_runtime as _set_events_runtime,
    register_plugin_events,
    register_plugin_filters,
    emit as emit_event,
)
from plugins import services as _plugin_services
from server import balance_monitor
from server.balance_monitor import set_runtime as _set_balance_runtime

logger = logging.getLogger(__name__)


# A plugin exposes PUBLIC (auth-exempt) endpoints under ``/api/plugins/<id>/
# public/…`` and authenticates them ITSELF (plano 46 · 01-D — e.g. the website
# widget validates a per-visitor session token + allowed-domains, never the
# operator bearer). Generic convention: the core never names a plugin; a plugin
# opts in simply by mounting its public routes under ``/public/``. Module-level so
# it compiles once and is unit-testable.
PLUGIN_PUBLIC_PATH_RE = re.compile(r"^/api/plugins/[a-z][a-z0-9_]{0,31}/public/")


# ── Static files with mandatory revalidation ─────────────────────────────

class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that forces browsers to revalidate every load (ETag 304s).

    Plain StaticFiles emits only ETag/Last-Modified and no Cache-Control, so
    browsers fall back to *heuristic* caching and may reuse a stale ES module
    without revalidating. With the no-build-step frontend that surfaces as
    "module does not provide an export named X" SyntaxErrors after an update:
    a freshly edited component is loaded fresh while a module it imports is
    served from the heuristic cache. `no-cache` means "cache but always
    revalidate"; with the ETag the server answers 304 when nothing changed, so
    the cost is one cheap conditional request per asset.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
    statics_outbox_dir: Path
    plugins_dir: Path = None
    plugins_registry: PluginRegistry = None
    channel_registry: object = None
    outbound_router: object = None
    # Dynamically set by webhook route for cross-module access
    broadcast_tool_calls: object = None
    # Set by webhook.register_routes — provider-agnostic inbound funnel (plano 11).
    ingest_event: object = None


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
    # Operator-uploaded media (panel sends). MUST NOT be "senditems": the GOWA
    # subprocess inherits our cwd and uses statics/senditems/ as its OWN outbound
    # working dir — it writes new-<name>/thumbnails-<name> there and DELETES the
    # file (incl. ours, same path) ~1.5s after a successful send. Use a separate
    # GOWA-untouched folder so panel uploads survive.
    statics_outbox_dir = statics_dir / "outbox"
    statics_media_dir.mkdir(parents=True, exist_ok=True)
    statics_outbox_dir.mkdir(parents=True, exist_ok=True)

    # Plugin discovery + load. Runs synchronously before route registration so
    # plugin routes/tools/prompts are wired into the app before the first request.
    plugins_dir = settings.data_dir / "storages" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    # Deploy safeguard: detect a wiped storages/ (Coolify redeploy without a
    # Persistent Storage volume) BEFORE bootstrap re-seeds, so a silent data-loss
    # failure becomes a loud, actionable log. Fail-open / WHATSBOT_TEST no-op.
    ensure_storage_persistence(settings.data_dir / "storages")
    _plugin_examples_dir = settings.data_dir / "assets" / "plugin_examples"
    bootstrap_initial_plugins(plugins_dir, _plugin_examples_dir)
    # plano 13: existing installs (storages/plugins already populated, so the
    # bootstrap above no-ops) that actually use GOWA get the bundled gowa plugin
    # installed+enabled here, once. WHATSBOT_TEST-guarded (no-op in the suite).
    bootstrap_gowa_upgrade(plugins_dir, _plugin_examples_dir)
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
        # Plugin→plugin services (in-process). Registered HERE, in create_app, so
        # the surface exists BEFORE the lifespan and before run_setup — which
        # imposes one contract line on providers: a service op MUST NOT depend on
        # state created in setup(); it answers DISABLED/ERROR until ready, never
        # raises and never blocks.
        if loaded.services:
            _plugin_services.register_plugin_services(
                loaded.id, loaded.services,
                version=loaded.services_version,
                allow=loaded.services_allow,
            )
        _plugin_services.register_plugin_uses(
            loaded.id, getattr(loaded.manifest, "uses_services", ()))
        for provider_cls in getattr(loaded, "channel_providers", []):
            channel_registry.register_provider(provider_cls)

    # Materialize a LIVE Channel instance for every configured channel.
    # GOWA channels get their OWN per-device client (build_gowa_channel) so several
    # WhatsApp numbers connect on the same shared GOWA process (per-channel QR);
    # non-GOWA channels (e.g. whatsapp_cloud) are built from their provider class
    # registered above (plano 11 multicanal). A channel whose provider isn't loaded
    # is skipped — logged, never fatal.
    try:
        for row in channel_repo.list_all():
            cid = row["id"]
            provider = row.get("provider")
            try:
                if provider == "gowa":
                    channel_registry.add_channel(
                        cid,
                        channel_registry.instantiate(
                            provider, cid, row=row,
                            gowa_client=gowa_client, gowa_manager=gowa_manager))
                    continue
                if not row.get("enabled", 1):
                    continue
                if channel_registry.get_provider(provider) is None:
                    logger.info(
                        "Channel %s: provider %r not loaded; skipping live instance",
                        cid, provider)
                    continue
                inst = channel_registry.instantiate(provider, cid)
                if inst is None:
                    continue
                channel_registry.add_channel(cid, inst)
                logger.info("Channel %s (%s) live instance registered", cid, provider)
            except Exception as e:
                logger.warning("Could not instantiate channel %s (%s): %s", cid, provider, e)
    except Exception as e:
        logger.warning("Channel materialization failed: %s", e)

    # Outbound router (plano 11): the single send surface the runtime uses instead
    # of gowa_client, routing each reply to the conversation's own channel.
    outbound_router = OutboundRouter(channel_registry)


    # AI engine (config-in-DB + code-in-DB). Seed the default agent/prompt from
    # the current config (idempotent), then materialise/install/register the
    # DB-defined tools. Tools are registered AFTER core + plugin tools so the
    # registry's collision no-op gives precedence to code over the DB. Both
    # steps are best-effort: a failure never blocks the app from booting.
    try:
        # Fix agente-padrão (2026-07): só semeia o agente "default" em instalação
        # NOVA (tabela vazia). Antes o boot recriava a row sempre que ausente —
        # o que ressuscitava um "default" que o operador tinha excluído (a
        # exclusão só é permitida com outro agente marcado como padrão, então
        # uma instalação com agentes nunca fica sem fallback).
        from db.repositories import agent_repo as _agent_repo
        if not _agent_repo.list_all():
            agent_factory.seed_default_agent(settings)
        # Plano 22: preserve any legacy config.system_prompt/model into the
        # canonical default agent before those config keys are retired (idempotent).
        agent_factory.migrate_legacy_config_to_default_agent()
    except Exception as e:
        logger.warning("AI engine seed failed: %s", e)
    # Core RBAC permissions: backfill any catalog key missing from the
    # ``permissions`` table (idempotent). Self-heals an orphaned/skipped
    # permission migration — otherwise those keys can't be granted (the checkbox
    # saves as a no-op). Mirrors how plugins reconcile their perms at load.
    try:
        from db.repositories import rbac_repo as _rbac_repo
        _n = _rbac_repo.sync_core_permissions()
        if _n:
            logger.warning("RBAC: backfilled %d missing core permission(s)", _n)
    except Exception as e:
        logger.warning("Core permissions sync failed: %s", e)
    # Built-in system custom-attributes (plano 19): seed CPF & friends (idempotent).
    try:
        from db.system_attributes import seed_system_attributes
        seed_system_attributes()
    except Exception as e:
        logger.warning("System attributes seed failed: %s", e)
    # Built-in (core) tools as editable code-in-DB rows: seed the rows from the
    # current on-disk source (idempotent) and reconcile each tool's registration
    # with its row (override edits, unregister disabled). This runs ALWAYS — core
    # tools run in-process with the live ToolContext and are NOT gated by the
    # code-in-DB kill-switch below.
    try:
        from agent import ai_builtin_tools
        ai_builtin_tools.seed_builtin_tools()
        ai_builtin_tools.register_builtin_overrides(agent_handler)
    except Exception as e:
        logger.warning("Built-in tools seed/register failed: %s", e)

    # Tools de PLUGIN, o mesmo tratamento: semear a row a partir da fonte em
    # disco do plugin e reconciliar a registração com ela. A posição é contrato:
    # depois do registro do loader (que criou a baseline) e do reconcile das
    # builtins (que mantém a precedência core > plugin), antes do installer
    # isolado, antes do delete_orphans e — o ponto crítico — antes do
    # refresh_tool_overrides, que é quem reconstrói a lista de schemas mandada ao
    # LLM. Depois dele, uma edição nunca chegaria ao modelo, em silêncio.
    try:
        from agent import ai_plugin_tools
        ai_plugin_tools.seed_plugin_tools(registry)
        ai_plugin_tools.register_plugin_tool_overrides(registry, agent_handler)
        _orfas = ai_plugin_tools.mark_orphan_rows(registry)
        if _orfas:
            logger.info("%d row(s) de tool de plugin sem plugin carregado", _orfas)
    except Exception as e:
        logger.warning("Plugin tools seed/register failed: %s", e)

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
        statics_outbox_dir=statics_outbox_dir,
        plugins_dir=plugins_dir,
        plugins_registry=registry,
        channel_registry=channel_registry,
        outbound_router=outbound_router,
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
        # Fase 8 do plano de API: subscriber "*" que ENFILEIRA os webhooks de saída.
        register_webhook_listener()
        # plano 23 Fase C5 (Contract): conversation-lifecycle WS broadcasts are now
        # LISTENERS of the domain event (single source). The synchronous core
        # subscriber runs inside emit_with_filter, so the panel sees the same WS
        # events with identical timing.
        from app.services.ws_projections import register_lifecycle_ws_projection
        register_lifecycle_ws_projection(ws_manager)
        _set_balance_runtime(ws_manager, _loop, settings)
        # plano 42 C: seed the balance cache at boot (fire-and-forget) so the first
        # GET /api/balance serves a cached snapshot before any LLM call primes it —
        # closing the window the old 502 lived in. A dead/slow proxy just no-ops.
        _bal_key = settings.get("openrouter_api_key", "")
        if _bal_key:
            _loop.create_task(balance_monitor.prime_cache(_bal_key))
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
        # GOWA's bring-up + 3 polling loops are owned by the gowa PLUGIN when it's
        # loaded (its lifecycle.setup spawns them with owner='gowa', auto-stopped on
        # disable/uninstall). The core registers them ONLY when the gowa plugin is
        # absent — never both (plano 13 Fase 2 double-start footgun guard).
        # registry.loaded is fixed at create_app (before lifespan), so this is a
        # single deterministic decision. With gowa disabled/absent neither side
        # runs them → the core boots with zero channels by design.
        # GOWA's bring-up + status/QR/avatar polling are owned EXCLUSIVELY by the
        # gowa plugin (its lifecycle.setup spawns them with owner='gowa'). The core
        # NEVER registers them — so disabling/uninstalling the plugin truly stops
        # GOWA (goal #2) and the core boots + operates with zero channels (goal #3).
        # A core fallback keyed on registry.loaded would resurrect GOWA on disable
        # (the channel row persists), which is exactly the bug to avoid.
        # audit_purge is not a channel concern and stays core, always registered.
        supervisor.register(TaskSpec(
            "audit_purge", lambda: audit_purge_loop(deps), policy=RestartPolicy.PERMANENT))
        # plano 28 Fase 5: sweep empty 'inbound' ghost conversations (t=0 materialized
        # but batch never persisted the 1st message). Core concern, always registered.
        supervisor.register(TaskSpec(
            "empty_conversation_sweep", lambda: empty_conversation_sweep_loop(deps),
            policy=RestartPolicy.PERMANENT))
        # Fase 8 do plano de API: entrega dos webhooks de SAÍDA. O subscriber do
        # barramento só enfileira; o POST + HMAC + backoff vivem neste loop, fora
        # do caminho da request. Concern do core, sempre registrado.
        supervisor.register(TaskSpec(
            "webhook_delivery", lambda: webhook_delivery_loop(deps),
            policy=RestartPolicy.PERMANENT))
        state.task_supervisor = supervisor
        # Shared subprocess service for plugins (plano 09 Fase 5). GOWA keeps its
        # own ManagedProcess; this one tracks plugin-spawned subprocesses.
        subprocess_service = SubprocessService()
        state.subprocess_service = subprocess_service
        _set_runtime_services(supervisor, subprocess_service)
        # Channel runtime (plano 13 Fase 1.1): wire the registry/router/inbound
        # funnel into the plugin context BEFORE plugin setup() runs, so a channel
        # provider plugin (GOWA/Telegram) can register live channels and push
        # inbound via ctx.ingest_event. deps.ingest_event was set by
        # webhook.register_routes during create_app.
        _set_channel_runtime(channel_registry, outbound_router,
                             getattr(deps, "ingest_event", None))
        # Server deps (plano 13 Fase 1.2): wired BEFORE plugin setup() so a
        # first-party lifecycle plugin (GOWA) can own its subprocess + polling.
        _set_deps(deps)

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
            # Unregister the service surface HERE and not in
            # plugins.lifecycle._do_teardown: run_setup returns early when both
            # setup_fn and teardown_fn are None, so a provider without
            # entry.lifecycle would never be unregistered there.
            _plugin_services.unregister_plugin(loaded.id)
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
        title="WhatsBot-Pro",
        lifespan=lifespan,
        docs_url="/docs" if _docs_enabled else None,
        redoc_url="/redoc" if _docs_enabled else None,
        openapi_url="/openapi.json" if _docs_enabled else None,
    )

    # Mount static files (frontend assets)
    static_dir = web_dir / "static"
    if static_dir.exists():
        app.mount("/static", NoCacheStaticFiles(directory=str(static_dir)), name="static")

    # Avatar serving with graceful fallback. The frontend always points <img> at
    # /statics/avatars/<phone>.jpg; when the cache file is missing (cold cache, or
    # a redeploy/replica without the file), the bare static mount would 404 and
    # spam the browser console. This route shadows that exact subpath (registered
    # BEFORE the /statics mount, so it wins) and returns the cached file when
    # present, otherwise a neutral silhouette placeholder with 200 — same visual as
    # the frontend's DefaultAvatar, but no console error.
    _AVATAR_PLACEHOLDER_SVG = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="212" height="212" viewBox="0 0 212 212">'
        '<rect width="212" height="212" fill="#dfe5e7"/>'
        '<path fill="#fff" d="M106.3 113.1c14.6 0 26.5-11.9 26.5-26.5S120.9 60 106.3 60 79.8 71.9 79.8 86.6s11.9 26.5 26.5 26.5zm0 13.2c-17.7 0-53 8.9-53 26.5V166h106v-13.2c0-17.6-35.3-26.5-53-26.5z"/>'
        '</svg>'
    )

    @app.get("/statics/avatars/{name}")
    async def serve_avatar(name: str):
        # Names are "<phone>.jpg" / "<jid>@g.us.jpg" — reject any path traversal.
        if "/" in name or "\\" in name or ".." in name:
            return Response(status_code=404)
        avatar_file = statics_dir / "avatars" / name
        if avatar_file.is_file():
            return FileResponse(str(avatar_file), media_type="image/jpeg")
        return Response(
            content=_AVATAR_PLACEHOLDER_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache"},
        )

    # Operator-uploaded media (plano 64 · F10) — XSS armazenado.
    #
    # `statics/outbox/` recebe arquivo ARBITRÁRIO enviado pelo operador e é
    # servido same-origin, com uma CSP que permite `'unsafe-inline'`. Um `.html`
    # ou `.svg` ali dentro executaria script no domínio do painel (o `nosniff`
    # não ajuda: o tipo é corretamente adivinhado). Arrastar arquivos amplia
    # muito a superfície, então:
    #
    #   1. o nome em disco já nasce com a extensão do MIME validado e nunca com
    #      uma extensão executável (F1, server/upload_names.py); e
    #   2. esta rota — que shadow-a o mount, por ser registrada ANTES, igual ao
    #      precedente do avatar — força `Content-Disposition: attachment` para
    #      todo tipo fora de uma allow-list inline pequena e explícita.
    #
    # A allow-list é exatamente o que o painel precisa renderizar embutido
    # (<img>/<video>/<audio> e o PDF que o navegador abre). Qualquer coisa fora
    # dela é baixada, nunca renderizada.
    _INLINE_SAFE_MIMES = {
        "image/jpeg", "image/png", "image/webp", "image/gif",
        "video/mp4", "video/webm",
        "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm",
        "application/pdf",
    }

    @app.get("/statics/outbox/{name}")
    async def serve_outbox_media(name: str):
        if "/" in name or "\\" in name or ".." in name:
            return Response(status_code=404)
        media_file = statics_outbox_dir / name
        if not media_file.is_file():
            return Response(status_code=404)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if mime in _INLINE_SAFE_MIMES:
            return FileResponse(str(media_file), media_type=mime)
        return FileResponse(
            str(media_file),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    # Mount statics/ for GOWA media files (auto-downloaded images, audio, etc.)
    app.mount("/statics", StaticFiles(directory=str(statics_dir)), name="statics")

    # ── Auth middleware ────────────────────────────────────────────────

    # Paths exempt from authentication
    # NOTE: "/api/webhook/" (trailing slash) exempts every per-provider webhook
    # (the generic ``/api/webhook/{provider}/{channel_id}`` route — GOWA, Cloud
    # API, Telegram — each authenticates the request itself). The legacy exact
    # ``/api/webhook`` (GOWA fallback) was retired in plano 23 Fase F2, so only
    # "/health" remains in _AUTH_EXEMPT_EXACT.
    _AUTH_EXEMPT_PREFIXES = ("/static/", "/statics/", "/plugins/", "/api/auth/", "/api/webhook/")
    _AUTH_EXEMPT_EXACT = {"/health"}
    _PLUGIN_SPA_PATHS = {
        s["path"]
        for loaded in registry.loaded.values()
        for s in loaded.manifest.screens
        if s.get("path", "").startswith("/")
    }
    # FONTE ÚNICA dos paths fixos da SPA: alimenta a isenção de auth (_SPA_PATHS)
    # E o registro das rotas que devolvem index.html (o loop em "Frontend routes").
    # Eram DUAS listas mantidas à mão e elas divergiram — /api-keys estava só na
    # isenção e /sounds em lugar nenhum, então um F5 nessas telas caía no
    # {"detail":"Not Found"} do router (não há catch-all). Toda tela nova
    # declarada em CORE_ROUTES (web/static/js/components/shell/routing.js) precisa
    # entrar AQUI; a paridade é travada por tests/core/test_spa_routes_parity.py.
    _CORE_SPA_PATHS = (
        "/", "/contacts", "/dashboard", "/sandbox", "/costs", "/executions",
        "/plugins", "/quick-replies", "/custom-attributes", "/runtime", "/users",
        "/conversations", "/protocolos", "/attendances", "/audit", "/ai",
        "/channels", "/api-keys", "/sounds", "/wizard",
        # Legacy PT aliases (kept so a hard reload on an old bookmark still serves
        # index.html; the frontend rewrites them to the English path via
        # redirectLegacyPath).
        "/contatos", "/painel", "/atendimentos", "/auditoria",
    )
    _SPA_PATHS = set(_CORE_SPA_PATHS) | _PLUGIN_SPA_PATHS

    # ── Rate-limit das chaves de API (§4.3 do plano) ──────────────────────
    # Bucket PRÓPRIO, chaveado no ID DA CHAVE. Nunca no bucket do login (uma
    # integração legítima esgotaria o limite de um IP inteiro) e nunca em
    # ``audit_ip``, que é autodeclarado pelo painel ⇒ forjável.
    _API_KEY_WINDOW_SECONDS = 60
    _API_KEY_MAX_CALLS = 600           # 10 req/s sustentados por chave

    def _api_key_rate_limited(key_id: int):
        """``JSONResponse`` 429 quando a chave estourou a janela, senão ``None``."""
        now = time.time()
        calls = state.api_key_calls.get(key_id)
        if calls is None:
            calls = deque(maxlen=_API_KEY_MAX_CALLS * 2)
            state.api_key_calls[key_id] = calls
        while calls and now - calls[0] > _API_KEY_WINDOW_SECONDS:
            calls.popleft()
        if len(calls) >= _API_KEY_MAX_CALLS:
            logger.warning("Rate limit da chave de API %s atingido.", key_id)
            return JSONResponse(
                {"ok": False, "error": "Limite de chamadas da chave de API excedido."},
                status_code=429,
                headers={"Retry-After": str(_API_KEY_WINDOW_SECONDS)},
            )
        calls.append(now)
        return None

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path

        # Plugin-owned PUBLIC endpoints (generic convention, plano 46 · 01-D): any
        # route under ``/api/plugins/<id>/public/`` is auth-exempt — the plugin
        # authenticates the request ITSELF (e.g. the website widget validates a
        # per-visitor session token + allowed-domains, never the operator bearer).
        # This is provider-agnostic: the core never names a plugin; a plugin opts in
        # simply by mounting its public routes under ``/public/``.
        if PLUGIN_PUBLIC_PATH_RE.match(path):
            return await call_next(request)

        # SPA pages, static assets, webhook, and auth endpoints are always open.
        # The prefixes serve the SPA on hard reload of an entity deep-link
        # (e.g. /channels/<id>, /ai/agents/<key>) — same as /contacts/<id>.
        # /plugins/ is already covered by _AUTH_EXEMPT_PREFIXES (static).
        if path in _SPA_PATHS or path.startswith((
            "/contacts/", "/conversations/", "/executions/",
            "/ai/", "/channels/", "/users/", "/quick-replies/", "/custom-attributes/",
        )):
            return await call_next(request)
        if path in _AUTH_EXEMPT_EXACT:
            return await call_next(request)
        for prefix in _AUTH_EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Only protect /api/* paths. The gate closes as soon as ≥1 RBAC user
        # exists (``has_users``, self-healing — plano 48): from that moment a
        # valid USER session is required. A genuinely zero-user install stays
        # open only until the first admin is bootstrapped (``/api/auth/`` is
        # exempt). ``rbac_enforce`` is a rigid override (normally redundant).
        request.state.user = None
        # Procedência da identidade: a linha da chave quando a request entrou por
        # ``X-Api-Key``, senão ``None`` (sessão de painel). Setada AQUI para que
        # nenhuma rota precise de ``getattr`` com default.
        request.state.api_key = None
        if path.startswith("/api/"):
            has_users = await asyncio.to_thread(user_repo.has_any)
            enforce = rbac_enforced(settings) or has_users
            auth_header = request.headers.get("authorization", "")
            token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            # Resolve a present token even in open mode so per-permission gating
            # applies to voluntarily-logged-in users.
            kind = None
            if token or enforce:
                kind, user = await asyncio.to_thread(resolve_request_token, token)
                request.state.user = user

            # ── Crachá alternativo: chave de API ──────────────────────────
            # A chave resolve para o MESMO ``request.state.user`` que a sessão
            # resolve. Feito isso aqui, RBAC, auditoria, escopo por inbox e o
            # gating das rotas de plugin funcionam SEM ALTERAÇÃO — a chave "vira
            # o usuário". Vale para todo ``/api/*``, inclusive
            # ``/api/plugins/<id>/*`` e ``/api/v1/*`` (decisão D2 do plano).
            raw_key = request.headers.get(API_KEY_HEADER, "")
            if request.state.user is None and raw_key:
                key_user, key_row = await asyncio.to_thread(resolve_api_key, raw_key)
                if key_user:
                    limited = _api_key_rate_limited(key_row["id"])
                    if limited is not None:
                        return limited
                    request.state.user = key_user
                    request.state.api_key = key_row
                    kind = "user"   # crachá válido ⇒ identidade de usuário

            if enforce and kind != "user":  # only a USER session/API key passes
                return JSONResponse(
                    {"ok": False, "error": "Não autenticado."},
                    status_code=401,
                )

        # Audit actor (plano 07): real user when logged in, else system. The bus
        # `*` listener reads this via contextvar (snapshotted into create_task).
        import uuid as _uuid
        _u = request.state.user
        request.state.request_id = _uuid.uuid4().hex
        # IP do NAVEGADOR (não do proxy) — ver server/client_ip.py. `audit_ip`
        # prefere o IP público que o painel declara em X-Client-Public-IP, e só
        # a AUDITORIA pode usá-lo: por ser autodeclarado (forjável), o bucket de
        # rate-limit do login segue em `client_ip` (plano 86 · D4). Não unificar.
        _ip = audit_ip(request)
        # Quando a request entrou por chave, o ATOR continua sendo o usuário dono
        # (a ação é dele); ``actor_type="apikey"`` + ``api_key_id`` registram a
        # PROCEDÊNCIA — por qual chave ela entrou (decisão D4 do plano).
        _key = request.state.api_key
        _actor_token = set_current_actor(ActorCtx(
            id=(_u.get("id") if _u else None),
            type=("apikey" if _key else ("user" if _u else "system")),
            label=(_u.get("name") or _u.get("email") if _u else None),
            ip=_ip, request_id=request.state.request_id,
            api_key_id=(_key.get("id") if _key else None),
        ))
        try:
            return await call_next(request)
        finally:
            reset_current_actor(_actor_token)

    @app.middleware("http")
    async def upload_size_limit(request: Request, call_next):
        # Plano 64 · F2 — recusa um upload grande demais ANTES de lê-lo na RAM.
        # Só olha o Content-Length declarado (barato); um cliente que mente sobre
        # ele ainda passa, mas o teto do navegador + este gate cobrem o caso real
        # (arrastar um arquivo enorme por engano).
        if request.method == "POST" and is_upload_path(request.url.path):
            raw_len = request.headers.get("content-length")
            try:
                if raw_len is not None and int(raw_len) > MAX_UPLOAD_BYTES:
                    return too_large_response()
            except ValueError:
                pass
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # A route may set its OWN Content-Security-Policy to opt out of the default
        # frame lock — e.g. an embeddable page (the website-widget iframe) that must
        # allow ``frame-ancestors <allowed_domains>`` instead of ``'none'``. When it
        # did, respect it and DON'T also send ``X-Frame-Options: DENY`` (which would
        # override the allow-list and block every embed). Otherwise apply the strict
        # app-wide default. Generic: the middleware never names a route/plugin.
        if "content-security-policy" not in resp.headers:
            resp.headers["X-Frame-Options"] = "DENY"
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
                "worker-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "media-src 'self' data: blob:; "
                # www.cloudflare.com: ÚNICA chamada externa do painel — o
                # /cdn-cgi/trace de onde o navegador descobre o próprio IP
                # público para a auditoria (plano 86; ver services/publicIp.js).
                # Host EXATO de propósito — nunca curinga.
                "connect-src 'self' ws: wss: https://www.cloudflare.com; "
                "frame-ancestors 'none'"
            )
        return resp

    @app.middleware("http")
    async def _legacy_conversation_route_alias(request: Request, call_next):
        # Compat: as rotas foram renomeadas /api/conversations -> /api/atendimentos
        # (e /api/contacts/{phone}/conversation -> /atendimento). Reescreve o path
        # legado para o novo ANTES do roteamento, então bundles/clients antigos que
        # ainda chamam /api/conversations continuam funcionando.
        p = request.scope.get("path", "")
        np = None
        if p.startswith("/api/conversations"):
            np = "/api/atendimentos" + p[len("/api/conversations"):]
        elif p.startswith("/api/contacts/") and p.endswith("/conversation"):
            np = p[: -len("/conversation")] + "/atendimento"
        if np is not None:
            request.scope["path"] = np
            request.scope["raw_path"] = np.encode("latin-1")
        return await call_next(request)

    # ── Health endpoint (always open, used by Docker healthcheck) ──────

    @app.get("/health")
    async def healthcheck():
        return JSONResponse({"ok": True})

    # ── Frontend routes ────────────────────────────────────────────────

    # Os paths FIXOS (/, /contacts, /api-keys, /sounds, aliases PT…) são
    # registrados pelo loop logo abaixo a partir de _CORE_SPA_PATHS — a mesma
    # tupla que isenta do gate de auth. Aqui ficam só os PARAMETRIZADOS, que
    # dependem da assinatura de ``index``.
    @app.get("/contacts/{contact_id:int}")
    @app.get("/conversations/{conversation_id:int}")
    @app.get("/executions/{execution_id:int}")
    # Deep-links por entidade (espelham /contacts/<id>): a URL carrega a
    # identidade natural da entidade aberta e o reload reabre na sub-aba certa.
    # Todas servem o mesmo index.html — o router do frontend resolve a seleção.
    @app.get("/ai/{sub:str}")
    @app.get("/ai/{sub:str}/{entity_id:str}")
    @app.get("/plugins/{plugin_id:str}")
    @app.get("/channels/{channel_id:str}")
    # user_id é numérico → :int exclui /users/roles naturalmente (sem depender da
    # ordem de registro entre /users/{id} e /users/roles).
    @app.get("/users/{user_id:int}")
    @app.get("/users/roles")
    @app.get("/users/roles/{role_key:str}")
    # short_code pode conter "/" (ex.: "/saud") → :path tolera o segmento extra
    # mesmo quando um proxy decodifica %2F antes de chegar aqui.
    @app.get("/quick-replies/{short_code:path}")
    @app.get("/custom-attributes/{scope:str}")
    @app.get("/custom-attributes/{scope:str}/{attr_key:str}")
    async def index(
        contact_id: int | None = None,
        conversation_id: int | None = None,
        execution_id: int | None = None,
        sub: str | None = None,
        entity_id: str | None = None,
        plugin_id: str | None = None,
        channel_id: str | None = None,
        user_id: int | None = None,
        role_key: str | None = None,
        short_code: str | None = None,
        scope: str | None = None,
        attr_key: str | None = None,
    ):
        index_file = web_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"error": "Frontend not found"}, status_code=404)

    # Register the fixed core SPA paths + the dynamic ones declared by plugin
    # manifests, so the frontend router gets the same index.html on hard reload
    # (direct URL / F5) of any of those URLs.
    async def _spa_index():
        index_file = web_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"error": "Frontend not found"}, status_code=404)

    for _spa_path in (*_CORE_SPA_PATHS, *_PLUGIN_SPA_PATHS):
        app.add_api_route(_spa_path, _spa_index, methods=["GET"])

    # Exposto para o teste de paridade (tests/core/test_spa_routes_parity.py)
    # comparar com as rotas do frontend sem re-parsear este arquivo.
    app.state.core_spa_paths = _CORE_SPA_PATHS
    app.state.spa_paths = _SPA_PATHS

    # ── Register route modules ─────────────────────────────────────────
    # Order matters: webhook must be registered before sandbox so
    # broadcast_tool_calls is available via deps.
    auth.register_routes(app, deps)
    users_routes.register_routes(app, deps)
    roles_routes.register_routes(app, deps)
    conversations_routes.register_routes(app, deps)
    conversation_labels_routes.register_routes(app, deps)
    saved_filters_routes.register_routes(app, deps)
    sound_prefs_routes.register_routes(app, deps)
    account_routes.register_routes(app, deps)
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
    plugins_routes.register_routes(app, deps)
    tools_routes.register_routes(app, deps)
    admin_routes.register_routes(app, deps)
    ai_engine_routes.register_routes(app, deps)
    audit_routes.register_routes(app, deps)
    api_keys_routes.register_routes(app, deps)
    webhooks_out_routes.register_routes(app, deps)
    # Fachada versionada para integrações externas (a chave X-Api-Key vale
    # aqui e em todo /api/*, D2 — a autenticação é do middleware, não da rota).
    v1_routes.register_routes(app, deps)

    # ── Plugin routers and static assets ──────────────────────────────
    for loaded in registry.loaded.values():
        if loaded.router is not None:
            app.include_router(loaded.router, prefix=f"/api/plugins/{loaded.id}")
        if loaded.static_dir is not None:
            app.mount(
                f"/plugins/{loaded.id}/static",
                NoCacheStaticFiles(directory=str(loaded.static_dir)),
                name=f"plugin_{loaded.id}_static",
            )

    # Expose the shared deps (registry, router, ingest funnel) for tests/tooling.
    app.state.deps = deps
    return app
