"""User management endpoints (plano 03 Fase 5). All gated by ``users.manage``.

The legacy single-password admin (no user identity) passes the gate, so a fresh
install can still reach these to bootstrap/manage users before enforcement is on.
"""

import asyncio
import logging

from fastapi import Request

from db.repositories import user_repo, rbac_repo
from server.auth import hash_password_argon2
from server.authz import permission_denied
from server.permissions import PERMISSION_CATALOG, ROLE_LABELS, ALL_PERMISSION_KEYS
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

_VALID_PERMISSION_KEYS = set(ALL_PERMISSION_KEYS)


def _count_active_admins() -> int:
    return sum(1 for u in user_repo.list_all()
               if u.get("is_admin") and u.get("is_active"))


def _valid_role_keys() -> set[str]:
    """Role keys that currently exist (system + custom)."""
    return {r["key"] for r in rbac_repo.list_roles()}


def register_routes(app, deps):

    @app.get("/api/users")
    async def list_users(request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        users = await asyncio.to_thread(user_repo.list_all)
        return _ok({"users": users})

    @app.get("/api/roles")
    async def list_roles(request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        roles = await asyncio.to_thread(rbac_repo.list_roles_with_permissions)
        for r in roles:
            r["label"] = ROLE_LABELS.get(r["key"], r["name"])
        return _ok({
            "roles": roles,
            "permissions": [{"key": k, "description": d} for k, d in PERMISSION_CATALOG],
        })

    @app.post("/api/users")
    async def create_user(body: dict, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        email = (body.get("email") or "").strip().lower()
        name = (body.get("name") or "").strip() or email
        password = body.get("password") or ""
        custom = bool(body.get("custom_permissions"))
        if not email or "@" not in email:
            return _err("Email inválido.", status=400)
        if len(password) < 8:
            return _err("A senha deve ter ao menos 8 caracteres.", status=400)
        if custom:
            perms = [p for p in (body.get("permissions") or []) if p in _VALID_PERMISSION_KEYS]
            if not perms:
                return _err("Selecione ao menos uma permissão.", status=400)
        else:
            valid_roles = await asyncio.to_thread(_valid_role_keys)
            roles = [r for r in (body.get("roles") or []) if r in valid_roles]
            if not roles:
                return _err("Selecione ao menos um papel.", status=400)
        if await asyncio.to_thread(user_repo.get_by_email, email):
            return _err("Já existe um usuário com esse email.", status=409)
        phc = hash_password_argon2(password)
        if custom:
            user = await asyncio.to_thread(
                user_repo.create, email=email, name=name, password_hash=phc,
                permission_keys=perms, custom=True)
            logger.info("User created: %s (custom perms=%s).", email, perms)
        else:
            user = await asyncio.to_thread(
                user_repo.create, email=email, name=name, password_hash=phc, role_keys=roles)
            logger.info("User created: %s (roles=%s).", email, roles)
        return _ok({"user": user})

    @app.put("/api/users/{user_id}")
    async def update_user(user_id: int, body: dict, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        existing = await asyncio.to_thread(user_repo.get, user_id)
        if not existing:
            return _err("Usuário não encontrado.", status=404)

        name = body.get("name")
        is_active = body.get("is_active")
        roles = body.get("roles")
        # Target permission mode: explicit flag if sent, else keep current mode.
        target_custom = (bool(body.get("custom_permissions"))
                         if "custom_permissions" in body
                         else bool(existing.get("custom_permissions")))
        # Whether this update touches the permission assignment at all.
        touches_perms = any(k in body for k in ("custom_permissions", "roles", "permissions"))

        # Guard: never deactivate or de-admin the last active admin. Switching an
        # admin to custom mode drops their roles too, so it counts as de-admin.
        will_deactivate = is_active is not None and not is_active and existing.get("is_active")
        will_remove_admin = existing.get("is_admin") and touches_perms and (
            target_custom or (roles is not None and "admin" not in roles))
        if (will_deactivate or will_remove_admin) and existing.get("is_admin"):
            if await asyncio.to_thread(_count_active_admins) <= 1:
                return _err("Não é possível remover o último administrador ativo.", status=409)

        if name is not None or is_active is not None:
            await asyncio.to_thread(
                user_repo.update_info, user_id,
                name=name if name is not None else None,
                is_active=(1 if is_active else 0) if is_active is not None else None)

        if touches_perms:
            if target_custom:
                perms = [p for p in (body.get("permissions") or [])
                         if p in _VALID_PERMISSION_KEYS]
                if not perms:
                    return _err("Selecione ao menos uma permissão.", status=400)
                await asyncio.to_thread(user_repo.set_custom_permissions, user_id, perms)
            else:
                valid_roles = await asyncio.to_thread(_valid_role_keys)
                valid = [r for r in (roles or []) if r in valid_roles]
                if not valid:
                    return _err("Selecione ao menos um papel.", status=400)
                await asyncio.to_thread(user_repo.clear_custom_permissions, user_id, valid)

        user = await asyncio.to_thread(user_repo.get, user_id)
        return _ok({"user": user})

    @app.post("/api/users/{user_id}/password")
    async def reset_password(user_id: int, body: dict, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        existing = await asyncio.to_thread(user_repo.get, user_id)
        if not existing:
            return _err("Usuário não encontrado.", status=404)
        password = body.get("password") or ""
        if len(password) < 8:
            return _err("A senha deve ter ao menos 8 caracteres.", status=400)
        phc = hash_password_argon2(password)
        await asyncio.to_thread(user_repo.set_password, user_id, phc)
        return _ok({"updated": True})

    @app.delete("/api/users/{user_id}")
    async def delete_user(user_id: int, request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        existing = await asyncio.to_thread(user_repo.get, user_id)
        if not existing:
            return _err("Usuário não encontrado.", status=404)
        if existing.get("is_admin") and await asyncio.to_thread(_count_active_admins) <= 1:
            return _err("Não é possível remover o último administrador ativo.", status=409)
        await asyncio.to_thread(user_repo.delete, user_id)
        logger.info("User deleted: %s.", existing.get("email"))
        return _ok({"deleted": True})
