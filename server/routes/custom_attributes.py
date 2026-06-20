"""Custom attribute definitions CRUD (plano 05).

Admin-managed metadata for contact/conversation custom attributes. Values are
written through the contact/conversation info endpoints, not here.

Authorization (admin-only on definitions) lands with RBAC (plano 03); until then
these sit behind the current session auth.
"""

import asyncio
import logging
import re
import time

from fastapi import Request

from db.repositories import custom_attribute_repo as ca_repo
from db.repositories.custom_attribute_validate import VALID_TYPES
from db.tables import contacts
from plugins.events import emit_with_filter
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_APPLIES = {"contact", "conversation"}
_ENTITY_TABLES = {"contact": contacts}  # conversation entity arrives with plano 01


def register_routes(app, deps):

    @app.get("/api/custom-attributes")
    async def list_custom_attributes(applies_to: str | None = None):
        if applies_to and applies_to not in _APPLIES:
            return _err("applies_to deve ser 'contact' ou 'conversation'.")
        rows = await asyncio.to_thread(ca_repo.list_definitions, applies_to)
        return _ok(rows)

    @app.post("/api/custom-attributes")
    async def create_custom_attribute(request: Request):
        body = await request.json()
        key = (body.get("attribute_key") or "").strip().lower()
        display_name = (body.get("display_name") or "").strip()
        attr_type = (body.get("type") or "text").strip().lower()
        applies_to = (body.get("applies_to") or "contact").strip().lower()
        options = body.get("options")

        if not display_name:
            return _err("O nome de exibição é obrigatório.")
        if not _KEY_RE.match(key):
            return _err("A chave deve ser snake_case (minúsculas, começando com letra).")
        if attr_type not in VALID_TYPES:
            return _err(f"Tipo inválido. Use um de: {', '.join(sorted(VALID_TYPES))}.")
        if applies_to not in _APPLIES:
            return _err("applies_to deve ser 'contact' ou 'conversation'.")
        if attr_type == "list":
            if not isinstance(options, list) or not options:
                return _err("Atributos do tipo 'list' precisam de uma lista de opções.")
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
            filterable=1 if body.get("filterable") else 0,
        )
        if row is None:
            return _err(f"Já existe um atributo '{key}' para {applies_to}.")
        await emit_with_filter("custom_attribute.created", {"definition": row, "ts": time.time()})
        return _ok(row)

    @app.put("/api/custom-attributes/{def_id}")
    async def update_custom_attribute(def_id: int, request: Request):
        body = await request.json()
        existing = await asyncio.to_thread(ca_repo.get_definition, def_id)
        if existing is None or existing.get("deleted_at") is not None:
            return _err("Atributo não encontrado.", 404)
        fields: dict = {}
        if "display_name" in body:
            dn = (body.get("display_name") or "").strip()
            if not dn:
                return _err("O nome de exibição é obrigatório.")
            fields["display_name"] = dn
        for f in ("description", "regex_pattern", "regex_cue"):
            if f in body:
                fields[f] = (body.get(f) or "")
        if "required" in body:
            fields["required"] = 1 if body.get("required") else 0
        if "position" in body:
            fields["position"] = int(body.get("position") or 0)
        if "filterable" in body:
            fields["filterable"] = 1 if body.get("filterable") else 0
        if "options" in body and existing.get("type") == "list":
            opts = body.get("options")
            if not isinstance(opts, list) or not opts:
                return _err("Atributos do tipo 'list' precisam de uma lista de opções.")
            fields["options"] = [str(o).strip() for o in opts if str(o).strip()]

        row = await asyncio.to_thread(ca_repo.update_definition, def_id, **fields)
        if row is None:
            return _err("Atributo não encontrado.", 404)
        await emit_with_filter("custom_attribute.updated", {"definition": row, "ts": time.time()})
        return _ok(row)

    @app.delete("/api/custom-attributes/{def_id}")
    async def delete_custom_attribute(def_id: int):
        ok = await asyncio.to_thread(ca_repo.delete_definition, def_id)
        if not ok:
            return _err("Atributo não encontrado.", 404)
        await emit_with_filter("custom_attribute.deleted", {"id": def_id, "ts": time.time()})
        return _ok({"deleted": def_id})

    @app.post("/api/custom-attributes/purge-orphans")
    async def purge_orphans(applies_to: str = "contact"):
        if applies_to not in _ENTITY_TABLES:
            return _err("applies_to deve ser 'contact' (conversation chega com o plano 01).")
        table = _ENTITY_TABLES[applies_to]
        touched = await asyncio.to_thread(ca_repo.purge_orphan_values, table, applies_to)
        return _ok({"touched": touched})
