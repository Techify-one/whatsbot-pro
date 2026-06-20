"""Conversation endpoints (plano 01 Fase 1). Gated by conversation.* permissions."""

import asyncio
import logging
import time

from fastapi import Request

from db.repositories import (conversation_repo, custom_attribute_repo, contact_repo,
                             message_repo, user_repo, agent_repo)
from db.repositories.custom_attribute_validate import validate_value
from server.avatars import avatar_version
from db import filters as conv_filters
from db.filters.translate import FilterContext
from plugins.events import emit_with_filter
from server import system_notices
from server.authz import permission_denied, current_user, visible_inbox_ids
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def _filter_context(request: Request) -> FilterContext:
    user = current_user(request)
    cattr_keys = frozenset(
        d["attribute_key"] for d in custom_attribute_repo.list_filterable("conversation"))
    return FilterContext(
        user_id=(user or {}).get("id"), now=time.time(), cattr_keys=cattr_keys)


async def _broadcast(deps, ws_event: str, bus_event: str, conv: dict, **extra):
    """Push a conversation change to the panel (WS) and the plugin bus.

    ``conv`` is the updated row; ``extra`` overrides/adds payload keys. Defensive:
    a broadcast failure never fails the HTTP action.
    """
    payload = {
        "conversation_id": conv.get("id"),
        "display_id": conv.get("display_id"),
        "contact_id": conv.get("contact_id"),
        "status": conv.get("status"),
        "assignee_user_id": conv.get("assignee_user_id"),
        "active_agent_key": conv.get("active_agent_key"),
        "ai_active": conv.get("ai_active"),
        "is_archived": conv.get("is_archived"),
        "inbox_id": conv.get("inbox_id"),
        **extra,
        "ts": time.time(),
    }
    try:
        await deps.ws_manager.broadcast(ws_event, payload)
    except Exception as e:
        logger.debug("WS broadcast %s failed: %s", ws_event, e)
    try:
        await emit_with_filter(bus_event, payload)
    except Exception as e:
        logger.debug("bus emit %s failed: %s", bus_event, e)


async def _emit_notice(request: Request, conv: dict, event_type: str, **ctx):
    """Write a conversation lifecycle notice into the chat thread (plano 12).

    Resolves the actor name from the current user (``None`` ⇒ neutral phrasing)
    and delegates to ``system_notices.emit_conversation_notice`` off-thread. The
    config gate (global, per group) decides whether anything is stored/emitted, so
    call sites stay dumb. Never raises — a failed notice never fails the action.
    """
    user = current_user(request)
    actor = (user or {}).get("name") or None
    try:
        await asyncio.to_thread(
            system_notices.emit_conversation_notice,
            event_type=event_type,
            conversation_id=conv.get("id"),
            contact_id=conv.get("contact_id"),
            actor=actor,
            **ctx,
        )
    except Exception as e:
        logger.debug("conversation notice %s failed: %s", event_type, e)


