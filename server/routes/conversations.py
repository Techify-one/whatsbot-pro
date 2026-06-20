"""Conversation endpoints (plano 01 Fase 1). Gated by conversation.* permissions."""

import asyncio
import logging
import time

from fastapi import Request

from db.repositories import conversation_repo, custom_attribute_repo, contact_repo
from db import filters as conv_filters
from db.filters.translate import FilterContext
from plugins.events import emit_with_filter
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


def register_routes(app, deps):

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
        if attrs is not None:
            if not isinstance(attrs, dict):
                return _err("custom_attributes deve ser um objeto.", status=400)
            defs = await asyncio.to_thread(
                custom_attribute_repo.get_definitions_map, "conversation")
            unknown = [k for k in attrs if k not in defs]
            if unknown:
                return _err(f"Atributos desconhecidos: {', '.join(unknown)}.", status=400)
            merged = dict(conv.get("custom_attributes") or {})
            merged.update(attrs)
            conv = await asyncio.to_thread(
                conversation_repo.set_custom_attributes, conv_id, merged)
        await _broadcast(deps, "conversation_updated", "conversation.updated", conv,
                         fields={"custom_attributes": conv.get("custom_attributes")})
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
