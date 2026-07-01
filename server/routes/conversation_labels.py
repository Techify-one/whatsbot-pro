"""Conversation labels (etiquetas próprias da conversa, Onda 3).

Registro global (``conversation_labels``) + atribuição N:N por conversa
(``conversation_label_links``). Separadas das tags de contato — decisão de
produto: a etiqueta pertence à CONVERSA, não ao contato (estilo Chatwoot).

Endpoints:
  GET/POST/PUT/DELETE  /api/conversation-labels[/{id}]   — registro global
  GET/PUT              /api/conversations/{id}/labels      — etiquetas da conversa
"""

import asyncio
import logging
import time

from fastapi import Request

from db.repositories import conversation_label_repo as label_repo, conversation_repo
from plugins.events import emit_with_filter
from server import system_notices
from server.authz import permission_denied, current_user
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

_MAX_NAME = 40


def register_routes(app, deps):
    ws_manager = deps.ws_manager

    async def _broadcast_registry():
        """Push the updated label registry so open editors refresh their palette."""
        try:
            rows = await asyncio.to_thread(label_repo.get_all)
            await ws_manager.broadcast("conversation_labels_registry_changed", {"labels": rows})
        except Exception as e:  # noqa: BLE001
            logger.debug("conversation labels registry broadcast failed: %s", e)

    # ── Registro global ───────────────────────────────────────────────
    @app.get("/api/conversation-labels")
    async def list_conversation_labels():
        rows = await asyncio.to_thread(label_repo.get_all)
        return _ok(rows)

    @app.post("/api/conversation-labels")
    async def create_conversation_label(request: Request):
        body = await request.json()
        name = (body.get("name") or "").strip()
        color = (body.get("color") or "#6b7280").strip()
        if not name:
            return _err("Nome da etiqueta é obrigatório.")
        if len(name) > _MAX_NAME:
            return _err(f"Nome da etiqueta deve ter no máximo {_MAX_NAME} caracteres.")
        row = await asyncio.to_thread(label_repo.create, name, color)
        if row is None:
            return _err(f"Etiqueta '{name}' já existe.")
        await _broadcast_registry()
        await emit_with_filter("conversation_label.created", {
            "id": row["id"], "name": name, "color": color, "ts": time.time()})
        return _ok(row)

    @app.put("/api/conversation-labels/{label_id}")
    async def update_conversation_label(label_id: int, request: Request):
        body = await request.json()
        name = (body.get("name") or "").strip() or None
        color = (body.get("color") or "").strip() or None
        if name and len(name) > _MAX_NAME:
            return _err(f"Nome da etiqueta deve ter no máximo {_MAX_NAME} caracteres.")
        if name:
            other = await asyncio.to_thread(label_repo.get_by_name, name)
            if other and other["id"] != label_id:
                return _err(f"Etiqueta '{name}' já existe.")
        row = await asyncio.to_thread(label_repo.update, label_id, name=name, color=color)
        if row is None:
            return _err("Etiqueta não encontrada.", 404)
        await _broadcast_registry()
        await emit_with_filter("conversation_label.updated", {
            "id": label_id, "name": row["name"], "color": row["color"], "ts": time.time()})
        return _ok(row)

    @app.delete("/api/conversation-labels/{label_id}")
    async def delete_conversation_label(label_id: int):
        ok = await asyncio.to_thread(label_repo.delete, label_id)
        if not ok:
            return _err("Etiqueta não encontrada.", 404)
        await _broadcast_registry()
        await emit_with_filter("conversation_label.deleted", {"id": label_id, "ts": time.time()})
        return _ok({"deleted": label_id})

    # ── Etiquetas de uma conversa ─────────────────────────────────────
    @app.get("/api/atendimentos/{conv_id}/labels")
    async def get_conversation_labels(conv_id: int, request: Request):
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        rows = await asyncio.to_thread(label_repo.get_for_conversation, conv_id)
        return _ok({"conversation_id": conv_id, "labels": rows})

    @app.put("/api/atendimentos/{conv_id}/labels")
    async def set_conversation_labels(conv_id: int, body: dict, request: Request):
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        names_in = body.get("labels", [])
        if not isinstance(names_in, list):
            return _err("labels deve ser uma lista.")
        conv = await asyncio.to_thread(conversation_repo.get, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", 404)

        previous = await asyncio.to_thread(label_repo.get_names_for_conversation, conv_id)
        result = await asyncio.to_thread(
            label_repo.set_for_conversation, conv_id, [str(x) for x in names_in])
        result_names = [r["name"] for r in result]

        try:
            await ws_manager.broadcast("conversation_labels_changed", {
                "conversation_id": conv_id, "contact_id": conv.get("contact_id"),
                "labels": result_names, "ts": time.time()})
        except Exception as e:  # noqa: BLE001
            logger.debug("conversation_labels_changed broadcast failed: %s", e)
        await emit_with_filter("conversation.labeled", {
            "conversation_id": conv_id, "contact_id": conv.get("contact_id"),
            "labels": result_names, "ts": time.time()})

        # Chat notices (grupo `conv_labels`): one card per added/removed label.
        actor = (current_user(request) or {}).get("name") or None
        prev_set, now_set = set(previous), set(result_names)
        for name in (now_set - prev_set):
            await asyncio.to_thread(
                system_notices.emit_conversation_notice,
                event_type="conv_label_added", conversation_id=conv_id,
                contact_id=conv.get("contact_id"), actor=actor, label=name)
        for name in (prev_set - now_set):
            await asyncio.to_thread(
                system_notices.emit_conversation_notice,
                event_type="conv_label_removed", conversation_id=conv_id,
                contact_id=conv.get("contact_id"), actor=actor, label=name)
        return _ok({"conversation_id": conv_id, "labels": result_names})