def _inbox_hidden(request: Request, inbox_id) -> bool:
    """True if inbox membership scoping hides ``inbox_id`` from the current user.

    Mirrors the list filter so single-conversation reads stay consistent: a user
    scoped to a set of inboxes (not admin / not ``conversation.read_all``) gets a
    404 for conversations outside that set."""
    vis = visible_inbox_ids(request)
    return vis is not None and inbox_id not in vis


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    settings = deps.settings
    outbound = deps.outbound_router

    async def _send_conv_read_receipts(channel_id: str, phone: str, msg_ids: list[str]):
        """Read receipts for ONE conversation, routed through its own channel."""
        for mid in msg_ids:
            try:
                await asyncio.to_thread(outbound.mark_read, channel_id, phone, mid)
            except Exception as e:
                logger.debug("[ReadReceipt] conv %s/%s failed: %s", phone, mid, e)

    @app.get("/api/conversations")
    async def list_conversations(request: Request, status: str | None = None,
                                 inbox_id: int | None = None,
                                 assignee_user_id: int | None = None,
                                 archived: bool = False, limit: int = 100,
                                 offset: int = 0):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        limit = max(1, min(limit, 200))
        rows = await asyncio.to_thread(
            conversation_repo.list_conversations,
            status=status, inbox_id=inbox_id, assignee_user_id=assignee_user_id,
            is_archived=1 if archived else 0,
            inbox_ids=visible_inbox_ids(request), limit=limit, offset=offset)
        return _ok({"conversations": rows})

    @app.get("/api/conversations/filter-schema")
    async def filter_schema(request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        defs = await asyncio.to_thread(custom_attribute_repo.list_filterable, "conversation")
        return _ok({"dimensions": conv_filters.available_dimensions(defs)})

    @app.get("/api/conversations/filter")
    async def filter_get(request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        params = dict(request.query_params)
        return await _run_filter(request, None, params=params)

    @app.post("/api/conversations/filter")
    async def filter_post(body: dict, request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        return await _run_filter(request, body, params=None)

    async def _run_filter(request: Request, payload, *, params):
        from db.filters.spec import from_params, from_payload
        try:
            spec = from_params(params) if params is not None else from_payload(payload)
            ctx = _filter_context(request)
            where = await asyncio.to_thread(conv_filters.build_where, spec, ctx)
        except conv_filters.FilterError as e:
            return _err(str(e), status=400)
        except (TypeError, ValueError, IndexError, KeyError) as e:
            # Safety net: malformed input must be a clean 400, never a 500.
            logger.warning("Filtro inválido: %s", e)
            return _err("Filtro inválido.", status=400)
        rows = await asyncio.to_thread(
            conversation_repo.list_filtered, where,
            inbox_ids=visible_inbox_ids(request), limit=spec.limit, offset=spec.offset)
        return _ok({"conversations": rows, "count": len(rows)})

    @app.get("/api/conversations/assignable-agents")
    async def assignable_agents(request: Request):
        """Agents that can take a conversation (plano 10): human users + AI agents,
        in one list for the unified assignee picker. Gated by conversation.read so
        attendants (not only users.manage) can transfer. Registered before the
        ``/{conv_id}`` route so the literal path is matched first."""
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        users = await asyncio.to_thread(user_repo.list_all)
        agents = await asyncio.to_thread(agent_repo.list_all)
        human_list = [
            {"id": u["id"], "name": u.get("name") or u.get("email"),
             "email": u.get("email"), "is_admin": bool(u.get("is_admin"))}
            for u in users if u.get("is_active")
        ]
        ai_list = [
            {"agent_key": a["agent_key"], "display_name": a.get("display_name") or a["agent_key"]}
            for a in agents if a.get("enabled")
        ]
        return _ok({"users": human_list, "ai_agents": ai_list})

    @app.get("/api/conversations/{conv_id}")
    async def get_conversation(conv_id: int, request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        # Enriched row (channel_id/provider/name + display fields + custom_attributes)
        # so the "Informações da conversa" panel has everything it renders (A.3).
        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        if _inbox_hidden(request, conv.get("inbox_id")):
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.get("/api/conversations/{conv_id}/messages")
    async def conversation_messages(conv_id: int, request: Request, mark_read: bool = True):
        """Messages of ONE conversation (conversa-cêntrico, plano 11 D1).

        Substitui GET /api/contacts/{phone} para o chat: escopa o thread a um único
        canal (não funde os canais do mesmo número) e marca como lida APENAS esta
        conversa. Devolve conversa + contato (shape do chat) + mensagens + channel_id.
        """
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        vis = visible_inbox_ids(request)

        def _load():
            conv = conversation_repo.get_with_channel(conv_id)
            if conv is None:
                return None, None, [], []
            # Inbox membership scoping: hide (as 404) before any mark-read side effect.
            if vis is not None and conv.get("inbox_id") not in vis:
                return None, None, [], []
            phone = conv.get("contact_phone") or ""
            contact = contact_repo.get_full_contact(phone) if phone else None
            ids: list[str] = []
            if mark_read and conv.get("unread_count", 0) > 0:
                ids = conversation_repo.mark_conversation_read(conv_id)
                conv["unread_count"] = 0
                # keep the in-RAM contact cache (keyed by phone) roughly in sync
                for cm in agent_handler.iter_cached_contacts(phone):
                    if cm.unread_count:
                        cm.unread_count = max(0, cm.unread_count - len(ids))
            msgs = message_repo.get_by_conversation(conv_id)
            return conv, contact, msgs, ids

        conv, contact, msgs, msg_ids = await asyncio.to_thread(_load)
        if conv is None:
            return _err("Conversa não encontrada.", status=404)
        channel_id = conv.get("channel_id") or "default"
        phone = conv.get("contact_phone") or ""
        if msg_ids:
            asyncio.create_task(_send_conv_read_receipts(channel_id, phone, msg_ids))
        # Compositor hints (Frente C): whether this channel can send templates, and
        # whether the free-text session window is still open (else only a template
        # may be sent). Capability-driven — no provider-name checks.
        last_inbound_ts = max(
            (m.get("ts") or 0 for m in msgs if m.get("role") == "user"), default=None)
        return _ok({
            "conversation": conv,
            "contact": contact,
            "messages": msgs,
            "channel_id": channel_id,
            "avatar_v": avatar_version(settings, phone),
            "templates_supported": outbound.supports(channel_id, "templates"),
            "session_open": outbound.session_open(channel_id, last_inbound_ts),
        })

    @app.post("/api/conversations/{conv_id}/status")
    async def set_status(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.resolve")
        if denied:
            return denied
        status = (body.get("status") or "").strip()
        if status not in ("open", "closed"):
            return _err("status deve ser 'open' ou 'closed'.", status=400)
        conv = await asyncio.to_thread(conversation_repo.set_status, conv_id, status)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        await _broadcast(deps, "conversation_status_changed", "conversation.status_changed", conv)
        await _emit_notice(request, conv,
                           "status_closed" if status == "closed" else "status_open")
        return _ok({"conversation": conv})

    @app.post("/api/conversations/{conv_id}/assign")
    async def assign(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.assign")
        if denied:
            return denied
        assignee = body.get("assignee_user_id")
        conv = await asyncio.to_thread(conversation_repo.set_assignee, conv_id, assignee)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        await _broadcast(deps, "conversation_assigned", "conversation.assigned", conv)
        if assignee:
            target = await asyncio.to_thread(user_repo.get, assignee)
            await _emit_notice(request, conv, "assigned",
                               target=(target or {}).get("name") or f"usuário #{assignee}")
        else:
            await _emit_notice(request, conv, "unassigned")
        return _ok({"conversation": conv})

    @app.post("/api/conversations/{conv_id}/assign-me")
    async def assign_me(conv_id: int, request: Request):
        denied = permission_denied(request, "conversation.assign")
        if denied:
            return denied
        user = current_user(request)
        if not user:
            return _err("Precisa estar autenticado para assumir a conversa.", status=401)
        conv = await asyncio.to_thread(conversation_repo.set_assignee, conv_id, user["id"])
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        await _broadcast(deps, "conversation_assigned", "conversation.assigned", conv,
                         by_user_id=user["id"])
        await _emit_notice(request, conv, "assigned_me")
        return _ok({"conversation": conv})

    @app.post("/api/conversations/{conv_id}/agent")
    async def set_agent(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        agent_key = body.get("agent_key") or None
        conv = await asyncio.to_thread(conversation_repo.set_agent, conv_id, agent_key)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        await _broadcast(deps, "conversation_updated", "conversation.updated", conv,
                         fields={"active_agent_key": conv.get("active_agent_key")})
        agent_name = None
        if conv.get("active_agent_key"):
            ag = await asyncio.to_thread(agent_repo.get, conv["active_agent_key"])
            agent_name = (ag or {}).get("display_name") or conv["active_agent_key"]
        await _emit_notice(request, conv, "agent_changed", agent=agent_name)
        return _ok({"conversation": conv})

    @app.post("/api/conversations/{conv_id}/assign-agent")
    async def assign_agent(conv_id: int, body: dict, request: Request):
        """Unified assignment (plano 10): route to a human OR an AI agent.

        body: ``{"kind": "user"|"ai"|"none", "user_id"?: int, "agent_key"?: str}``
        - ``user`` → set assignee, clear the AI agent, turn the IA OFF (a person took it).
        - ``ai``   → set the AI agent, clear the human assignee, turn the IA ON.
        - ``none`` → unassign (clear both); leave the IA gate as it is.

        Assigning to a person/AI also flips the CONTACT-level ``ai_enabled`` so the
        "IA OFF"/"IA" badge on the inbox row reflects who is handling the chat: a
        human takes over → IA OFF; an AI agent takes over → IA back ON.
        """
        denied = permission_denied(request, "conversation.assign")
        if denied:
            return denied
        kind = (body.get("kind") or "").strip()
        contact_ai_enabled = None  # None = leave the contact-level gate untouched
        if kind == "user":
            uid = body.get("user_id")
            if not isinstance(uid, int):
                return _err("user_id é obrigatório para kind=user.", status=400)
            conv = await asyncio.to_thread(
                conversation_repo.assign_agent, conv_id,
                assignee_user_id=uid, active_agent_key=None, ai_active=0)
            contact_ai_enabled = False
        elif kind == "ai":
            agent_key = (body.get("agent_key") or "").strip()
            if not agent_key:
                return _err("agent_key é obrigatório para kind=ai.", status=400)
            conv = await asyncio.to_thread(
                conversation_repo.assign_agent, conv_id,
                assignee_user_id=None, active_agent_key=agent_key, ai_active=1)
            contact_ai_enabled = True
        elif kind == "none":
            conv = await asyncio.to_thread(
                conversation_repo.assign_agent, conv_id,
                assignee_user_id=None, active_agent_key=None, ai_active=None)
        else:
            return _err("kind deve ser 'user', 'ai' ou 'none'.", status=400)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        # Sync the contact-level AI gate + badge with the new owner.
        if contact_ai_enabled is not None:
            contact = await asyncio.to_thread(contact_repo.get, conv["contact_id"])
            if contact:
                await asyncio.to_thread(
                    contact_repo.update, contact["id"],
                    ai_enabled=1 if contact_ai_enabled else 0)
                try:
                    await deps.ws_manager.broadcast("contact_ai_toggled", {
                        "phone": contact["phone"], "ai_enabled": contact_ai_enabled})
                    await emit_with_filter("contact.ai_toggled", {
                        "phone": contact["phone"], "ai_enabled": contact_ai_enabled})
                except Exception as e:
                    logger.debug("contact_ai_toggled broadcast failed: %s", e)
        await _broadcast(deps, "conversation_assigned", "conversation.assigned", conv)
        return _ok({"conversation": conv})

    @app.post("/api/conversations/{conv_id}/ai")
    async def set_ai(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        active = 1 if body.get("active") else 0
        conv = await asyncio.to_thread(conversation_repo.set_ai_active, conv_id, active)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        await _broadcast(deps, "conversation_ai_toggled", "conversation.ai_toggled", conv)
        await _emit_notice(request, conv, "ai_on" if active else "ai_off")
        return _ok({"conversation": conv})

    @app.post("/api/conversations/{conv_id}/archive")
    async def archive(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        archived = 1 if body.get("archived") else 0
        conv = await asyncio.to_thread(conversation_repo.set_archived, conv_id, archived)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        await _broadcast(deps, "conversation_archived", "conversation.archived", conv)
        await _emit_notice(request, conv, "archived" if archived else "unarchived")
        return _ok({"conversation": conv})

    @app.put("/api/conversations/{conv_id}/info")
    async def update_info(conv_id: int, body: dict, request: Request):
        """Update conversation custom_attributes (FF5). Validates keys against the
        conversation attribute definitions; unknown keys are rejected."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        conv = await asyncio.to_thread(conversation_repo.get, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        attrs = body.get("custom_attributes")
        changed: dict = {}
        defs: dict = {}
        if attrs is not None:
            if not isinstance(attrs, dict):
                return _err("custom_attributes deve ser um objeto.", status=400)
            defs = await asyncio.to_thread(
                custom_attribute_repo.get_definitions_map, "conversation")
            # Validate keys (P50: unknown → error) AND values (type/regex parity
            # with the contact PUT /info), returning a clean 400 before writing.
            valid: dict = {}
            for key, value in attrs.items():
                definition = defs.get(key)
                if definition is None:
                    return _err(f"Atributo '{key}' não existe.", status=400)
                norm, err = validate_value(definition, value)
                if err:
                    return _err(err, status=400)
                valid[key] = norm
            previous = dict(conv.get("custom_attributes") or {})
            # Only the keys whose value actually changed feed the notice (no card
            # for a no-op save). A None value clears the attribute (mirrors set_values).
            changed = {k: v for k, v in valid.items() if previous.get(k) != v}
            merged = dict(previous)
            for k, v in valid.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            conv = await asyncio.to_thread(
                conversation_repo.set_custom_attributes, conv_id, merged)
        await _broadcast(deps, "conversation_updated", "conversation.updated", conv,
                         fields={"custom_attributes": conv.get("custom_attributes")})
        if changed:
            # Aggregate a batch of attribute changes into a single card (plano 12 §6).
            if len(changed) == 1:
                key, value = next(iter(changed.items()))
                label = (defs.get(key) or {}).get("display_name") or key
                await _emit_notice(request, conv, "attribute_set",
                                   attribute=label, value=value)
            else:
                await _emit_notice(request, conv, "attribute_set", count=len(changed))
        return _ok({"conversation": conv})

    @app.get("/api/conversations/{conv_id}/templates")
    async def conversation_templates(conv_id: int, request: Request):
        """Approved templates for the picker (Frente C). Channel-aware: resolves the
        conversation's channel and returns ``{supported, templates}``. Non-template
        channels (GOWA) return ``supported=False`` with no provider call."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        channel_id = conv.get("channel_id") or "default"
        if not outbound.supports(channel_id, "templates"):
            return _ok({"supported": False, "templates": []})
        templates = await asyncio.to_thread(outbound.list_templates, channel_id)
        return _ok({"supported": True, "templates": templates})

    @app.post("/api/conversations/{conv_id}/send-template")
    async def send_conversation_template(conv_id: int, body: dict, request: Request):
        """Send an approved template through the conversation's channel (Frente C).

        body: ``{template_name, language?, components?, preview_text?}`` — components
        are the filled Graph parameters (built by the UI from the template
        definition). Persists the sent message (operator), broadcasts ``new_message``
        and emits ``message.sent`` (source ``template``), mirroring operator sends.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        template_name = (body.get("template_name") or "").strip()
        language = (body.get("language") or "pt_BR").strip() or "pt_BR"
        components = body.get("components")
        if not template_name:
            return _err("template_name é obrigatório.", status=400)
        if components is not None and not isinstance(components, list):
            return _err("components deve ser uma lista.", status=400)
        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        channel_id = conv.get("channel_id") or "default"
        phone = conv.get("contact_phone") or ""
        if not outbound.supports(channel_id, "templates"):
            return _err("Este canal não suporta templates.", status=400)
        if not phone:
            return _err("Conversa sem número de destino.", status=400)

        result = await asyncio.to_thread(
            outbound.send_template, channel_id, phone, template_name,
            lang=language, components=components or None)
        if not result.ok:
            return _err(f"Falha ao enviar template: {result.error}", status=502)

        msg_id = result.external_msg_id or None
        preview = (body.get("preview_text") or "").strip() or f"📋 Template: {template_name}"
        try:
            msg_data = await asyncio.to_thread(
                agent_handler.save_operator_message, phone, preview,
                status="operator", msg_id=msg_id)
        except Exception as e:  # noqa: BLE001
            logger.error("[Template] save failed for %s: %s", phone, e)
            return _err(f"Template enviado, mas falha ao salvar a mensagem: {e}", status=500)

        try:
            await deps.ws_manager.broadcast("new_message", {
                "phone": phone, "channel_id": channel_id, "message": msg_data})
        except Exception as e:  # noqa: BLE001
            logger.debug("template new_message broadcast failed: %s", e)
        await emit_with_filter("message.sent", {
            "phone": phone, "text": preview, "msg_id": msg_id,
            "media_type": None, "media_path": None,
            "source": "template", "status": "operator",
            "template_name": template_name, "ts": time.time(),
        })
        return _ok({"message": "Template enviado.", "msg_id": msg_id})

    @app.get("/api/contacts/{phone}/conversation")
    async def contact_conversation(phone: str, request: Request,
                                   include_closed: bool = False):
        """Resolve the conversation for a contact by phone — feeds the chat header
        (display_id, status, assignee, ai_active, custom_attributes).

        By default returns the active (open) thread. With ``include_closed`` it
        returns the latest conversation regardless of status, so the sidebar
        right-click menu can still show assignee/reopen on a resolved thread.
        """
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        contact = await asyncio.to_thread(contact_repo.get_by_phone, phone)
        if not contact:
            return _err("Contato não encontrado.", status=404)
        if include_closed:
            conv = await asyncio.to_thread(
                conversation_repo.get_latest_for_contact, contact["id"])
        else:
            conv = await asyncio.to_thread(
                conversation_repo.get_open_for_contact, contact["id"])
            # Fall back to the latest (resolved) thread so the header can still show
            # the conversation's status + the Reabrir action after it was resolved.
            if not conv:
                conv = await asyncio.to_thread(
                    conversation_repo.get_latest_for_contact, contact["id"])
        return _ok({"conversation": conv})
