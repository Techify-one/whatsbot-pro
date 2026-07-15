"""Conversation endpoints (plano 01 Fase 1). Gated by conversation.* permissions.

Plano 23 Fase B4: the LIFECYCLE + OWNERSHIP behaviour (status / assign / archive /
attributes / agent / per-conversation AI) lives in
``app.services.conversation_service`` (Branch by Abstraction). The routes here own
the HTTP surface — permission check, inbox-scope guard, body validation, 404
mapping — then delegate the WRITE + the side effects (WS broadcast, plugin-bus
emit, system-notice card) to that service, which guarantees every domain event
fires EXACTLY once. The route no longer emits anything it delegates.
"""

import asyncio
import logging
import time

from fastapi import Request

from db.repositories import (conversation_repo, custom_attribute_repo, contact_repo,
                             message_repo, user_repo, agent_repo, mention_repo)
from db.repositories.custom_attribute_validate import validate_value
from server.avatars import avatar_version
from db import filters as conv_filters
from db.filters.translate import FilterContext
from plugins.events import emit_with_filter
from server.authz import permission_denied, has_permission, current_user, visible_inbox_ids
from server.helpers import _ok, _err
from server.pagination import CAP_MSGS, PAGE_MSGS, clamp_limit

logger = logging.getLogger(__name__)


def _filter_context(request: Request) -> FilterContext:
    user = current_user(request)
    cattr_keys = frozenset(
        d["attribute_key"] for d in custom_attribute_repo.list_filterable("conversation"))
    return FilterContext(
        user_id=(user or {}).get("id"), now=time.time(), cattr_keys=cattr_keys)


def _actor(request: Request) -> tuple[object, str | None]:
    """Resolve the (actor_id, actor_name) pair the service needs for events +
    notices, so the service stays free of the ``Request`` object. ``actor_name``
    is ``None`` for an anonymous caller (neutral notice phrasing)."""
    user = current_user(request)
    return (user or {}).get("id"), ((user or {}).get("name") or None)


def _inbox_hidden(request: Request, inbox_id) -> bool:
    """True if inbox membership scoping hides ``inbox_id`` from the current user.

    Mirrors the list filter so single-conversation reads stay consistent: a user
    scoped to a set of inboxes (not admin / not ``conversation.read_all``) gets a
    404 for conversations outside that set."""
    vis = visible_inbox_ids(request)
    return vis is not None and inbox_id not in vis


