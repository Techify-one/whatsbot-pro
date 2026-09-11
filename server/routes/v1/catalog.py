"""``/api/v1`` — catálogo: etiquetas, atributos personalizados, canais e inboxes
(domínio 4 de D6).

Etiquetas (de contato e de conversa) e definições de atributo personalizado têm
CRUD; **canais e inboxes são somente LEITURA** — administração de canal
(credenciais, conexão, QR) fica fora da v1 por decisão de escopo (D6), e continua
exclusiva do painel.

Gates: ``tag.manage`` / ``conversation_label.manage`` / ``custom_attribute.manage``
/ ``channel.manage``. Nenhuma chave nova (D5).
"""

from __future__ import annotations

import asyncio
import re
import time

from fastapi import Depends, Request

from db.repositories import (channel_repo, conversation_label_repo,
                             custom_attribute_repo as ca_repo, inbox_repo, tag_repo)
from db.repositories.custom_attribute_validate import VALID_TYPES
from plugins.events import emit_with_filter
from server.routes.v1._common import V1_PREFIX, V1Error, not_found, require

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_APPLIES = {"contact", "conversation"}
_MAX_LABEL_NAME = 40   # espelha server/routes/conversation_labels.py


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager

    async def _broadcast_label_registry() -> None:
        """Empurra o registro de etiquetas para os editores abertos.

        A rota do painel faz isto em TODA escrita do registro; sem ele, uma
        etiqueta criada/renomeada pela API só aparecia na paleta do operador
        depois de recarregar a tela.
        """
        try:
            rows = await asyncio.to_thread(conversation_label_repo.get_all)
            await ws_manager.broadcast("conversation_labels_registry_changed",
                                       {"labels": rows})
        except Exception:  # noqa: BLE001 — broadcast é best-effort
            pass

    # ── Etiquetas de CONTATO ────────────────────────────────────────────────

    @app.get(f"{V1_PREFIX}/tags", tags=["catalog"], summary="Listar etiquetas de contato")
    async def list_tags(request: Request):
        return {"items": [{"name": n, **v}
                          for n, v in (await asyncio.to_thread(tag_repo.get_all)).items()]}

    @app.post(f"{V1_PREFIX}/tags", status_code=201, tags=["catalog"],
              summary="Criar etiqueta de contato",
              dependencies=[Depends(require("tag.manage"))])
    async def create_tag(body: dict, request: Request):
        name = (body.get("name") or "").strip()
        color = (body.get("color") or "").strip()
        if not name:
            raise V1Error("Campo 'name' é obrigatório.", code="missing_field")
        if len(name) > 30:
            raise V1Error("Nome da etiqueta deve ter no máximo 30 caracteres.",
                          code="invalid_field")
        if not color:
            raise V1Error("Campo 'color' é obrigatório.", code="missing_field")
        if not await asyncio.to_thread(agent_handler.tag_registry.create, name, color):
            raise V1Error(f"Etiqueta '{name}' já existe.", status=409, code="duplicate")
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        await emit_with_filter("tag.created",
                               {"name": name, "color": color, "ts": time.time()})
        return {"name": name, "color": color}

    @app.patch(f"{V1_PREFIX}/tags/{{name}}", tags=["catalog"],
               summary="Renomear/recolorir etiqueta de contato",
               dependencies=[Depends(require("tag.manage"))])
    async def update_tag(name: str, body: dict, request: Request):
        new_name = (body.get("name") or "").strip() or None
        color = (body.get("color") or "").strip() or None
        if new_name and len(new_name) > 30:
            raise V1Error("Nome da etiqueta deve ter no máximo 30 caracteres.",
                          code="invalid_field")
        old = await asyncio.to_thread(agent_handler.tag_registry.get, name)
        if not await asyncio.to_thread(
                agent_handler.tag_registry.update, name, new_name=new_name, color=color):
            raise not_found(f"Etiqueta '{name}' não encontrada.")
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        final = new_name or name
        data = await asyncio.to_thread(agent_handler.tag_registry.get, final)
        await emit_with_filter("tag.updated", {
            "old_name": name, "name": final,
            "color": (data or {}).get("color") or color, "ts": time.time(),
            "_audit_before": {"name": name, "color": (old or {}).get("color")}})
        return {"name": final, "color": (data or {}).get("color") or color}

    @app.delete(f"{V1_PREFIX}/tags/{{name}}", tags=["catalog"],
                summary="Excluir etiqueta de contato",
                dependencies=[Depends(require("tag.manage"))])
    async def delete_tag(name: str, request: Request):
        old = await asyncio.to_thread(agent_handler.tag_registry.get, name)
        if not await asyncio.to_thread(agent_handler.tag_registry.delete, name):
            raise not_found(f"Etiqueta '{name}' não encontrada.")
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        await emit_with_filter("tag.deleted", {
            "name": name, "ts": time.time(),
            "_audit_before": {"name": name, "color": (old or {}).get("color")}})
        return {"deleted": name}

    # ── Etiquetas de CONVERSA ───────────────────────────────────────────────

    @app.get(f"{V1_PREFIX}/conversation-labels", tags=["catalog"],
             summary="Listar etiquetas de conversa")
    async def list_conv_labels(request: Request):
        return {"items": await asyncio.to_thread(conversation_label_repo.get_all)}

    @app.post(f"{V1_PREFIX}/conversation-labels", status_code=201, tags=["catalog"],
              summary="Criar etiqueta de conversa",
              dependencies=[Depends(require("conversation_label.manage"))])
    async def create_conv_label(body: dict, request: Request):
        name = (body.get("name") or "").strip()
        if not name:
            raise V1Error("Campo 'name' é obrigatório.", code="missing_field")
        row = await asyncio.to_thread(
            conversation_label_repo.create, name,
            (body.get("color") or "#6b7280"), int(body.get("position") or 0))
        if row is None:
            raise V1Error(f"Etiqueta '{name}' já existe.", status=409, code="duplicate")
        await _broadcast_label_registry()
        await emit_with_filter("conversation_label.created",
                               {"label": row, "ts": time.time()})
        return row

    @app.patch(f"{V1_PREFIX}/conversation-labels/{{label_id}}", tags=["catalog"],
               summary="Renomear/recolorir etiqueta de conversa",
               dependencies=[Depends(require("conversation_label.manage"))])
    async def update_conv_label(label_id: int, body: dict, request: Request):
        """Edição parcial: ``name`` e/ou ``color``; campo ausente fica intocado.

        A identidade da etiqueta é o ``id``, não o nome — renomear PRESERVA os
        vínculos com as conversas já etiquetadas (é essa a diferença para
        apagar-e-recriar).
        """
        name = (body.get("name") or "").strip() or None
        color = (body.get("color") or "").strip() or None
        if name and len(name) > _MAX_LABEL_NAME:
            raise V1Error(
                f"Nome da etiqueta deve ter no máximo {_MAX_LABEL_NAME} caracteres.",
                code="invalid_field")
        if name:
            other = await asyncio.to_thread(conversation_label_repo.get_by_name, name)
            if other and other["id"] != label_id:
                raise V1Error(f"Etiqueta '{name}' já existe.", status=409,
                              code="duplicate")
        before = await asyncio.to_thread(conversation_label_repo.get, label_id)
        if before is None:
            raise not_found("Etiqueta não encontrada.")
        row = await asyncio.to_thread(conversation_label_repo.update, label_id,
                                      name=name, color=color)
        if row is None:
            raise not_found("Etiqueta não encontrada.")
        await _broadcast_label_registry()
        await emit_with_filter("conversation_label.updated", {
            "id": label_id, "name": row["name"], "color": row["color"],
            "ts": time.time()})
        return row

    @app.delete(f"{V1_PREFIX}/conversation-labels/{{label_id}}", tags=["catalog"],
                summary="Excluir etiqueta de conversa",
                dependencies=[Depends(require("conversation_label.manage"))])
    async def delete_conv_label(label_id: int, request: Request):
        if not await asyncio.to_thread(conversation_label_repo.delete, label_id):
            raise not_found("Etiqueta não encontrada.")
        await _broadcast_label_registry()
        await emit_with_filter("conversation_label.deleted",
                               {"id": label_id, "ts": time.time()})
        return {"deleted": label_id}

    # ── Atributos personalizados (DEFINIÇÕES) ───────────────────────────────

    @app.get(f"{V1_PREFIX}/custom-attributes", tags=["catalog"],
             summary="Listar definições de atributo personalizado")
    async def list_attrs(request: Request, applies_to: str | None = None):
        if applies_to and applies_to not in _APPLIES:
            raise V1Error("applies_to deve ser 'contact' ou 'conversation'.",
                          code="invalid_field")
        return {"items": await asyncio.to_thread(ca_repo.list_definitions, applies_to)}

    @app.post(f"{V1_PREFIX}/custom-attributes", status_code=201, tags=["catalog"],
              summary="Criar definição de atributo personalizado",
              dependencies=[Depends(require("custom_attribute.manage"))])
    async def create_attr(body: dict, request: Request):
        key = (body.get("attribute_key") or "").strip().lower()
        display_name = (body.get("display_name") or "").strip()
        attr_type = (body.get("type") or "text").strip().lower()
        applies_to = (body.get("applies_to") or "contact").strip().lower()
        options = body.get("options")
        if not display_name:
            raise V1Error("Campo 'display_name' é obrigatório.", code="missing_field")
        if not _KEY_RE.match(key):
            raise V1Error("A chave deve ser snake_case (minúsculas, começando com letra).",
                          code="invalid_field")
        if attr_type not in VALID_TYPES:
            raise V1Error(f"Tipo inválido. Use um de: {', '.join(sorted(VALID_TYPES))}.",
                          code="invalid_field")
        if applies_to not in _APPLIES:
            raise V1Error("applies_to deve ser 'contact' ou 'conversation'.",
                          code="invalid_field")
        if attr_type == "list":
            if not isinstance(options, list) or not options:
                raise V1Error("Atributos do tipo 'list' precisam de uma lista de opções.",
                              code="invalid_field")
            options = [str(o).strip() for o in options if str(o).strip()]
        row = await asyncio.to_thread(
            ca_repo.create_definition,
            attribute_key=key, display_name=display_name, type=attr_type,
            applies_to=applies_to, options=options if attr_type == "list" else None,
            required=1 if body.get("required") else 0,
            description=(body.get("description") or "").strip(),
            regex_pattern=(body.get("regex_pattern") or None),
            regex_cue=(body.get("regex_cue") or None),
            position=int(body.get("position") or 0),
            filterable=1 if body.get("filterable") else 0)
        if row is None:
            raise V1Error(f"Já existe um atributo '{key}' para {applies_to}.",
                          status=409, code="duplicate")
        await emit_with_filter("custom_attribute.created",
                               {"definition": row, "ts": time.time()})
        return row

    @app.patch(f"{V1_PREFIX}/custom-attributes/{{def_id}}", tags=["catalog"],
               summary="Editar definição de atributo personalizado",
               dependencies=[Depends(require("custom_attribute.manage"))])
    async def update_attr(def_id: int, body: dict, request: Request):
        """Edição parcial da definição. Campo ausente fica intocado.

        ``attribute_key``, ``type`` e ``applies_to`` são a IDENTIDADE do atributo
        e não se editam por aqui: os valores já gravados nas entidades são
        indexados pela chave e tipados pelo tipo, então mudá-los renegaria o
        dado existente em vez de migrá-lo. Enviá-los é ignorado — exceto num
        atributo de sistema, onde tentar renomear é **400** explícito (mesma
        regra do painel).
        """
        existing = await asyncio.to_thread(ca_repo.get_definition, def_id)
        if existing is None or existing.get("deleted_at") is not None:
            raise not_found("Atributo não encontrado.")
        if existing.get("is_system"):
            for ident in ("attribute_key", "applies_to"):
                if ident in body and (body.get(ident) or "") != existing.get(ident):
                    raise V1Error("Atributos de sistema não podem ser renomeados.",
                                  code="system_attribute")
        fields: dict = {}
        if "display_name" in body:
            dn = (body.get("display_name") or "").strip()
            if not dn:
                raise V1Error("Campo 'display_name' não pode ficar vazio.",
                              code="invalid_field")
            fields["display_name"] = dn
        for f in ("description", "regex_pattern", "regex_cue"):
            if f in body:
                fields[f] = (body.get(f) or "")
        if "required" in body:
            fields["required"] = 1 if body.get("required") else 0
        if "position" in body:
            try:
                fields["position"] = int(body.get("position") or 0)
            except (TypeError, ValueError):
                raise V1Error("Campo 'position' deve ser um inteiro.",
                              code="invalid_field")
        if "filterable" in body:
            fields["filterable"] = 1 if body.get("filterable") else 0
        # ``options`` só faz sentido no tipo 'list' — e o tipo NÃO muda aqui,
        # então a checagem é contra o tipo JÁ gravado.
        if "options" in body and existing.get("type") == "list":
            opts = body.get("options")
            if not isinstance(opts, list) or not opts:
                raise V1Error("Atributos do tipo 'list' precisam de uma lista de opções.",
                              code="invalid_field")
            fields["options"] = [str(o).strip() for o in opts if str(o).strip()]

        row = await asyncio.to_thread(ca_repo.update_definition, def_id, **fields)
        if row is None:
            raise not_found("Atributo não encontrado.")
        await emit_with_filter("custom_attribute.updated",
                               {"definition": row, "ts": time.time()})
        return row

    @app.delete(f"{V1_PREFIX}/custom-attributes/{{def_id}}", tags=["catalog"],
                summary="Excluir definição de atributo personalizado",
                dependencies=[Depends(require("custom_attribute.manage"))])
    async def delete_attr(def_id: int, request: Request):
        existing = await asyncio.to_thread(ca_repo.get_definition, def_id)
        if existing is None or existing.get("deleted_at") is not None:
            raise not_found("Atributo não encontrado.")
        if existing.get("is_system"):
            raise V1Error("Atributos de sistema não podem ser apagados.",
                          code="system_attribute")
        if not await asyncio.to_thread(ca_repo.delete_definition, def_id):
            raise not_found("Atributo não encontrado.")
        await emit_with_filter("custom_attribute.deleted",
                               {"id": def_id, "ts": time.time()})
        return {"deleted": def_id}

    # ── Canais e inboxes (LEITURA — administração fica fora da v1, D6) ───────

    @app.get(f"{V1_PREFIX}/channels", tags=["catalog"],
             summary="Listar canais (leitura)",
             dependencies=[Depends(require("channel.manage"))])
    async def list_channels(request: Request, include_archived: bool = False):
        """Forma REDUZIDA de propósito: nenhuma credencial sai por aqui — nem
        mascarada. Quem precisa gerir canal usa o painel."""
        rows = await asyncio.to_thread(channel_repo.list_all, include_archived)
        return {"items": [{
            "id": r.get("id"), "provider": r.get("provider"),
            "display_name": r.get("display_name"),
            "enabled": bool(r.get("enabled")), "archived": bool(r.get("archived")),
            "own_phone": r.get("own_phone"),
            "connected": bool(r.get("connected")),
            "logged_in": bool(r.get("logged_in")),
        } for r in rows]}

    @app.get(f"{V1_PREFIX}/inboxes", tags=["catalog"],
             summary="Listar caixas de entrada (leitura)",
             dependencies=[Depends(require("channel.manage"))])
    async def list_inboxes(request: Request):
        rows = await asyncio.to_thread(inbox_repo.list_with_channel)
        return {"items": [{
            "id": r.get("id"), "name": r.get("name"),
            "channel_id": r.get("channel_id"),
            "channel_type": r.get("channel_type"),
            "provider": r.get("provider"),
        } for r in rows]}
