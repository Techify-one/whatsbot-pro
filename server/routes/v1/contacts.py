"""``/api/v1/contacts`` — contatos + busca (domínio 1 de D6).

Delega a ``contact_repo`` + ``db.search.contact_search`` (a MESMA busca trigram
da barra lateral) e, na escrita, a ``app.services.contact_service`` — o serviço
que o handler do painel também usa. Nenhuma regra é reimplementada aqui.

Gates: ``contact.read`` / ``contact.write`` / ``contact.delete``. Nada de chave
nova no catálogo (D5).
"""

from __future__ import annotations

import asyncio

from fastapi import Depends, Request

from db.repositories import contact_repo
from server.routes.v1._common import (V1_PREFIX, V1Error, contact_dto, not_found,
                                      page_params, require, visible_inboxes)


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager
    from app.services import contact_service as contact_svc

    @app.get(f"{V1_PREFIX}/contacts", tags=["contacts"],
             summary="Listar/pesquisar contatos",
             dependencies=[Depends(require("contact.read"))])
    async def list_contacts(request: Request, q: str = "", archived: bool = False,
                            limit: int | None = None, offset: int = 0,
                            sort: str = "recency"):
        """Página de contatos. ``q`` pesquisa nome/telefone/atributos **e** conteúdo
        de mensagem (trigram/unaccent, o mesmo motor da busca do painel).

        O resultado é escopado pela membresia de inbox do dono da chave — uma
        integração de um cliente específico só enxerga o canal dele.
        """
        lim, off = page_params(limit, offset)
        page = await asyncio.to_thread(
            contact_repo.list_contacts_page, q, archived, visible_inboxes(request),
            limit=lim, offset=off, sort=sort)
        return {"items": [contact_dto(r) for r in page["items"]],
                "total": page["total"], "limit": lim, "offset": off,
                "has_more": page["has_more"]}

    @app.get(f"{V1_PREFIX}/contacts/{{phone}}", tags=["contacts"],
             summary="Obter um contato",
             dependencies=[Depends(require("contact.read"))])
    async def get_contact(phone: str, request: Request):
        row = await asyncio.to_thread(contact_repo.get_full_contact, phone)
        if row is None:
            raise not_found("Contato não encontrado.")
        # Escopo de DADOS: um usuário escopado por inbox não lê contato de fora dela.
        hidden = await asyncio.to_thread(
            contact_repo.contact_hidden_by_inbox_scope, row["id"], visible_inboxes(request))
        if hidden:
            raise not_found("Contato não encontrado.")
        return contact_dto(row)

    @app.post(f"{V1_PREFIX}/contacts", status_code=201, tags=["contacts"],
              summary="Criar contato",
              dependencies=[Depends(require("contact.write"))])
    async def create_contact(body: dict, request: Request):
        """Cria (ou devolve) o contato do telefone informado e aplica os campos.

        Idempotente por telefone: um POST para um telefone já existente atualiza
        os campos enviados em vez de duplicar — o telefone é a identidade do
        contato no core.
        """
        phone = (body.get("phone") or "").strip()
        if not phone:
            raise V1Error("Campo 'phone' é obrigatório.", code="missing_field")
        existing = await asyncio.to_thread(contact_repo.get_by_phone, phone)
        # ``_get_contact`` materializa a linha pelo mesmo caminho do painel
        # (respeitando o seed de IA por canal), sem duplicar regra aqui.
        await asyncio.to_thread(agent_handler._get_contact, phone)
        info, err = await contact_svc.update_info(agent_handler, phone, body)
        if err:
            raise V1Error(err, code="invalid_attribute")
        row = await asyncio.to_thread(contact_repo.get_full_contact, phone)
        dto = contact_dto(row)
        dto["created"] = existing is None
        return dto

    @app.patch(f"{V1_PREFIX}/contacts/{{phone}}", tags=["contacts"],
               summary="Editar contato",
               dependencies=[Depends(require("contact.write"))])
    async def update_contact(phone: str, body: dict, request: Request):
        """Edição explícita: campo escalar presente no corpo SUBSTITUI (string
        vazia limpa); ausente fica intocado; atributo enviado como ``null`` é
        removido. Mesma semântica do painel — é a mesma função."""
        if await asyncio.to_thread(contact_repo.get_by_phone, phone) is None:
            raise not_found("Contato não encontrado.")
        info, err = await contact_svc.update_info(agent_handler, phone, body)
        if err:
            raise V1Error(err, code="invalid_attribute")
        row = await asyncio.to_thread(contact_repo.get_full_contact, phone)
        return contact_dto(row)

    @app.delete(f"{V1_PREFIX}/contacts/{{phone}}", tags=["contacts"],
                summary="Excluir contato (e todas as conversas)",
                dependencies=[Depends(require("contact.delete"))])
    async def delete_contact(phone: str, request: Request):
        if not await contact_svc.delete_contact(agent_handler, ws_manager, phone):
            raise not_found("Contato não encontrado.")
        return {"deleted": True, "phone": phone}

    @app.put(f"{V1_PREFIX}/contacts/{{phone}}/tags", tags=["contacts"],
             summary="Definir as etiquetas de um contato",
             dependencies=[Depends(require("contact.write"))])
    async def set_contact_tags(phone: str, body: dict, request: Request):
        """Substitui a lista de etiquetas do contato. Passa pelo mesmo
        ``filter.contact.tags`` do painel (um plugin pode reescrever ou abortar)."""
        import time
        from plugins.events import apply_filter, emit_with_filter

        tags = body.get("tags")
        if not isinstance(tags, list):
            raise V1Error("Campo 'tags' deve ser uma lista.", code="invalid_field")
        contact = await asyncio.to_thread(contact_repo.get_by_phone, phone)
        if contact is None:
            raise not_found("Contato não encontrado.")
        previous = await asyncio.to_thread(
            lambda: list(agent_handler._get_contact(phone).tags))
        new_tags = await apply_filter(
            "filter.contact.tags", list(tags),
            {"phone": phone, "previous_tags": list(previous)})
        if new_tags is None:
            return {"phone": phone, "tags": previous, "applied": False}

        def _update():
            c = agent_handler._get_contact(phone)
            c.set_tags(new_tags)
            return c.tags

        result = await asyncio.to_thread(_update)
        await ws_manager.broadcast("contact_tags_updated",
                                   {"phone": phone, "tags": result})
        await emit_with_filter("contact.tagged", {
            "phone": phone, "tags": list(result), "ts": time.time()})
        for removed in set(previous) - set(result):
            await emit_with_filter("contact.untagged", {
                "phone": phone, "tag": removed, "ts": time.time()})
        return {"phone": phone, "tags": result, "applied": True}
