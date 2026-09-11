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

from fastapi import Depends, Request

from db.repositories import conversation_label_repo as label_repo, conversation_repo
from plugins.events import emit_with_filter
from server.authz import permission_denied, current_user
from server.deps import require_permission, install_exception_handlers
from server.helpers import _ok, _err
from server.pagination import CAP_LIST

logger = logging.getLogger(__name__)

_MAX_NAME = 40


def register_routes(app, deps):
    ws_manager = deps.ws_manager

    # A escrita das etiquetas DA CONVERSA (+ WS + bus + cards no fio) mora no
    # serviço, compartilhada com a fachada /api/v1. Import diferido pelo mesmo
    # motivo das fases B3/B4 (o serviço importa de volta em ``server``).
    from app.services import conversation_service as conv_svc

    install_exception_handlers(app)

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

    @app.post("/api/conversation-labels",
              dependencies=[Depends(require_permission("conversation_label.manage"))])
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

    @app.put("/api/conversation-labels/{label_id}",
             dependencies=[Depends(require_permission("conversation_label.manage"))])
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

    @app.delete("/api/conversation-labels/{label_id}",
                dependencies=[Depends(require_permission("conversation_label.manage"))])
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

    @app.post("/api/atendimentos/labels-batch")
    async def get_conversation_labels_batch(body: dict, request: Request):
        """Etiquetas de VÁRIAS conversas em UMA request (plano 50 F13). Substitui o
        fan-out de 1 GET por atendimento no modo etiqueta do Kanban. Body: ``{ids:[...]}``
        → ``{labels_by_conv: {id: [label,...]}}``. Cap defensivo no nº de ids."""
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        raw = body.get("ids") or []
        if not isinstance(raw, list):
            return _err("ids deve ser uma lista.")
        ids = []
        for x in raw[:CAP_LIST * 5]:  # cap defensivo (F12): no máximo 1000 ids
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        by_conv = await asyncio.to_thread(label_repo.get_for_conversations, ids)
        return _ok({"labels_by_conv": by_conv})

    @app.put("/api/atendimentos/{conv_id}/labels")
    async def set_conversation_labels(conv_id: int, body: dict, request: Request):
        """Substitui as etiquetas da conversa.

        A escrita + os três efeitos (broadcast ``conversation_labels_changed``,
        evento ``conversation.labeled`` e os cards no fio) vivem em
        ``conversation_service.apply_labels`` desde o plano de API, para que a
        fachada ``/api/v1`` produza EXATAMENTE as mesmas consequências."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        names_in = body.get("labels", [])
        if not isinstance(names_in, list):
            return _err("labels deve ser uma lista.")
        conv = await asyncio.to_thread(conversation_repo.get, conv_id)
        if not conv:
            return _err("Conversa não encontrada.", 404)
        actor = (current_user(request) or {}).get("name") or None
        result_names = await conv_svc.apply_labels(
            deps, conv, names_in, actor_name=actor)
        return _ok({"conversation_id": conv_id, "labels": result_names})