async def _guard_conv(request: Request, conv_id: int):
    """Load a conversation and enforce inbox-membership scoping on WRITE paths.

    Returns ``(conv, None)`` when the caller may act on it, or ``(None, err)``
    (a 404 response) when the conversation is missing OR hidden by the user's
    inbox scope. Closes Bug 2 (plano inboxes/canais §4.7): ações mutadoras por
    conversa passam a checar a caixa, não só a leitura."""
    conv = await asyncio.to_thread(conversation_repo.get, conv_id)
    if not conv:
        return None, _err("Conversa não encontrada.", status=404)
    if _inbox_hidden(request, conv.get("inbox_id")):
        return None, _err("Conversa não encontrada.", status=404)
    return conv, None


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    settings = deps.settings
    outbound = deps.outbound_router

    # Conversation lifecycle + ownership service (plano 23 Fase B4). Imported here
    # (not at module top) to mirror B3's cycle-avoidance: the service imports
    # ``messaging_service`` which imports back into ``server``; deferring to call
    # time keeps this module importable in any order.
    from app.services import conversation_service as conv_svc

    async def _send_conv_read_receipts(channel_id: str, phone: str, msg_ids: list[str]):
        """Read receipts for ONE conversation, routed through its own channel."""
        for mid in msg_ids:
            # Notas privadas notificadas usam msg_id sintético ("pn:…") inexistente no
            # provedor — pular (não é uma mensagem real do canal).
            if str(mid).startswith("pn:"):
                continue
            try:
                await asyncio.to_thread(outbound.mark_read, channel_id, phone, mid)
            except Exception as e:
                logger.debug("[ReadReceipt] conv %s/%s failed: %s", phone, mid, e)

    @app.get("/api/atendimentos")
    async def list_conversations(request: Request, status: str | None = None,
                                 inbox_id: int | None = None,
                                 assignee_user_id: int | None = None,
                                 archived: bool = False, limit: int = 100,
                                 offset: int = 0):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        limit = max(1, min(limit, 200))
        _u = current_user(request)
        rows = await asyncio.to_thread(
            conversation_repo.list_conversations,
            status=status, inbox_id=inbox_id, assignee_user_id=assignee_user_id,
            is_archived=1 if archived else 0,
            inbox_ids=visible_inbox_ids(request),
            current_user_id=(_u.get("id") if _u else None),
            limit=limit, offset=offset)
        # avatar_v por row (plano 50 F8): o sidebar conversa-first monta a foto sem um
        # fetch de contatos à parte. has_more = veio a página cheia (há próxima).
        for r in rows:
            r["avatar_v"] = avatar_version(settings, r.get("contact_phone") or "")
        return _ok({"conversations": rows, "has_more": len(rows) >= limit})

    @app.get("/api/atendimentos/filter-schema")
    async def filter_schema(request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        defs = await asyncio.to_thread(custom_attribute_repo.list_filterable, "conversation")
        return _ok({"dimensions": conv_filters.available_dimensions(defs)})

    @app.get("/api/atendimentos/filter")
    async def filter_get(request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        params = dict(request.query_params)
        return await _run_filter(request, None, params=params)

    @app.post("/api/atendimentos/filter")
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
        _u = current_user(request)
        rows = await asyncio.to_thread(
            conversation_repo.list_filtered, where,
            inbox_ids=visible_inbox_ids(request),
            current_user_id=(_u.get("id") if _u else None),
            limit=spec.limit, offset=spec.offset)
        return _ok({"conversations": rows, "count": len(rows)})

    @app.get("/api/atendimentos/assignable-agents")
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

    @app.get("/api/mentions/unread-count")
    async def mentions_unread_count(request: Request):
        """Quantas menções não-lidas o usuário logado tem (badge da aba Menções).
        Gated por conversation.read. Registrado antes do ``/{conv_id}`` literal."""
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        _u = current_user(request)
        uid = _u.get("id") if _u else None
        count = 0 if uid is None else await asyncio.to_thread(mention_repo.unread_count, uid)
        return _ok({"count": count})

    @app.get("/api/atendimentos/{conv_id}")
    async def get_conversation(conv_id: int, request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        # Enriched row (channel_id/provider/name + display fields + custom_attributes)
        # so the "Informações da conversa" panel has everything it renders (A.3).
        _u = current_user(request)
        conv = await asyncio.to_thread(
            conversation_repo.get_with_channel, conv_id,
            (_u.get("id") if _u else None))
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        if _inbox_hidden(request, conv.get("inbox_id")):
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.get("/api/atendimentos/{conv_id}/messages")
    async def conversation_messages(conv_id: int, request: Request,
                                    mark_read: bool = True,
                                    limit: int = PAGE_MSGS,
                                    before_id: int | None = None):
        """Messages of ONE conversation (conversa-cêntrico, plano 11 D1).

        Substitui GET /api/contacts/{phone} para o chat: escopa o thread a um único
        canal (não funde os canais do mesmo número) e marca como lida APENAS esta
        conversa. Devolve conversa + contato (shape do chat) + mensagens + channel_id.

        Paginação keyset (plano 50 F3): devolve a PÁGINA mais recente (as ``limit``
        últimas, capado em ``CAP_MSGS``). ``before_id`` (id da 1ª msg da página atual)
        traz as ``limit`` anteriores — o "carregar anteriores" do scroll-up. ``has_more``
        avisa se ainda há msgs mais antigas. Ordem sempre cronológica (oldest→newest).
        """
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        page_limit = clamp_limit(limit, PAGE_MSGS, CAP_MSGS)
        vis = visible_inbox_ids(request)
        can_read_contact = has_permission(request, "contact.read")
        _u = current_user(request)
        _uid = _u.get("id") if _u else None

        def _load():
            conv = conversation_repo.get_with_channel(conv_id, _uid)
            if conv is None:
                return None, None, [], [], False, None
            # Inbox membership scoping: hide (as 404) before any mark-read side effect.
            if vis is not None and conv.get("inbox_id") not in vis:
                return None, None, [], [], False, None
            phone = conv.get("contact_phone") or ""
            if can_read_contact:
                contact = contact_repo.get_full_contact(phone) if phone else None
            else:
                contact = {
                    "id": conv.get("contact_id"),
                    "phone": phone,
                    "is_group": bool(conv.get("contact_is_group")),
                    "info": {},
                    "tags": [],
                }
            ids: list[str] = []
            if mark_read and conv.get("unread_count", 0) > 0:
                ids = conversation_repo.mark_conversation_read(conv_id)
                conv["unread_count"] = 0
                # keep the in-RAM contact cache (keyed by phone) roughly in sync
                for cm in agent_handler.iter_cached_contacts(phone):
                    if cm.unread_count:
                        cm.unread_count = max(0, cm.unread_count - len(ids))
            # Abrir a conversa limpa as MINHAS menções não-lidas nela (badge "@" + aba
            # Menções). Independe de unread_count (menção pode existir sem não-lida). Best-effort.
            if mark_read and _uid is not None and conv.get("has_user_mention"):
                try:
                    mention_repo.mark_read(_uid, conv_id)
                    conv["has_user_mention"] = False
                except Exception:
                    logger.exception("Falha ao marcar menções lidas na conversa %s", conv_id)
            # Keyset (plano 50 F3): over-fetch por 1 p/ detectar has_more sem 2ª query.
            # A msg extra é a mais ANTIGA da janela (lista cronológica) → dropa índice 0.
            msgs = message_repo.get_by_conversation(
                conv_id, limit=page_limit + 1, before_id=before_id)
            has_more = len(msgs) > page_limit
            if has_more:
                msgs = msgs[1:]
            # Atribuição de agente: resolve agent_key → display_name (dedupe por chave)
            # para o painel exibir "IA - <NOME>" / "Ferramenta IA - <NOME>".
            _an_cache: dict = {}
            for _m in msgs:
                _ak = _m.get("agent_key")
                if not _ak:
                    continue
                if _ak not in _an_cache:
                    _an_cache[_ak] = agent_repo.display_name_for(_ak)
                if _an_cache[_ak]:
                    _m["agent_name"] = _an_cache[_ak]
            # Janela Cloud 24h: SEMPRE a query dedicada (não o max(ts) da página — que
            # com paginação só veria a página recente e poderia "fechar" errado). Risco
            # apontado no plano; mesmo precedente de contacts.py.
            last_in = message_repo.last_inbound_ts(conversation_id=conv_id)
            return conv, contact, msgs, ids, has_more, last_in

        conv, contact, msgs, msg_ids, has_more, last_inbound_ts = await asyncio.to_thread(_load)
        if conv is None:
            return _err("Conversa não encontrada.", status=404)
        channel_id = conv.get("channel_id") or "default"
        phone = conv.get("contact_phone") or ""
        if msg_ids:
            asyncio.create_task(_send_conv_read_receipts(channel_id, phone, msg_ids))
        # Compositor hints (Frente C): whether this channel can send templates, and
        # whether the free-text session window is still open (else only a template
        # may be sent). Capability-driven — no provider-name checks.
        return _ok({
            "conversation": conv,
            "contact": contact,
            "messages": msgs,
            "has_more": has_more,
            "channel_id": channel_id,
            "avatar_v": avatar_version(settings, phone) if can_read_contact else None,
            "templates_supported": outbound.supports(channel_id, "templates"),
            "session_open": outbound.session_open(channel_id, last_inbound_ts),
        })

    @app.post("/api/atendimentos/{conv_id}/status")
    async def set_status(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.resolve")
        if denied:
            return denied
        status = (body.get("status") or "").strip()
        if status not in ("open", "closed"):
            return _err("status deve ser 'open' ou 'closed'.", status=400)
        _conv, err = await _guard_conv(request, conv_id)
        if err:
            return err
        actor_id, actor_name = _actor(request)
        # The service applies ``filter.conversation.before_status`` (a plugin may
        # REFUSE closing by returning None — the server-side counterpart of the
        # frontend beforeResolve popup), owns the close POLICY (clear assignee +
        # active agent), and emits conversation.status_changed (+ conversation.reopened
        # on a closed→open transition) + the status notice, each exactly once.
        conv = await conv_svc.set_status(deps, _conv, status,
                                         actor_id=actor_id, actor_name=actor_name)
        if conv == "blocked":
            return _err("Fechamento bloqueado por um plugin.", status=403)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.post("/api/atendimentos/{conv_id}/assign")
    async def assign(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.assign")
        if denied:
            return denied
        assignee = body.get("assignee_user_id")
        _conv, err = await _guard_conv(request, conv_id)
        if err:
            return err
        actor_id, actor_name = _actor(request)
        # The service applies ``filter.conversation.before_assign`` (None aborts),
        # writes the assignee, and emits conversation.assigned (set) OR
        # conversation.unassigned (cleared) + the assigned/unassigned notice, once.
        conv = await conv_svc.assign(deps, _conv, assignee,
                                     actor_id=actor_id, actor_name=actor_name)
        if conv == "blocked":
            return _err("Atribuição bloqueada por um plugin.", status=403)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.post("/api/atendimentos/{conv_id}/assign-me")
    async def assign_me(conv_id: int, request: Request):
        denied = permission_denied(request, "conversation.assign")
        if denied:
            return denied
        user = current_user(request)
        if not user:
            return _err("Precisa estar autenticado para assumir a conversa.", status=401)
        _conv, err = await _guard_conv(request, conv_id)
        if err:
            return err
        _aid, actor_name = _actor(request)
        conv = await conv_svc.assign_me(deps, _conv, user["id"], actor_name=actor_name)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.post("/api/atendimentos/{conv_id}/agent")
    async def set_agent(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        agent_key = body.get("agent_key") or None
        _conv, err = await _guard_conv(request, conv_id)
        if err:
            return err
        _aid, actor_name = _actor(request)
        conv = await conv_svc.set_agent(deps, _conv, agent_key, actor_name=actor_name)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.post("/api/atendimentos/{conv_id}/assign-agent")
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
        _conv, err = await _guard_conv(request, conv_id)
        if err:
            return err
        kind = (body.get("kind") or "").strip()
        uid = None
        agent_key = None
        if kind == "user":
            uid = body.get("user_id")
            if not isinstance(uid, int):
                return _err("user_id é obrigatório para kind=user.", status=400)
        elif kind == "ai":
            agent_key = (body.get("agent_key") or "").strip()
            if not agent_key:
                return _err("agent_key é obrigatório para kind=ai.", status=400)
        elif kind != "none":
            return _err("kind deve ser 'user', 'ai' ou 'none'.", status=400)
        actor_id, actor_name = _actor(request)
        # The service converges the three ownership transitions (user/ai/none) through
        # its unified _transfer policy: it writes the assignee/agent/ai columns, mirrors
        # the contact-level AI gate (+ contact.ai_toggled) for user/ai, and emits
        # conversation.assigned + the matching notice (assigned/assigned_me/unassigned/
        # agent_changed), each exactly once.
        conv = await conv_svc.assign_unified(
            deps, _conv, kind=kind, user_id=uid, agent_key=agent_key,
            actor_id=actor_id, actor_name=actor_name)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.post("/api/atendimentos/{conv_id}/ai")
    async def set_ai(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        active = 1 if body.get("active") else 0
        _conv, err = await _guard_conv(request, conv_id)
        if err:
            return err
        actor_id, actor_name = _actor(request)
        # The service owns the per-conversation AI transfer POLICY (plano 17, unified
        # via _transfer): OFF hands the chat to whoever turned it off (actor_id, else
        # unassigned) and clears the agent; ON re-binds the inbox's default AI agent
        # and clears the human assignee. It emits conversation.assigned +
        # conversation.ai_toggled + the ai_on/ai_off (and assigned_me) notice, once each.
        conv = await conv_svc.set_ai(deps, _conv, active,
                                     actor_id=actor_id, actor_name=actor_name)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.post("/api/atendimentos/{conv_id}/unread")
    async def conv_mark_unread(conv_id: int, request: Request):
        """Mark ONE conversation as unread (plano 49 — per-conversa).

        Diferente de ``POST /api/contacts/{phone}/unread`` (contato-nível, acende TODAS
        as conversas do número): aqui a não-lida é escopada à conversa via
        ``conversation_repo.mark_conversation_unread``."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        _conv, err = await _guard_conv(request, conv_id)
        if err:
            return err
        marked = await asyncio.to_thread(
            conversation_repo.mark_conversation_unread, conv_id)
        return _ok({"marked": marked, "message": "Marcado como não lida."})

    @app.post("/api/atendimentos/{conv_id}/read")
    async def conv_mark_read(conv_id: int, request: Request):
        """Mark ONE conversation as read (plano 49 — per-conversa).

        Espelha o mark-read de abrir a conversa (``mark_conversation_read`` +
        recibos pelo canal da conversa), sem carregar as mensagens."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if not conv or _inbox_hidden(request, conv.get("inbox_id")):
            return _err("Conversa não encontrada.", status=404)
        msg_ids = await asyncio.to_thread(
            conversation_repo.mark_conversation_read, conv_id)
        if msg_ids:
            channel_id = conv.get("channel_id") or "default"
            phone = conv.get("contact_phone") or ""
            asyncio.create_task(_send_conv_read_receipts(channel_id, phone, msg_ids))
        return _ok({"read": len(msg_ids), "message": "Marcado como lido."})

    @app.delete("/api/atendimentos/{conv_id}")
    async def delete_conversation(conv_id: int, request: Request):
        """Hard-delete a single conversation/thread (plano 16, ação A).

        Removes only this conversation + its messages — the contact and its other
        conversations are kept. Gated by ``conversation.delete`` (plano 24: split
        from ``conversation.resolve`` so an atendente can resolve without hard-
        deleting). No lifecycle card is emitted (the thread it would land in is
        destroyed)."""
        denied = permission_denied(request, "conversation.delete")
        if denied:
            return denied
        # Read the enriched row BEFORE deleting — afterwards the payload (phone,
        # inbox_id, contact_id) is gone. ``get_with_channel`` exposes the phone as
        # ``contact_phone`` (label in _enriched_columns), NOT ``phone``.
        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        if _inbox_hidden(request, conv.get("inbox_id")):
            return _err("Conversa não encontrada.", status=404)
        try:
            deps.agent_handler.drop_cached_contact(conv.get("contact_phone"))
        except Exception:
            pass
        # The service deletes the row and emits conversation.deleted once. The
        # cached-contact drop stays in the route (it touches agent_handler state).
        await conv_svc.delete(deps, conv)
        return _ok({"message": "Conversa apagada.", "conversation_id": conv_id,
                    "contact_id": conv.get("contact_id")})

    @app.post("/api/atendimentos/{conv_id}/archive")
    async def archive(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.resolve")
        if denied:
            return denied
        archived = 1 if body.get("archived") else 0
        _conv, err = await _guard_conv(request, conv_id)
        if err:
            return err
        _aid, actor_name = _actor(request)
        conv = await conv_svc.archive(deps, _conv, archived, actor_name=actor_name)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})

    @app.put("/api/atendimentos/{conv_id}/info")
    async def update_info(conv_id: int, body: dict, request: Request):
        """Update conversation custom_attributes (FF5). Validates keys against the
        conversation attribute definitions; unknown keys are rejected."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        conv = await asyncio.to_thread(conversation_repo.get, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        if _inbox_hidden(request, conv.get("inbox_id")):
            return _err("Conversa não encontrada.", status=404)
        attrs = body.get("custom_attributes")
        changed: dict = {}
        defs: dict = {}
        merged: dict | None = None  # None = no custom_attributes in body → no write
        if attrs is not None:
            if not isinstance(attrs, dict):
                return _err("custom_attributes deve ser um objeto.", status=400)
            all_defs = await asyncio.to_thread(
                custom_attribute_repo.list_definitions, "conversation", True)
            defs = {d["attribute_key"]: d for d in all_defs if d.get("deleted_at") is None}
            known_keys = {d["attribute_key"] for d in all_defs}
            # Validate keys (P50: unknown → error) AND values (type/regex parity
            # with the contact PUT /info), returning a clean 400 before writing. A
            # value orphaned by a deleted attribute (P49 keeps it) is tolerated —
            # left untouched — instead of blocking the save.
            valid: dict = {}
            for key, value in attrs.items():
                definition = defs.get(key)
                if definition is None:
                    if key in known_keys:
                        continue
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
        actor_id, actor_name = _actor(request)
        # The service persists ``merged`` (None ⇒ no write), broadcasts
        # conversation.updated, and emits the aggregated attribute_set notice + one
        # conversation.attribute_set bus event per changed key — each exactly once.
        conv = await conv_svc.set_attributes(
            deps, conv, merged, changed, defs,
            actor_id=actor_id, actor_name=actor_name)
        return _ok({"conversation": conv})

    @app.get("/api/atendimentos/{conv_id}/templates")
    async def conversation_templates(conv_id: int, request: Request):
        """Templates for the picker (Frente C). Channel-aware: resolves the
        conversation's channel and returns ``{supported, templates, can_create,
        can_delete}``. Lists templates of every status (the UI badges the status and
        only lets approved ones be sent). ``can_create``/``can_delete`` are the
        caller's RBAC capability flags so the UI shows/hides the manage actions.
        Non-template channels (GOWA) return ``supported=False`` with no provider call."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        channel_id = conv.get("channel_id") or "default"
        can_create = has_permission(request, "template.create")
        can_delete = has_permission(request, "template.delete")
        if not outbound.supports(channel_id, "templates"):
            return _ok({"supported": False, "templates": [],
                        "can_create": can_create, "can_delete": can_delete})
        templates = await asyncio.to_thread(outbound.list_templates, channel_id)
        return _ok({"supported": True, "templates": templates,
                    "can_create": can_create, "can_delete": can_delete})

    @app.post("/api/atendimentos/{conv_id}/send-template")
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
        if _inbox_hidden(request, conv.get("inbox_id")):
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
            _u = current_user(request)
            msg_data = await asyncio.to_thread(
                agent_handler.save_operator_message, phone, preview,
                status="operator", msg_id=msg_id, channel_id=channel_id,
                sent_by_user_id=(_u.get("id") if _u else None),
                sent_by_name=(_u.get("name") if _u else None))
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

    _TEMPLATE_CATEGORIES = {"UTILITY", "MARKETING", "AUTHENTICATION"}

    @app.post("/api/atendimentos/{conv_id}/templates")
    async def create_conversation_template(conv_id: int, body: dict, request: Request):
        """Create a template on the conversation's channel (gated ``template.create``).

        body: ``{name, category?, language?, body_text, header_text?, footer_text?,
        body_examples?, header_examples?}``. The provider assembles the Graph
        components (including the ``example`` arrays Meta requires for ``{{n}}``).
        The template is created ``PENDING`` until Meta reviews it."""
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

        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        channel_id = conv.get("channel_id") or "default"
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

    @app.delete("/api/atendimentos/{conv_id}/templates/{name}")
    async def delete_conversation_template(conv_id: int, name: str, request: Request):
        """Delete a template (all language versions) on the conversation's channel
        (gated ``template.delete``)."""
        denied = permission_denied(request, "template.delete")
        if denied:
            return denied
        name = (name or "").strip()
        if not name:
            return _err("name é obrigatório.", status=400)
        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", status=404)
        channel_id = conv.get("channel_id") or "default"
        if not outbound.supports(channel_id, "templates"):
            return _err("Este canal não suporta templates.", status=400)
        result = await asyncio.to_thread(outbound.delete_template, channel_id, name)
        if not result.get("ok"):
            return _err(f"Falha ao apagar template: {result.get('error')}", status=502)
        return _ok({"message": "Template apagado.", "name": name})

    @app.get("/api/contacts/{phone}/atendimento")
    async def contact_conversation(phone: str, request: Request,
                                   include_closed: bool = False,
                                   channel_id: str | None = None,
                                   conversation_id: int | None = None):
        """Resolve the conversation for a contact by phone — feeds the chat header
        (display_id, status, assignee, ai_active, custom_attributes).

        By default returns the active (open) thread. With ``include_closed`` it
        returns the latest conversation regardless of status, so the sidebar
        right-click menu can still show assignee/reopen on a resolved thread.

        Plano 37 (A12/P3): o mesmo número existe em vários canais. Quando o painel
        já sabe qual fio quer, manda ``conversation_id`` (direto) ou ``channel_id``
        (escopa a resolução ao inbox do canal, via as variantes ``*_inbox``). Sem
        nenhum dos dois, mantém o legado por-phone (a conversa de qualquer canal).
        """
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        contact = await asyncio.to_thread(contact_repo.get_by_phone, phone)
        if not contact:
            return _err("Contato não encontrado.", status=404)
        # Painel já sabe o fio: resolve direto (validando posse do contato).
        if conversation_id:
            conv = await asyncio.to_thread(conversation_repo.get, int(conversation_id))
            if not conv or conv.get("contact_id") != contact["id"]:
                conv = None
            if conv and _inbox_hidden(request, conv.get("inbox_id")):
                return _err("Conversa não encontrada.", status=404)
            return _ok({"conversation": conv})
        # Escopa por canal quando informado (multicanal); senão, legado por-phone.
        inbox_id = None
        if channel_id:
            from db.repositories import inbox_repo
            inbox = await asyncio.to_thread(inbox_repo.get_by_channel, channel_id)
            inbox_id = inbox["id"] if inbox else None
        if inbox_id is not None:
            if include_closed:
                conv = await asyncio.to_thread(
                    conversation_repo.get_latest_for_contact_inbox, contact["id"], inbox_id)
            else:
                conv = await asyncio.to_thread(
                    conversation_repo.get_open_for_contact_inbox, contact["id"], inbox_id)
                if not conv:
                    conv = await asyncio.to_thread(
                        conversation_repo.get_latest_for_contact_inbox, contact["id"], inbox_id)
        elif include_closed:
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
        if conv and _inbox_hidden(request, conv.get("inbox_id")):
            return _err("Conversa não encontrada.", status=404)
        return _ok({"conversation": conv})
