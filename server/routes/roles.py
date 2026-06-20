"""Role editor endpoints (RBAC). All gated by ``users.manage``.

Lets an admin edit the permissions of existing roles and create/delete custom
roles. The ``admin`` role is a runtime short-circuit (always every permission),
so it is locked: it cannot be edited, reset or deleted. The seeded system roles
``gestor``/``atendente`` can have their permissions edited and reset to defaults,
but cannot be deleted. Custom roles (``is_system=0``) are fully editable and
deletable (unless still assigned to a user).
"""

import asyncio
import logging
import re

from fastapi import Request

from db.repositories import rbac_repo
from server.authz import permission_denied
from server.permissions import ROLE_LABELS, ROLE_DEFAULTS, ALL_PERMISSION_KEYS
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_RESERVED_KEYS = {"admin", "gestor", "atendente"}
_VALID_PERMISSION_KEYS = set(ALL_PERMISSION_KEYS)


def _clean_perms(raw) -> list[str]:
    return [p for p in (raw or []) if p in _VALID_PERMISSION_KEYS]


def register_routes(app, deps):

    @app.post("/api/roles")
    async def create_role(body: dict, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        key = (body.get("key") or "").strip().lower()
        name = (body.get("name") or "").strip()
        if not _KEY_RE.match(key):
            return _err("Chave inválida (use a-z, 0-9, _; começando por letra).", status=400)
        if key in _RESERVED_KEYS:
            return _err("Essa chave é reservada para um papel de sistema.", status=400)
        if not name:
            return _err("Informe um nome para o papel.", status=400)
        if await asyncio.to_thread(rbac_repo.get_role_by_key, key):
            return _err("Já existe um papel com essa chave.", status=409)
        role = await asyncio.to_thread(
            rbac_repo.create_role, key, name, _clean_perms(body.get("permission_keys")))
        role["label"] = ROLE_LABELS.get(role["key"], role["name"])
        logger.info("Role created: %s", key)
        return _ok({"role": role})

    @app.put("/api/roles/{role_id}")
    async def update_role(role_id: int, body: dict, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        role = await asyncio.to_thread(rbac_repo.get_role, role_id)
        if not role:
            return _err("Papel não encontrado.", status=404)
        if role["key"] == "admin":
            return _err("O papel Administrador tem todas as permissões e não é editável.",
                        status=400)
        name = body.get("name")
        perms = (_clean_perms(body.get("permission_keys"))
                 if "permission_keys" in body else None)
        updated = await asyncio.to_thread(
            rbac_repo.update_role, role_id,
            name=(name.strip() if isinstance(name, str) and name.strip() else None),
            permission_keys=perms)
        updated["label"] = ROLE_LABELS.get(updated["key"], updated["name"])
        logger.info("Role updated: %s", role["key"])
        return _ok({"role": updated})

    @app.delete("/api/roles/{role_id}")
    async def delete_role(role_id: int, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        role = await asyncio.to_thread(rbac_repo.get_role, role_id)
        if not role:
            return _err("Papel não encontrado.", status=404)
        if role.get("is_system"):
            return _err("Papéis de sistema não podem ser excluídos.", status=409)
        count = await asyncio.to_thread(rbac_repo.role_assignment_count, role_id)
        if count > 0:
            return _err(f"Papel atribuído a {count} usuário(s). "
                        "Remova-o desses usuários antes de excluir.", status=409)
        await asyncio.to_thread(rbac_repo.delete_role, role_id)
        logger.info("Role deleted: %s", role["key"])
        return _ok({"deleted": True})

    @app.post("/api/roles/{role_id}/reset")
    async def reset_role(role_id: int, request: Request):
        """Restore a system role's permissions to the seeded defaults."""
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        role = await asyncio.to_thread(rbac_repo.get_role, role_id)
        if not role:
            return _err("Papel não encontrado.", status=404)
        if role["key"] not in ROLE_DEFAULTS:
            return _err("Sem padrão definido para este papel.", status=400)
        defaults = sorted(ROLE_DEFAULTS[role["key"]])
        await asyncio.to_thread(rbac_repo.set_role_permissions, role_id, defaults)
        updated = await asyncio.to_thread(rbac_repo.get_role, role_id)
        updated["label"] = ROLE_LABELS.get(updated["key"], updated["name"])
        logger.info("Role reset to defaults: %s", role["key"])
        return _ok({"role": updated})
