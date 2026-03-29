"""Tag CRUD and contact-tag assignment endpoints."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import Request

from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager

    @app.get("/api/tags")
    async def list_tags():
        """Return all global tags."""
        return _ok(agent_handler.tag_registry.all())

    @app.post("/api/tags")
    async def create_tag(request: Request):
        """Create a new global tag."""
        body = await request.json()
        name = (body.get("name") or "").strip()
        color = (body.get("color") or "").strip()
        if not name:
            return _err("Nome da tag é obrigatório.")
        if len(name) > 30:
            return _err("Nome da tag deve ter no máximo 30 caracteres.")
        if not color:
            return _err("Cor da tag é obrigatória.")
        if not agent_handler.tag_registry.create(name, color):
            return _err(f"Tag '{name}' já existe.")
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        return _ok({"name": name, "color": color})

    @app.put("/api/tags/{name}")
    async def update_tag(name: str, request: Request):
        """Update a global tag (rename and/or change color)."""
        body = await request.json()
        new_name = (body.get("name") or "").strip() or None
        color = (body.get("color") or "").strip() or None
        if new_name and len(new_name) > 30:
            return _err("Nome da tag deve ter no máximo 30 caracteres.")
        if new_name and new_name != name and agent_handler.tag_registry.get(new_name):
            return _err(f"Tag '{new_name}' já existe.")
        if not agent_handler.tag_registry.update(name, new_name=new_name, color=color):
            return _err(f"Tag '{name}' não encontrada.", 404)
        # If renamed, update all contacts that reference the old name
        if new_name and new_name != name:
            await asyncio.to_thread(_rename_tag_in_contacts, name, new_name)
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        final_name = new_name or name
        tag_data = agent_handler.tag_registry.get(final_name)
        return _ok({"name": final_name, "color": tag_data["color"] if tag_data else color})

    @app.delete("/api/tags/{name}")
    async def delete_tag(name: str):
        """Delete a global tag and remove it from all contacts."""
        if not agent_handler.tag_registry.delete(name):
            return _err(f"Tag '{name}' não encontrada.", 404)
        affected = await asyncio.to_thread(_remove_tag_from_contacts, name)
        await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
        return _ok({"deleted": name, "contacts_affected": affected})

    @app.put("/api/contacts/{phone}/tags")
    async def set_contact_tags(phone: str, request: Request):
        """Set the tags for a specific contact."""
        body = await request.json()
        tags = body.get("tags", [])
        if not isinstance(tags, list):
            return _err("Tags deve ser uma lista.")

        def _update():
            # Check contact file exists before modifying
            fp = agent_handler.memory_dir / f"{phone}.json"
            if not fp.exists():
                return None
            contact = agent_handler._get_contact(phone)
            contact.set_tags(tags)
            return contact.tags

        result = await asyncio.to_thread(_update)
        if result is None:
            return _err("Contato não encontrado.", 404)
        await ws_manager.broadcast("contact_tags_updated", {"phone": phone, "tags": result})
        return _ok({"phone": phone, "tags": result})

    def _remove_tag_from_contacts(tag_name: str) -> int:
        """Remove a tag from all contact files. Returns count of affected contacts."""
        contacts_dir = agent_handler.memory_dir
        affected = 0
        for f in contacts_dir.glob("*.json"):
            if f.stem.startswith("_"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                tags = data.get("tags", [])
                if tag_name in tags:
                    tags.remove(tag_name)
                    data["tags"] = tags
                    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    affected += 1
                    # Update in-memory contact if loaded
                    phone = data.get("phone", f.stem)
                    if phone in agent_handler._contacts:
                        agent_handler._contacts[phone].tags = tags
            except Exception:
                continue
        return affected

    def _rename_tag_in_contacts(old_name: str, new_name: str) -> int:
        """Rename a tag in all contact files. Returns count of affected contacts."""
        contacts_dir = agent_handler.memory_dir
        affected = 0
        for f in contacts_dir.glob("*.json"):
            if f.stem.startswith("_"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                tags = data.get("tags", [])
                if old_name in tags:
                    idx = tags.index(old_name)
                    tags[idx] = new_name
                    data["tags"] = tags
                    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    affected += 1
                    # Update in-memory contact if loaded
                    phone = data.get("phone", f.stem)
                    if phone in agent_handler._contacts:
                        agent_handler._contacts[phone].tags = tags
            except Exception:
                continue
        return affected
