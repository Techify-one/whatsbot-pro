"""Escrita de dados de CONTATO como serviço (irmão de ``messaging_service``).

Existe pelo mesmo motivo de ``MessagingService.send_text`` (refactor R-txt): a
edição de contato do painel eram ~80 linhas DENTRO do handler
``PUT /api/contacts/{phone}/info``, e a fachada ``/api/v1`` precisa exatamente
das mesmas regras — validação de atributo personalizado com as duas tolerâncias
(atributo soft-deleted e chave herdada da migração Chatwoot), semântica de
REPLACE nos campos escalares, substituição integral das observações e o emit
``contact.updated``. Uma segunda implementação divergiria em silêncio: a API
gravaria atributo inexistente ou recusaria um contato legado que o painel aceita.

Nada aqui conhece FastAPI: a validação devolve ``(valid_partial, erro)`` e a rota
decide como renderizar o erro (envelope legado no painel, DTO na v1).
"""

from __future__ import annotations

import asyncio
import logging
import time

from db.repositories import contact_repo
from db.repositories import custom_attribute_repo as ca_repo
from db.repositories.custom_attribute_validate import validate_value
from db.tables import contacts as contacts_table
from plugins.events import emit_with_filter

logger = logging.getLogger(__name__)

_SCALAR_KEYS = ("name", "email", "profession", "company", "address")


def validate_custom_attributes(phone: str, custom_attrs) -> tuple[dict, str | None]:
    """``(valid_partial, erro)`` para o bloco ``custom_attributes`` de um contato.

    Regras preservadas do handler original:

    * chave DESCONHECIDA **e** não armazenada ⇒ erro (P50 — é typo de código);
    * chave de atributo soft-deleted (P49) ou herdada da migração Chatwoot
      (``cw_id``/``cw_identifier``) ⇒ **tolerada e ignorada**, porque o painel
      reenvia o JSON inteiro no save e um 400 abortaria a gravação toda;
    * ``None`` explícito ⇒ limpar o valor;
    * o resto passa por :func:`validate_value` do tipo declarado.
    """
    if custom_attrs is None:
        return {}, None
    if not isinstance(custom_attrs, dict):
        return {}, "custom_attributes deve ser um objeto."
    all_defs = ca_repo.list_definitions("contact", True)   # inclui soft-deleted
    defs = {d["attribute_key"]: d for d in all_defs if d.get("deleted_at") is None}
    known_keys = {d["attribute_key"] for d in all_defs}
    stored = contact_repo.get_by_phone(phone)
    stored_keys = set((stored or {}).get("custom_attributes") or {})
    valid_partial: dict = {}
    for key, value in custom_attrs.items():
        definition = defs.get(key)
        if definition is None:
            if key in known_keys or key in stored_keys:
                continue
            return {}, f"Atributo '{key}' não existe."
        if value is None:
            valid_partial[key] = None      # limpeza explícita → set_values remove
            continue
        norm, err = validate_value(definition, value)
        if err:
            return {}, err
        valid_partial[key] = norm
    return valid_partial, None


def _write_info(agent_handler, phone: str, body: dict, *,
                custom_attrs, valid_partial: dict) -> tuple[dict, dict]:
    """Grava (bloqueante). Devolve ``(info, custom_attributes_persistidos)``."""
    contact = agent_handler._get_contact(phone)
    # Escalares: edição HUMANA explícita ⇒ semântica de REPLACE (string vazia
    # limpa o campo). Só chaves presentes no body são escritas, então um campo
    # ausente fica intocado enquanto "" é uma limpeza intencional. Diferente do
    # ``update_info`` (merge feito pela IA).
    scalar_fields = {k: body[k] for k in _SCALAR_KEYS if k in body}
    if scalar_fields:
        contact.set_info_fields(scalar_fields)
    # Observações: substitui a lista inteira (``update_info`` só acrescenta).
    if "observations" in body:
        new_obs = [o for o in body["observations"] if isinstance(o, str) and o.strip()]
        contact.info["observations"] = new_obs
        contact_repo.set_observations(contact.id, new_obs)
    if custom_attrs is not None:
        result_attrs = ca_repo.set_values(contacts_table, contact.id, valid_partial)
    else:
        result_attrs = ca_repo.get_values(contacts_table, contact.id)
    return contact.info, result_attrs


async def update_info(agent_handler, phone: str, body: dict) -> tuple[dict | None, str | None]:
    """Aplica a edição e emite ``contact.updated``. ``(info, erro)``.

    ``info`` já vem com ``custom_attributes`` embutido — o painel e o guard de
    resolver leem UMA fonte só (mesma forma de ``get_full_contact``).
    """
    custom_attrs = body.get("custom_attributes")
    valid_partial, err = await asyncio.to_thread(
        validate_custom_attributes, phone, custom_attrs)
    if err:
        return None, err
    info, result_attrs = await asyncio.to_thread(
        _write_info, agent_handler, phone, body,
        custom_attrs=custom_attrs, valid_partial=valid_partial)
    info = {**info, "custom_attributes": result_attrs}
    await emit_with_filter("contact.updated", {
        "phone": phone, "info": info, "custom_attributes": result_attrs,
        "ts": time.time(),
    })
    return info, None


async def delete_contact(agent_handler, ws_manager, phone: str) -> bool:
    """Apaga o contato e todos os dados associados. ``False`` = não existia."""
    def _delete():
        data = contact_repo.get_by_phone(phone)
        if data is None:
            return False
        contact_repo.delete(data["id"])
        agent_handler.drop_cached_contact(phone)   # cache em memória, todas as variantes
        return True

    found = await asyncio.to_thread(_delete)
    if not found:
        return False
    logger.info("[Contact] Deleted contact %s", phone)
    await ws_manager.broadcast("contact_deleted", {"phone": phone})
    return True
