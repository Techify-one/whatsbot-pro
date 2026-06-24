"""Channels API (plano 02 Fase 0 + Fase 2).

CRUD of configured channels for the panel. Credentials are masked at this
boundary (P15 — secrets never returned in clear), mirroring how ``/api/config``
masks the LLM key. Secrets are stored in clear (MVP, P15) via
``channel_credential_repo``; only the read path masks.

Live registration: creating/deleting a channel persists to the DB; the live
``ChannelRegistry`` is (re)built from the DB at boot, so a new channel becomes
operational on the next restart (same model as plugins). Status reads prefer the
live instance when present, else the stored flags.
"""

import asyncio
import json
import logging
import re
import time
import uuid

from fastapi import Request
from fastapi.responses import Response

from channels.providers.gowa_channel import build_gowa_channel
from db.repositories import (channel_repo, channel_credential_repo, inbox_repo,
                             inbox_member_repo, user_repo,
                             contact_repo, conversation_repo, message_repo)
from plugins.events import emit_with_filter
from server.authz import permission_denied, has_permission
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ALLOWED_PROVIDERS = {"gowa", "whatsapp_cloud", "telegram", "test"}


def _mask(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return f"••••{tail}"


# Non-secret credential keys returned in CLEAR so the edit form can show + pre-fill
# them (they are public identifiers, not secrets). Everything else is masked (P15).
_NON_SECRET_CRED_KEYS = {"waba_id", "phone_number_id"}


def _serialize(row: dict, creds: dict) -> dict:
    row = dict(row)
    row["credentials"] = {
        k: (v if k in _NON_SECRET_CRED_KEYS else _mask(v))
        for k, v in creds.items()
    }
    return row


def _assignable_users() -> list[dict]:
    """Active panel users for the channel agent picker (id/name/email/is_admin)."""
    return [
        {"id": u["id"], "name": u.get("name") or u.get("email"),
         "email": u.get("email"), "is_admin": bool(u.get("is_admin"))}
        for u in user_repo.list_all() if u.get("is_active")
    ]


def register_routes(app, deps):

    registry = getattr(deps, "channel_registry", None)
    gowa_client = getattr(deps, "gowa_client", None)
    gowa_manager = getattr(deps, "gowa_manager", None)

    def _register_live_gowa(cid: str, row: dict) -> None:
        """Make a freshly-created GOWA channel operational without a restart, so
        its QR/status endpoints work immediately."""
        if registry is None or gowa_client is None:
            return
        try:
            inst = build_gowa_channel(cid, row, gowa_client=gowa_client,
                                      gowa_manager=gowa_manager)
            registry.add_channel(cid, inst)
        except Exception:
            pass

    def _register_live_provider(cid: str, provider: str) -> None:
        """Make a freshly-created non-GOWA channel (Cloud/Telegram/…) live without
        a restart, when its provider plugin is loaded.

        Mirrors the boot-time materialization in ``create_app``: instantiate the
        registered provider class with ``(channel_id, registry=...)`` so status/QR/
        send and (for Telegram) the long-poll loop pick it up immediately. Pure
        constructor — no network — so it is safe and best-effort."""
        if registry is None:
            return
        provider_cls = registry.get_provider(provider)
        if provider_cls is None:
            return
        try:
            try:
                inst = provider_cls(cid, registry=registry)
            except TypeError:
                inst = provider_cls(cid)
            registry.add_channel(cid, inst)
        except Exception:
            pass

    @app.get("/api/channels")
    async def list_channels(request: Request, archived: bool = False):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        rows = await asyncio.to_thread(
            channel_repo.list_archived if archived else channel_repo.list_all)
        out = []
        for row in rows:
            creds = await asyncio.to_thread(channel_credential_repo.get_all, row["id"])
            out.append(_serialize(row, creds))
        return _ok(out)

    @app.get("/api/channels/connected")
    async def list_connected_channels(request: Request):
        """Connected + logged-in channels an operator can start a conversation on.

        Lighter and lower-privileged than ``GET /api/channels`` (gated by
        ``conversation.reply`` instead of ``channel.manage``, no credentials): the
        "start conversation" inbox picker needs only id/provider/name/status.
        Status prefers the live registry instance, falling back to the stored flags.

        Inclusion is by ``logged_in`` (the session is authenticated and can send),
        NOT ``connected``. For GOWA the two flags mean different things —
        ``connected`` tracks the local subprocess manager (``manager.is_running``),
        while ``logged_in`` reflects the live WhatsApp session via ``/app/status``.
        A GOWA inbox can be ``Desconectado · Autenticado`` (subprocess not owned by
        this process, e.g. launched externally in dev) yet fully usable to send;
        requiring ``connected`` would wrongly hide it. ``logged_in`` already implies
        the REST API is reachable, so it is the correct "can I send" signal. For
        Cloud/Telegram the two flags are always equal, so nothing changes there.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        rows = await asyncio.to_thread(channel_repo.list_all)
        out = []
        for row in rows:
            if not row.get("enabled"):
                continue
            logged_in = bool(row.get("logged_in"))
            inst = registry.get(row["id"]) if registry is not None else None
            if inst is not None:
                try:
                    st = await asyncio.to_thread(inst.status)
                    logged_in = bool(st.get("logged_in"))
                except Exception:
                    pass
            if not logged_in:
                continue
            out.append({
                "id": row["id"],
                "provider": row.get("provider"),
                "display_name": row.get("display_name") or "",
                "own_phone": row.get("own_phone"),
            })
        return _ok(out)

    @app.get("/api/channels/{channel_id}/session-state")
    async def channel_session_state(channel_id: str, request: Request, phone: str = ""):
        """Session/window state for STARTING a conversation on ``channel_id`` (plano 21).

        The "Nova conversa" modal needs to know, BEFORE a conversation row exists,
        whether free text is allowed to ``phone`` on this channel or a template is
        required. Resolves the contact's latest conversation IN this channel's inbox
        (if any) and computes the 24h free-text window from its last inbound.

        Returns ``{templates_supported, session_open, has_conversation,
        conversation_id, last_inbound_ts}``:
        - always-open channels (GOWA) → ``session_open=True`` (free text always ok);
        - windowed channels (WhatsApp Cloud) → ``session_open`` is True only when an
          existing conversation has an inbound within 24h. No conversation, or an
          expired window, yields ``False`` (a template is then mandatory).
        Capability-driven — never by provider name.
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
        outbound = deps.outbound_router

        def _resolve() -> dict:
            templates_supported = outbound.supports(channel_id, "templates")
            contact = contact_repo.get_by_phone(phone)
            conv = None
            if contact:
                inbox = inbox_repo.get_by_channel(channel_id)
                if inbox:
                    conv = conversation_repo.get_latest_for_contact_inbox(
                        contact["id"], inbox["id"])
            conv_id = conv["id"] if conv else None
            last_ts = (message_repo.last_inbound_ts(conversation_id=conv_id)
                       if conv_id else None)
            return {
                "templates_supported": templates_supported,
                "session_open": outbound.session_open(channel_id, last_ts),
                "has_conversation": conv is not None,
                "conversation_id": conv_id,
                "last_inbound_ts": last_ts,
            }

        data = await asyncio.to_thread(_resolve)
        return _ok(data)

    @app.get("/api/channels/{channel_id}/templates")
    async def channel_templates(channel_id: str, request: Request):
        """Templates for a channel, used by the picker when starting a BRAND-NEW
        conversation (no conversation row yet, plano 21). Channel-scoped twin of
        ``GET /api/conversations/{id}/templates`` — same ``{supported, templates,
        can_create, can_delete}`` shape."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        outbound = deps.outbound_router
        can_create = has_permission(request, "template.create")
        can_delete = has_permission(request, "template.delete")
        if not outbound.supports(channel_id, "templates"):
            return _ok({"supported": False, "templates": [],
                        "can_create": can_create, "can_delete": can_delete})
        templates = await asyncio.to_thread(outbound.list_templates, channel_id)
        return _ok({"supported": True, "templates": templates,
                    "can_create": can_create, "can_delete": can_delete})

    @app.post("/api/channels/{channel_id}/send-template")
    async def channel_send_template(channel_id: str, body: dict, request: Request):
        """Send an approved template to ``phone`` through ``channel_id`` when there is
        no conversation yet (plano 21). Channel-scoped twin of
        ``POST /api/conversations/{id}/send-template``: saving the operator message
        creates the contact + conversation in this channel's inbox, so the new thread
        appears in the sidebar exactly like a normal first send.

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
        outbound = deps.outbound_router
        if not outbound.supports(channel_id, "templates"):
            return _err("Este canal não suporta templates.", status=400)

        result = await asyncio.to_thread(
            outbound.send_template, channel_id, phone, template_name,
            lang=language, components=components or None)
        if not result.ok:
            return _err(f"Falha ao enviar template: {result.error}", status=502)

        msg_id = result.external_msg_id or None
        preview = (body.get("preview_text") or "").strip() or f"📋 Template: {template_name}"
        try:
            msg_data = await asyncio.to_thread(
                deps.agent_handler.save_operator_message, phone, preview,
                status="operator", msg_id=msg_id, channel_id=channel_id)
        except Exception as e:  # noqa: BLE001
            return _err(f"Template enviado, mas falha ao salvar a mensagem: {e}", status=500)

        try:
            await deps.ws_manager.broadcast("new_message", {
                "phone": phone, "channel_id": channel_id, "message": msg_data})
        except Exception:
            pass
        await emit_with_filter("message.sent", {
            "phone": phone, "text": preview, "msg_id": msg_id,
            "media_type": None, "media_path": None,
            "source": "template", "status": "operator",
            "template_name": template_name, "ts": time.time(),
        })
        return _ok({"message": "Template enviado.", "msg_id": msg_id, "phone": phone})

    _TEMPLATE_CATEGORIES = {"UTILITY", "MARKETING", "AUTHENTICATION"}

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
        if category not in _TEMPLATE_CATEGORIES:
            return _err(f"category deve ser uma de {sorted(_TEMPLATE_CATEGORIES)}.", status=400)
        language = (body.get("language") or "pt_BR").strip() or "pt_BR"
        body_examples = body.get("body_examples")
        header_examples = body.get("header_examples")
        if body_examples is not None and not isinstance(body_examples, list):
            return _err("body_examples deve ser uma lista.", status=400)
        if header_examples is not None and not isinstance(header_examples, list):
            return _err("header_examples deve ser uma lista.", status=400)
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        outbound = deps.outbound_router
        if not outbound.supports(channel_id, "templates"):
            return _err("Este canal não suporta templates.", status=400)
        result = await asyncio.to_thread(
            outbound.create_template, channel_id, name,
            category=category, language=language, body_text=body_text,
            header_text=(body.get("header_text") or "").strip() or None,
            footer_text=(body.get("footer_text") or "").strip() or None,
            body_examples=body_examples or None,
            header_examples=header_examples or None)
        if not result.get("ok"):
            return _err(f"Falha ao criar template: {result.get('error')}", status=502)
        return _ok({
            "message": "Template enviado para aprovação da Meta.",
            "id": result.get("id"), "status": result.get("status"),
            "category": result.get("category"), "name": name,
        })

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
        outbound = deps.outbound_router
        if not outbound.supports(channel_id, "templates"):
            return _err("Este canal não suporta templates.", status=400)
        result = await asyncio.to_thread(outbound.delete_template, channel_id, name)
        if not result.get("ok"):
            return _err(f"Falha ao apagar template: {result.get('error')}", status=502)
        return _ok({"message": "Template apagado.", "name": name})

    @app.get("/api/channels/assignable-users")
    async def assignable_users(request: Request):
        """Active panel users for the channel agent picker (create + edit).

        Gated by ``channel.manage`` (same as the rest of this screen). Registered
        before ``/{channel_id}`` so the literal path wins the match."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        users = await asyncio.to_thread(_assignable_users)
        return _ok({"users": users})

    @app.get("/api/channels/{channel_id}")
    async def get_channel(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        creds = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
        return _ok(_serialize(row, creds))

    @app.post("/api/channels")
    async def create_channel(body: dict, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        cid = (body.get("id") or "").strip()
        provider = (body.get("provider") or "").strip()
        if provider not in _ALLOWED_PROVIDERS:
            return _err(f"provider deve ser um de: {', '.join(sorted(_ALLOWED_PROVIDERS))}.", 400)
        config = body.get("config")
        # The UI may nest gowa_device_id inside config; accept either spot.
        gowa_device_id = body.get("gowa_device_id")
        if not gowa_device_id and isinstance(config, dict):
            gowa_device_id = config.get("gowa_device_id")
        # GOWA device id is auto-generated, never user-chosen. Each channel maps to
        # its own GOWA device (X-Device-Id) on the shared GOWA process.
        if provider == "gowa" and not gowa_device_id:
            gowa_device_id = f"gowa_{uuid.uuid4().hex[:8]}"
        # Channel id is auto-generated: the user only picks a display name. GOWA
        # reuses its device id as the channel id; other providers get
        # "<provider>_<hex>". A client may still send an explicit id (back-compat):
        # it is validated and checked for uniqueness as before.
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
        row = await asyncio.to_thread(
            channel_repo.create, id=cid, provider=provider,
            display_name=body.get("display_name", "") or cid,
            gowa_device_id=gowa_device_id,
            config=json.dumps(config) if isinstance(config, (dict, list)) else config,
        )
        creds = body.get("credentials") or {}
        for key, value in creds.items():
            if value:  # never store an empty/placeholder secret
                await asyncio.to_thread(channel_credential_repo.set, cid, str(key), str(value))
        # One inbox per channel (plano 11): conversations on this channel get their
        # own thread. Best-effort — a failure here never blocks channel creation
        # (resolve_inbox_id self-heals on first inbound).
        try:
            await asyncio.to_thread(
                inbox_repo.get_or_create_for_channel, cid,
                name=row.get("display_name") or cid)
        except Exception:
            # A failed inbox creation is the root cause of channel/inbox drift
            # (órfãs, nomes velhos). resolve_inbox_id self-heals on first inbound,
            # but log it loudly so the inconsistency is visible.
            logger.warning("Falha ao criar inbox do canal %s", cid, exc_info=True)
        # Register the live channel now so status/QR/send work without a restart.
        # GOWA needs its per-device client; other providers are built generically
        # from the registered provider class (no-op if the plugin isn't loaded).
        if provider == "gowa":
            _register_live_gowa(cid, row)
        else:
            _register_live_provider(cid, provider)
        stored = await asyncio.to_thread(channel_credential_repo.get_all, cid)
        return _ok(_serialize(row, stored))

    @app.put("/api/channels/{channel_id}")
    async def update_channel(channel_id: str, body: dict, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        fields = {}
        if "display_name" in body:
            fields["display_name"] = body["display_name"]
        if "enabled" in body:
            fields["enabled"] = 1 if body["enabled"] else 0
        if "config" in body:
            cfg = body["config"]
            fields["config"] = json.dumps(cfg) if isinstance(cfg, (dict, list)) else cfg
        if fields:
            row = await asyncio.to_thread(channel_repo.update, channel_id, **fields)
            # Keep the channel's inbox name in sync (plano inboxes/canais §4.1):
            # ``channels.display_name`` is the source of truth; the UI shows it, but
            # ``inbox.name`` is kept current as cache/compat.
            if "display_name" in fields:
                inbox = await asyncio.to_thread(inbox_repo.get_by_channel, channel_id)
                if inbox:
                    await asyncio.to_thread(
                        inbox_repo.update, inbox["id"],
                        name=fields["display_name"] or channel_id)
        # A config edit may change the GOWA JID-type filter — drop its cache so
        # the webhook picks up the new allowed types without waiting for the TTL.
        if "config" in body:
            try:
                from server.routes.webhook import reset_allowed_jid_cache
                reset_allowed_jid_cache()
            except Exception:
                pass
            # Drop the per-channel AI settings cache too (plano 21) so the new
            # overrides take effect without waiting for the TTL.
            try:
                from channels import ai_settings
                ai_settings.reset_cache(channel_id)
            except Exception:
                pass
        # Credentials: a non-empty value replaces; the masked placeholder (••••) is ignored.
        for key, value in (body.get("credentials") or {}).items():
            if value and not str(value).startswith("••••"):
                await asyncio.to_thread(channel_credential_repo.set, channel_id, str(key), str(value))
        stored = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
        return _ok(_serialize(row, stored))

    @app.get("/api/channels/{channel_id}/members")
    async def get_channel_members(channel_id: str, request: Request):
        """Agents (panel users) who see/receive this channel's inbox.

        Returns the channel's inbox id, the current member user ids, and the full
        list of assignable (active) users for the picker — one round-trip for the
        editor. Gated by ``channel.manage`` (same as the rest of this screen)."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        inbox = await asyncio.to_thread(
            inbox_repo.get_or_create_for_channel, channel_id,
            name=row.get("display_name") or channel_id)
        members = await asyncio.to_thread(inbox_member_repo.member_ids, inbox["id"])
        users = await asyncio.to_thread(_assignable_users)
        return _ok({"inbox_id": inbox["id"], "member_ids": members, "users": users})

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
        inbox = await asyncio.to_thread(
            inbox_repo.get_or_create_for_channel, channel_id,
            name=row.get("display_name") or channel_id)
        members = await asyncio.to_thread(
            inbox_member_repo.set_members, inbox["id"], user_ids)
        return _ok({"inbox_id": inbox["id"], "member_ids": members})

    @app.delete("/api/channels/{channel_id}")
    async def delete_channel(channel_id: str, request: Request, purge: bool = False):
        """Remove a channel.

        Default = soft-delete (plano inboxes/canais §4.3-c): arquiva o canal e sua
        inbox, escondendo-os da UI mas PRESERVANDO o histórico de conversas. Pode
        ser desfeito via ``POST /api/channels/{id}/restore``.

        ``?purge=true`` = hard-delete: apaga o canal e a inbox de vez. A FK
        ``inboxes.channel_id → channels.id ON DELETE CASCADE`` leva conversas,
        contact_inboxes e inbox_members junto. **Destrói histórico** — irreversível.
        """
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        if channel_id == "default":
            return _err("O canal default não pode ser removido.", 400)
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        # Best-effort: log the GOWA device out so the WhatsApp number is freed.
        inst = registry.get(channel_id) if registry is not None else None
        if inst is not None and getattr(inst, "_client", None) is not None:
            try:
                await asyncio.to_thread(inst._client.logout)
            except Exception:
                pass

        if purge:
            # Hard-delete: drop the inbox first (CASCADE clears conversas/membros),
            # then the credentials and the channel row.
            inbox = await asyncio.to_thread(inbox_repo.get_by_channel, channel_id)
            if inbox:
                await asyncio.to_thread(inbox_repo.delete, inbox["id"])
            await asyncio.to_thread(channel_credential_repo.delete_all, channel_id)
            ok = await asyncio.to_thread(channel_repo.delete, channel_id)
            if not ok:
                return _err("Canal não encontrado.", 404)
            if registry is not None:
                try:
                    registry.remove_channel(channel_id)
                except Exception:
                    pass
            logger.info("Canal %s removido (purge).", channel_id)
            return _ok({"deleted": channel_id, "purged": True})

        # Soft-delete: archive the channel; the inbox + history stay intact.
        await asyncio.to_thread(channel_repo.archive, channel_id)
        if registry is not None:
            try:
                registry.remove_channel(channel_id)
            except Exception:
                pass
        logger.info("Canal %s arquivado (soft-delete).", channel_id)
        return _ok({"deleted": channel_id, "archived": True})

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
        row = await asyncio.to_thread(channel_repo.restore, channel_id)
        creds = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
        return _ok(_serialize(row, creds))

    @app.get("/api/channels/{channel_id}/status")
    async def channel_status(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        # Prefer the live instance's status; fall back to the stored flags.
        inst = registry.get(channel_id) if registry is not None else None
        if inst is not None:
            try:
                st = await asyncio.to_thread(inst.status)
                return _ok(st)
            except Exception as e:
                return _ok({"connected": False, "logged_in": False,
                            "needs_qr": False, "error": str(e)})
        return _ok({
            "connected": bool(row.get("connected")),
            "logged_in": bool(row.get("logged_in")),
            "needs_qr": False,
            "error": row.get("last_error"),
        })

    @app.get("/api/channels/{channel_id}/qr")
    async def channel_qr(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        if row.get("provider") != "gowa":
            return _err("QR disponível apenas para canais GOWA.", 400)
        inst = registry.get(channel_id) if registry is not None else None
        # The channel may exist in the DB but not be live yet (created before this
        # boot, or registry unavailable) — build/register it on demand.
        if inst is None:
            _register_live_gowa(channel_id, row)
            inst = registry.get(channel_id) if registry is not None else None
        if inst is None or not hasattr(inst, "qr"):
            return _err("Canal GOWA indisponível.", 503)
        try:
            png = await asyncio.to_thread(inst.qr)
        except Exception as e:  # noqa: BLE001
            return _err(f"Falha ao obter QR: {e}", 502)
        if not png:
            # Already logged in, or GOWA not ready yet — 204 tells the UI to poll status.
            return Response(status_code=204)
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})
