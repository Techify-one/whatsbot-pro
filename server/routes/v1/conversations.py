"""``/api/v1/conversations`` — conversas, filtros e ciclo de vida (domínio 3 de D6).

Delega ao motor de filtros (``db.filters``) e a ``app.services.conversation_service``
— o mesmo serviço que as rotas do painel usam, então ``filter.conversation.before_status``
/ ``before_assign``, a política de fechamento (limpar atendente e agente), o
``abort_ai_cycle`` e os avisos de sistema no fio acontecem exatamente uma vez, do
mesmo jeito.

``GET /filter-schema`` reexpõe ``conv_filters.available_dimensions``: o motor de
filtros **já se autodescreve**, então o integrador descobre as dimensões sem
documentação escrita à mão.

Gates: ``conversation.read`` / ``.resolve`` / ``.assign`` / ``.reply``.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import Depends, Request

from db import filters as conv_filters
from db.filters.translate import FilterContext
from db.repositories import conversation_repo, custom_attribute_repo
from server.routes.v1._common import (V1_PREFIX, V1Error, conversation_dto, forbidden,
                                      not_found, page_params, require, visible_inboxes)


def _filter_context(request: Request) -> FilterContext:
    user = getattr(request.state, "user", None)
    cattr_keys = frozenset(
        d["attribute_key"] for d in custom_attribute_repo.list_filterable("conversation"))
    contact_cattr_keys = frozenset(
        d["attribute_key"] for d in custom_attribute_repo.list_filterable("contact"))
    return FilterContext(
        user_id=(user or {}).get("id"), now=time.time(), cattr_keys=cattr_keys,
        contact_cattr_keys=contact_cattr_keys)


def register_routes(app, deps):
    from app.services import conversation_service as conv_svc

    async def _load(request: Request, conv_id: int) -> dict:
        """Conversa + escopo de caixa. 404 (não 403) quando fora do escopo — a
        existência de uma conversa de outra caixa não deve vazar."""
        conv = await asyncio.to_thread(conversation_repo.get, conv_id)
        if conv is None:
            raise not_found("Conversa não encontrada.")
        vis = visible_inboxes(request)
        if vis is not None and conv.get("inbox_id") not in vis:
            raise not_found("Conversa não encontrada.")
        return conv

    def _actor(request: Request):
        user = getattr(request.state, "user", None)
        return (user or {}).get("id"), ((user or {}).get("name") or None)

    @app.get(f"{V1_PREFIX}/conversations", tags=["conversations"],
             summary="Listar conversas",
             dependencies=[Depends(require("conversation.read"))])
    async def list_conversations(request: Request, status: str | None = None,
                                 inbox_id: int | None = None,
                                 assignee_user_id: int | None = None,
                                 archived: bool = False,
                                 limit: int | None = None, offset: int = 0):
        """Listagem simples. Para consulta rica use ``POST /conversations/filter``."""
        lim, off = page_params(limit, offset, default=50, cap=200)
        user = getattr(request.state, "user", None)
        rows = await asyncio.to_thread(
            conversation_repo.list_conversations,
            status=status, inbox_id=inbox_id, assignee_user_id=assignee_user_id,
            is_archived=1 if archived else 0,
            inbox_ids=visible_inboxes(request),
            current_user_id=(user.get("id") if user else None),
            limit=lim, offset=off)
        return {"items": [conversation_dto(r) for r in rows],
                "limit": lim, "offset": off, "has_more": len(rows) >= lim}

    @app.get(f"{V1_PREFIX}/conversations/filter-schema", tags=["conversations"],
             summary="Dimensões disponíveis para o filtro",
             dependencies=[Depends(require("conversation.read"))])
    async def filter_schema(request: Request):
        """O motor de filtros se autodescreve — inclusive os atributos
        personalizados que ESTA instalação definiu. Consulte aqui antes de montar
        um ``POST /conversations/filter``."""
        defs = await asyncio.to_thread(
            custom_attribute_repo.list_filterable, "conversation")
        return {"dimensions": conv_filters.available_dimensions(defs)}

    @app.post(f"{V1_PREFIX}/conversations/filter", tags=["conversations"],
              summary="Filtrar conversas (motor completo)",
              dependencies=[Depends(require("conversation.read"))])
    async def filter_conversations(body: dict, request: Request):
        """Corpo: ``{"filters": [{"key","operator","values"}...], "limit", "offset"}``.

        As chaves e operadores válidos vêm de ``GET /conversations/filter-schema``.
        Entrada malformada é **400**, nunca 500.
        """
        from db.filters.spec import from_payload
        try:
            spec = from_payload(body or {})
            ctx = _filter_context(request)
            where = await asyncio.to_thread(conv_filters.build_where, spec, ctx)
        except conv_filters.FilterError as e:
            raise V1Error(str(e), code="invalid_filter")
        except (TypeError, ValueError, IndexError, KeyError):
            raise V1Error("Filtro inválido.", code="invalid_filter")
        user = getattr(request.state, "user", None)
        rows = await asyncio.to_thread(
            conversation_repo.list_filtered, where,
            inbox_ids=visible_inboxes(request),
            current_user_id=(user.get("id") if user else None),
            limit=spec.limit + 1, offset=spec.offset)
        has_more = len(rows) > spec.limit
        if has_more:
            rows = rows[:spec.limit]
        return {"items": [conversation_dto(r) for r in rows],
                "count": len(rows), "limit": spec.limit,
                "offset": spec.offset, "has_more": has_more}

    @app.post(f"{V1_PREFIX}/conversations/count", tags=["conversations"],
              summary="Contagens por aba, com o mesmo filtro",
              dependencies=[Depends(require("conversation.read"))])
    async def count_conversations(body: dict, request: Request):
        from db.filters.spec import from_payload
        try:
            spec = from_payload(body or {})
            ctx = _filter_context(request)
            where = await asyncio.to_thread(conv_filters.build_where, spec, ctx)
        except conv_filters.FilterError as e:
            raise V1Error(str(e), code="invalid_filter")
        except (TypeError, ValueError, IndexError, KeyError):
            raise V1Error("Filtro inválido.", code="invalid_filter")
        user = getattr(request.state, "user", None)
        return await asyncio.to_thread(
            conversation_repo.count_tab_counts, where,
            inbox_ids=visible_inboxes(request),
            current_user_id=(user.get("id") if user else None))

    @app.get(f"{V1_PREFIX}/conversations/{{conv_id}}", tags=["conversations"],
             summary="Obter uma conversa",
             dependencies=[Depends(require("conversation.read"))])
    async def get_conversation(conv_id: int, request: Request):
        await _load(request, conv_id)
        user = getattr(request.state, "user", None)
        conv = await asyncio.to_thread(
            conversation_repo.get_with_channel, conv_id,
            (user.get("id") if user else None))
        return conversation_dto(conv)

    @app.post(f"{V1_PREFIX}/conversations/{{conv_id}}/status", tags=["conversations"],
              summary="Resolver ou reabrir",
              dependencies=[Depends(require("conversation.resolve"))])
    async def set_status(conv_id: int, body: dict, request: Request):
        """``{"status": "open"|"closed"}``. Um plugin pode RECUSAR o fechamento
        (``filter.conversation.before_status``) → **403**."""
        status = (body.get("status") or "").strip()
        if status not in ("open", "closed"):
            raise V1Error("status deve ser 'open' ou 'closed'.", code="invalid_field")
        conv = await _load(request, conv_id)
        actor_id, actor_name = _actor(request)
        result = await conv_svc.set_status(deps, conv, status,
                                           actor_id=actor_id, actor_name=actor_name)
        if result == "blocked":
            raise forbidden("Fechamento bloqueado por um plugin.")
        if not result:
            raise not_found("Conversa não encontrada.")
        return conversation_dto(result)

    @app.post(f"{V1_PREFIX}/conversations/{{conv_id}}/assign", tags=["conversations"],
              summary="Atribuir/desatribuir a um atendente",
              dependencies=[Depends(require("conversation.assign"))])
    async def assign(conv_id: int, body: dict, request: Request):
        """``{"assignee_user_id": <id>|null}``. ``null`` desatribui (e, por
        contrato do core, NÃO devolve a conversa para a IA)."""
        conv = await _load(request, conv_id)
        actor_id, actor_name = _actor(request)
        result = await conv_svc.assign(deps, conv, body.get("assignee_user_id"),
                                       actor_id=actor_id, actor_name=actor_name)
        if result == "blocked":
            raise forbidden("Atribuição bloqueada por um plugin.")
        if not result:
            raise not_found("Conversa não encontrada.")
        return conversation_dto(result)

    @app.post(f"{V1_PREFIX}/conversations/{{conv_id}}/ai", tags=["conversations"],
              summary="Ligar/desligar a IA da conversa",
              dependencies=[Depends(require("conversation.reply"))])
    async def set_ai(conv_id: int, body: dict, request: Request):
        """``{"active": true|false}``. Desligar entrega a conversa a quem
        desligou e limpa o agente; ligar re-vincula o agente padrão da caixa e
        limpa o atendente humano — a MESMA política do painel."""
        conv = await _load(request, conv_id)
        actor_id, actor_name = _actor(request)
        result = await conv_svc.set_ai(deps, conv, 1 if body.get("active") else 0,
                                       actor_id=actor_id, actor_name=actor_name)
        if not result:
            raise not_found("Conversa não encontrada.")
        return conversation_dto(result)

    @app.put(f"{V1_PREFIX}/conversations/{{conv_id}}/labels", tags=["conversations"],
             summary="Definir as etiquetas de uma conversa",
             dependencies=[Depends(require("conversation.reply"))])
    async def set_labels(conv_id: int, body: dict, request: Request):
        """Substitui a lista de etiquetas da conversa.

        Mesmo gate (``conversation.reply``) e mesma função do painel
        (``conversation_service.apply_labels``), então o operador com o chat
        aberto vê a mudança, os plugins recebem ``conversation.labeled`` e o fio
        ganha o card de etiqueta — igualzinho.
        """
        labels = body.get("labels")
        if not isinstance(labels, list):
            raise V1Error("Campo 'labels' deve ser uma lista.", code="invalid_field")
        conv = await _load(request, conv_id)
        _aid, actor_name = _actor(request)
        names = await conv_svc.apply_labels(deps, conv, labels, actor_name=actor_name)
        return {"conversation_id": conv_id, "labels": names}
