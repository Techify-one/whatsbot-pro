"""Channels API (plano 02 Fase 0 + Fase 2).

CRUD of configured channels for the panel. Credentials are masked at this
boundary (P15 — secrets never returned in clear), mirroring how ``/api/config``
masks the LLM key. Secrets are stored in clear (MVP, P15) via
``channel_credential_repo``; only the read path masks.

Live registration: creating/deleting a channel persists to the DB; the live
``ChannelRegistry`` is (re)built from the DB at boot, so a new channel becomes
operational on the next restart (same model as plugins). Status reads prefer the
live instance when present, else the stored flags.

Plano 23 · Fase B6 — these routes are THIN delegators: they resolve permission +
body validation + 404, then delegate the WRITE + side effects to
``app.services.channel_service`` / ``app.services.template_service``.
"""

import asyncio
import uuid

from fastapi import File, Request, UploadFile
from fastapi.responses import Response

from app.services import channel_service as svc
from app.services import template_service as tpl_svc
from db.repositories import channel_repo
from server.authz import permission_denied, has_permission, current_user
from server.helpers import _ok, _err

_ID_RE = svc.ID_RE


def register_routes(app, deps):

    @app.get("/api/channels")
    async def list_channels(request: Request, archived: bool = False):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        return _ok(await svc.list_channels(deps, archived=archived))

    @app.get("/api/channels/connected")
    async def list_connected_channels(request: Request):
        """Connected + logged-in channels an operator can start a conversation on.

        Lighter and lower-privileged than ``GET /api/channels`` (gated by
        ``conversation.reply`` instead of ``channel.manage``, no credentials): the
        "start conversation" inbox picker needs only id/provider/name/status.
        Inclusion is by ``logged_in`` (the session can send), NOT ``connected``.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        return _ok(await svc.list_connected(deps))

    @app.get("/api/channels/for-filter")
    async def list_channels_for_filter(request: Request):
        """ALL channels (id/provider/display_name) for the conversation-filter
        "Canais" options (plano 59).

        Lower-privileged than ``GET /api/channels`` (``conversation.reply`` vs.
        ``channel.manage``, no credentials) and broader than ``/connected``
        (includes disabled + archived): the filter must offer every channel a
        conversation could belong to, independent of the loaded sidebar rows.
        Registered BEFORE ``/api/channels/{channel_id}`` so the fixed path wins.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        return _ok(await svc.list_for_filter(deps))

    @app.get("/api/channels/{channel_id}/session-state")
    async def channel_session_state(channel_id: str, request: Request, phone: str = ""):
        """Session/window state for STARTING a conversation on ``channel_id`` (plano 21).

        The "Nova conversa" modal needs to know, BEFORE a conversation row exists,
        whether free text is allowed to ``phone`` on this channel or a template is
        required. Capability-driven — never by provider name.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        phone = (phone or "").strip()
        if not phone:
            return _err("phone é obrigatório.", status=400)
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        data = await asyncio.to_thread(tpl_svc.session_state, deps, channel_id, phone)
        return _ok(data)

    @app.get("/api/channels/{channel_id}/templates")
    async def channel_templates(channel_id: str, request: Request):
        """Templates for a channel, used by the picker when starting a BRAND-NEW
        conversation (no conversation row yet, plano 21). Channel-scoped twin of
        ``GET /api/conversations/{id}/templates`` — same ``{supported, templates,
        can_create, can_delete, channel}`` shape (``channel`` = ``{id, name,
        phone}``, o remetente que o modal exibe no cabeçalho)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        can_create = has_permission(request, "template.create")
        can_delete = has_permission(request, "template.delete")
        channel = await tpl_svc.channel_badge(deps, channel_id)
        if not tpl_svc.supports_templates(deps, channel_id):
            return _ok({"supported": False, "templates": [],
                        "can_create": can_create, "can_delete": can_delete,
                        "channel": channel})
        templates = await tpl_svc.list_templates(deps, channel_id)
        return _ok({"supported": True, "templates": templates,
                    "can_create": can_create, "can_delete": can_delete,
                    "channel": channel})

    @app.post("/api/channels/{channel_id}/send-template")
    async def channel_send_template(channel_id: str, body: dict, request: Request):
        """Send an approved template to ``phone`` through ``channel_id`` when there is
        no conversation yet (plano 21). Channel-scoped twin of
        ``POST /api/conversations/{id}/send-template``.

        body: ``{phone, template_name, language?, components?, preview_text?}``.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        phone = (body.get("phone") or "").strip()
        template_name = (body.get("template_name") or "").strip()
        language = (body.get("language") or "pt_BR").strip() or "pt_BR"
        components = body.get("components")
        if not phone:
            return _err("phone é obrigatório.", status=400)
        if not template_name:
            return _err("template_name é obrigatório.", status=400)
        if components is not None and not isinstance(components, list):
            return _err("components deve ser uma lista.", status=400)
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        if not tpl_svc.supports_templates(deps, channel_id):
            return _err("Este canal não suporta templates.", status=400)

        _u = current_user(request)
        kind, data = await tpl_svc.send_template(
            deps, channel_id, phone=phone, template_name=template_name,
            language=language, components=components,
            preview_text=body.get("preview_text") or "",
            sent_by_user_id=(_u.get("id") if _u else None),
            sent_by_name=(_u.get("name") if _u else None))
        if kind == "send_failed":
            return _err(f"Falha ao enviar template: {data}", status=502)
        if kind == "save_failed":
            return _err(f"Template enviado, mas falha ao salvar a mensagem: {data}", status=500)
        return _ok(data)

    @app.post("/api/channels/{channel_id}/templates")
    async def channel_create_template(channel_id: str, body: dict, request: Request):
        """Create a template on ``channel_id`` (gated ``template.create``). Channel-
        scoped twin of ``POST /api/conversations/{id}/templates`` for the new-
        conversation picker."""
        denied = permission_denied(request, "template.create")
        if denied:
            return denied
        name = (body.get("name") or "").strip().lower()
        if not name or not name.isascii() or not all(c.isalnum() or c == "_" for c in name):
            return _err("Nome inválido: use apenas letras minúsculas, números e _.", status=400)
        body_text = (body.get("body_text") or "").strip()
        if not body_text:
            return _err("body_text é obrigatório.", status=400)
        category = (body.get("category") or "UTILITY").strip().upper()
        if category not in tpl_svc.TEMPLATE_CATEGORIES:
            return _err(f"category deve ser uma de {sorted(tpl_svc.TEMPLATE_CATEGORIES)}.", status=400)
        language = (body.get("language") or "pt_BR").strip() or "pt_BR"
        body_examples = body.get("body_examples")
        header_examples = body.get("header_examples")
        if body_examples is not None and not isinstance(body_examples, list):
            return _err("body_examples deve ser uma lista.", status=400)
        if header_examples is not None and not isinstance(header_examples, list):
            return _err("header_examples deve ser uma lista.", status=400)
        header_format, header_handle, media_err = tpl_svc.normalize_header_media(
            body.get("header_format"), body.get("header_handle"))
        if media_err:
            return _err(media_err, status=400)
        buttons, btn_err = tpl_svc.normalize_buttons(body.get("buttons"))
        if btn_err:
            return _err(btn_err, status=400)
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        if not tpl_svc.supports_templates(deps, channel_id):
            return _err("Este canal não suporta templates.", status=400)
        kind, data = await tpl_svc.create_template(
            deps, channel_id, name=name, category=category, language=language,
            body_text=body_text,
            header_text=(body.get("header_text") or "").strip() or None,
            footer_text=(body.get("footer_text") or "").strip() or None,
            body_examples=body_examples, header_examples=header_examples,
            header_format=header_format, header_handle=header_handle,
            buttons=buttons)
        if kind == "failed":
            return _err(f"Falha ao criar template: {data}", status=502)
        return _ok(data)

    @app.post("/api/channels/{channel_id}/templates/upload-example")
    async def channel_upload_template_example(
            channel_id: str, request: Request, file: UploadFile = File(...)):
        """Upload a media sample and return its Meta handle (plano 73).

        The handle goes back as ``header_handle`` in the create-template body — it
        is how Meta wants the example of an IMAGE/VIDEO/DOCUMENT header. MIME and
        size are validated BEFORE the provider call so a bad file never leaves the
        server."""
        denied = permission_denied(request, "template.create")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        if not tpl_svc.supports_templates(deps, channel_id):
            return _err("Este canal não suporta templates.", status=400)
        data_bytes = await file.read()
        err = tpl_svc.validate_example_upload(file.content_type or "",
                                              len(data_bytes or b""))
        if err:
            return _err(err, status=400)
        kind, data = await tpl_svc.upload_template_example(
            deps, channel_id, file_bytes=data_bytes,
            mime=(file.content_type or "").split(";")[0].strip().lower(),
            filename=file.filename or "example")
        if kind == "failed":
            return _err(f"Falha ao enviar o arquivo: {data}", status=502)
        return _ok(data)

    @app.delete("/api/channels/{channel_id}/templates/{name}")
    async def channel_delete_template(channel_id: str, name: str, request: Request):
        """Delete a template (all languages) on ``channel_id`` (gated
        ``template.delete``)."""
        denied = permission_denied(request, "template.delete")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        if not tpl_svc.supports_templates(deps, channel_id):
            return _err("Este canal não suporta templates.", status=400)
        kind, data = await tpl_svc.delete_template(deps, channel_id, name)
        if kind == "failed":
            return _err(f"Falha ao apagar template: {data}", status=502)
        return _ok(data)

    @app.get("/api/channels/assignable-users")
    async def assignable_users(request: Request):
        """Active panel users for the channel agent picker (create + edit).

        Gated by ``channel.manage`` (same as the rest of this screen). Registered
        before ``/{channel_id}`` so the literal path wins the match."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        users = await asyncio.to_thread(svc.assignable_users)
        return _ok({"users": users})

    @app.get("/api/channels/providers")
    async def list_providers(request: Request):
        """Channel providers currently AVAILABLE to create a channel with.

        A provider is only offered when its backing plugin is enabled — i.e. its
        ``CHANNEL_PROVIDERS`` class is registered in the live ``ChannelRegistry``.
        GOWA is core and always present. Registered before ``/{channel_id}`` so the
        literal path wins the match.

        Gated by ``conversation.reply``, not ``channel.manage`` (plano 85 B2): desde o
        plano 76 H1 este é também o catálogo de APRESENTAÇÃO do hub — rótulo, cor e
        tipo de contato de cada provider, lido pelo selo de canal de toda linha da
        sidebar. Um operador sem permissão de GESTÃO de canais levava 403 e ficava com
        os selos no fallback cinza. O payload é a auto-descrição da CLASSE do provider
        (definição de campo: ``credential_fields``/``config_fields``), nunca valor de
        credencial armazenado — segue o mesmo precedente de ``/connected`` e
        ``/for-filter``, as outras rotas de operador desta tela. Escrita (criar, editar,
        excluir canal) continua em ``channel.manage``."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        return _ok(await svc.providers(deps))

    @app.get("/api/channels/{channel_id}")
    async def get_channel(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        data = await svc.get_serialized(deps, channel_id)
        if data is None:
            return _err("Canal não encontrado.", 404)
        return _ok(data)

    @app.post("/api/channels")
    async def create_channel(body: dict, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        cid = (body.get("id") or "").strip()
        provider = (body.get("provider") or "").strip()
        # A provider is creatable iff it is REGISTERED (its plugin is enabled) — so
        # ANY installed provider works, including a brand-new one from a plugin, with
        # no hardcoded list (plano 33 D1). The legacy ``ALLOWED_PROVIDERS`` survives
        # only as a compat safety-net allow-list (lets a known provider be created
        # while its plugin is momentarily disabled — its row persists and goes live
        # on re-enable). No ``if provider ==`` in the core.
        registry = getattr(deps, "channel_registry", None)
        registered = set(registry.providers()) if registry is not None else set()
        creatable = registered | svc.ALLOWED_PROVIDERS
        if provider not in creatable:
            return _err(
                f"provider deve ser um de: {', '.join(sorted(creatable))}.", 400)
        # Anti zombie-channel (capability-driven): a credential-only provider with no
        # connect step (Cloud/Telegram) is useless without its required credentials —
        # reject the create up front. GOWA's required set is empty (QR flow).
        submitted_creds = body.get("credentials") or {}
        missing_creds = [k for k in svc.required_credentials(deps, provider)
                         if not str(submitted_creds.get(k) or "").strip()]
        if missing_creds:
            return _err(
                f"Credenciais obrigatórias faltando para {provider}: "
                f"{', '.join(missing_creds)}.", 400)
        config = body.get("config")
        # The UI may nest gowa_device_id inside config; accept either spot.
        gowa_device_id = body.get("gowa_device_id")
        if not gowa_device_id and isinstance(config, dict):
            gowa_device_id = config.get("gowa_device_id")
        # GOWA device id is auto-generated, never user-chosen.
        if provider == "gowa" and not gowa_device_id:
            gowa_device_id = f"gowa_{uuid.uuid4().hex[:8]}"
        # Channel id is auto-generated: the user only picks a display name. GOWA
        # reuses its device id as the channel id; other providers get
        # "<provider>_<hex>". A client may still send an explicit id (back-compat).
        if cid:
            if not _ID_RE.match(cid):
                return _err("id inválido (use snake_case: a-z, 0-9, _; começa com letra).", 400)
            if await asyncio.to_thread(channel_repo.get, cid):
                return _err("Já existe um canal com esse id.", 409)
        else:
            cid = (gowa_device_id if (provider == "gowa" and gowa_device_id)
                   else f"{provider}_{uuid.uuid4().hex[:8]}")
            while await asyncio.to_thread(channel_repo.get, cid):
                cid = f"{provider}_{uuid.uuid4().hex[:8]}"
        try:
            data = await svc.create(
                deps, cid=cid, provider=provider,
                display_name=body.get("display_name", "") or cid,
                submitted_creds=submitted_creds, config=config,
                gowa_device_id=gowa_device_id)
        except svc.DuplicateChannelError as e:
            return _err(str(e), 409)
        return _ok(data)

    @app.put("/api/channels/{channel_id}")
    async def update_channel(channel_id: str, body: dict, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        try:
            return _ok(await svc.update(deps, row, body))
        except svc.DuplicateChannelError as e:
            return _err(str(e), 409)

    @app.get("/api/channels/{channel_id}/members")
    async def get_channel_members(channel_id: str, request: Request):
        """Agents (panel users) who see/receive this channel's inbox.

        Returns the channel's inbox id, the current member user ids, and the full
        list of assignable (active) users for the picker. Gated by ``channel.manage``."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        return _ok(await svc.get_members(deps, row))

    @app.put("/api/channels/{channel_id}/members")
    async def set_channel_members(channel_id: str, body: dict, request: Request):
        """Replace the member set of this channel's inbox. Body: ``{user_ids: [...]}``."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        raw = body.get("user_ids")
        if not isinstance(raw, list):
            return _err("user_ids deve ser uma lista.", 400)
        try:
            user_ids = [int(u) for u in raw]
        except (TypeError, ValueError):
            return _err("user_ids deve conter apenas inteiros.", 400)
        return _ok(await svc.set_members(deps, row, user_ids))

    @app.delete("/api/channels/{channel_id}")
    async def delete_channel(channel_id: str, request: Request, purge: bool = False):
        """Remove a channel.

        Default = soft-delete (plano inboxes/canais §4.3-c): arquiva o canal e sua
        inbox, escondendo-os da UI mas PRESERVANDO o histórico de conversas. Pode
        ser desfeito via ``POST /api/channels/{id}/restore``.

        ``?purge=true`` = hard-delete: apaga o canal e a inbox de vez (CASCADE leva
        conversas/membros junto). **Destrói histórico** — irreversível.
        """
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        result = await svc.delete(deps, channel_id, purge=purge)
        if result is None:
            return _err("Canal não encontrado.", 404)
        return _ok(result)

    @app.post("/api/channels/{channel_id}/restore")
    async def restore_channel(channel_id: str, request: Request):
        """Undo a soft-delete: unarchive the channel (stays disabled until the user
        re-enables it). The live instance is re-created on the next restart."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        return _ok(await svc.restore(deps, channel_id))

    @app.get("/api/channels/{channel_id}/status")
    async def channel_status(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        return _ok(await svc.status(deps, row))

    @app.post("/api/channels/status-batch")
    async def channels_status_batch(body: dict, request: Request):
        """Status de VÁRIOS canais em UMA request (plano 50 F13). Substitui o fan-out de
        1 GET por canal na tela Canais. Body ``{ids:[...]}`` → ``{status_by_id: {id: status}}``.
        Status é volátil (rede/GOWA), por isso batch-endpoint (P6 opção a) e não embutido."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        raw = body.get("ids") or []
        if not isinstance(raw, list):
            return _err("ids deve ser uma lista.")
        ids = [str(x) for x in raw[:200]]  # cap defensivo
        out: dict[str, dict] = {}
        for cid in ids:
            row = await asyncio.to_thread(channel_repo.get, cid)
            if row is not None:
                out[cid] = await svc.status(deps, row)
        return _ok({"status_by_id": out})

    @app.get("/api/channels/{channel_id}/qr")
    async def channel_qr(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        result = await svc.qr(deps, row)
        if result == "not_gowa":
            return _err("QR disponível apenas para canais GOWA.", 400)
        if result == "unavailable":
            return _err("Canal GOWA indisponível.", 503)
        if isinstance(result, tuple) and result and result[0] == "error":
            return _err(f"Falha ao obter QR: {result[1]}", 502)
        if not result:
            # Already logged in, or GOWA not ready yet — 204 tells the UI to poll status.
            return Response(status_code=204)
        return Response(content=result, media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    async def _channel_session(channel_id: str, request: Request, action: str):
        """Shared handler for reconnect/logout (plano 27 F3.2)."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        fn = svc.reconnect if action == "reconnect" else svc.logout
        result = await fn(deps, row)
        if result == "not_gowa":
            return _err("Ação disponível apenas para canais GOWA.", 400)
        if result == "unavailable":
            return _err("Canal GOWA indisponível.", 503)
        result = result or {}
        if not result.get("ok"):
            return _err(result.get("error") or "Falha na ação.", 502)
        return _ok({"message": "ok"})

    @app.post("/api/channels/{channel_id}/reconnect")
    async def channel_reconnect(channel_id: str, request: Request):
        return await _channel_session(channel_id, request, "reconnect")

    @app.post("/api/channels/{channel_id}/logout")
    async def channel_logout(channel_id: str, request: Request):
        return await _channel_session(channel_id, request, "logout")
