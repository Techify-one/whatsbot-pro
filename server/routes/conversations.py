"""Conversation endpoints (plano 01 Fase 1). Gated by conversation.* permissions."""

import asyncio
import logging
import time

from fastapi import Request

from db.repositories import (conversation_repo, custom_attribute_repo, contact_repo,
                             message_repo, user_repo, agent_repo)
from server.avatars import avatar_version
from db import filters as conv_filters
from db.filters.translate import FilterContext
from plugins.events import emit_with_filter
from server import system_notices
from server.authz import permission_denied, current_user
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
        "status": conv.get("status"),
        "assignee_user_id": conv.get("assignee_user_id"),
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
            is_archived=1 if archived else 0, limit=limit, offset=offset)
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
            conversation_repo.list_filtered, where, limit=spec.limit, offset=spec.offset)
        return _ok({"conversations": rows, "count": len(rows)})

    @app.get("/api/conversations/{conv_id}")
    async def get_conversation(conv_id: int, request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        conv = await asyncio.to_thread(conversation_repo.get, conv_id)
        if not conv:
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

        def _load():
            conv = conversation_repo.get_with_channel(conv_id)
            if conv is None:
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
        return _ok({
            "conversation": conv,
            "contact": contact,
            "messages": msgs,
            "channel_id": channel_id,
            "avatar_v": avatar_version(settings, phone),
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
            unknown = [k for k in attrs if k not in defs]
            if unknown:
                return _err(f"Atributos desconhecidos: {', '.join(unknown)}.", status=400)
            previous = dict(conv.get("custom_attributes") or {})
            # Only the keys whose value actually changed feed the notice (no card
            # for a no-op save).
            changed = {k: v for k, v in attrs.items() if previous.get(k) != v}
            merged = dict(previous)
            merged.update(attrs)
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

    @app.get("/api/contacts/{phone}/conversation")
    async def contact_conversation(phone: str, request: Request):
        """Resolve the active (open) conversation for a contact by phone — feeds the
        chat header (display_id, status, assignee, ai_active, custom_attributes)."""
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        contact = await asyncio.to_thread(contact_repo.get_by_phone, phone)
        if not contact:
            return _err("Contato não encontrado.", status=404)
        conv = await asyncio.to_thread(
            conversation_repo.get_open_for_contact, contact["id"])
        return _ok({"conversation": conv})
