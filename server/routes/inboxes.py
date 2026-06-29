"""Inbox endpoints (plano 01 / plano 06 binding).

Minimal management surface: list inboxes and bind a default AI agent to an inbox
(``inboxes.default_agent_key``), which ``agent_factory.resolve_active_agent_key``
honours in the precedence conversa → inbox → default. Gated by ``inbox.manage``.
"""

import asyncio

from fastapi import Request

from db.repositories import inbox_repo, agent_repo
from server.authz import permission_denied
from server.helpers import _ok, _err


def register_routes(app, deps):

    @app.get("/api/inboxes")
    async def list_inboxes(request: Request):
        denied = permission_denied(request, "inbox.manage")
        if denied:
            return denied
        # Só caixas vivas (canal existente, não arquivado), nomeadas pelo
        # display_name atual do canal (plano inboxes/canais §4.6).
        rows = await asyncio.to_thread(inbox_repo.list_with_channel)
        return _ok(rows)

    @app.put("/api/inboxes/{inbox_id}/default-agent")
    async def set_default_agent(inbox_id: int, body: dict, request: Request):
        denied = permission_denied(request, "inbox.manage")
        if denied:
            return denied
        if await asyncio.to_thread(inbox_repo.get, inbox_id) is None:
            return _err("Inbox não encontrada.", status=404)
        agent_key = body.get("agent_key") or None
        if agent_key is not None:
            agent = await asyncio.to_thread(agent_repo.get, agent_key)
            if not agent:
                return _err(f"Agente '{agent_key}' não existe.", status=400)
        row = await asyncio.to_thread(inbox_repo.update, inbox_id, default_agent_key=agent_key)
        return _ok(row)
