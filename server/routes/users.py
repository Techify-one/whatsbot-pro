"""User management endpoints (plano 03 Fase 5). All gated by ``users.manage``.

An open install (no user identity, before the first admin) passes the gate, so a
fresh install can still reach these to bootstrap the first user.
"""

import asyncio
import logging

from fastapi import Request

from db.repositories import user_repo, rbac_repo, inbox_repo, inbox_member_repo
from server.auth import hash_password_argon2
from server.authz import permission_denied
from server.permissions import ROLE_LABELS, ALL_PERMISSION_KEYS
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def _valid_permission_keys() -> set[str]:
    """Core (static) + plugin (DB) permission keys, resolved per request.

    Plugin perms are registered at load, so they can appear/disappear across a
    restart — never freeze this set at import time (plano "RBAC para Plugins")."""
    return set(ALL_PERMISSION_KEYS) | rbac_repo.plugin_permission_keys()


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
        # Enriquece com as inboxes (caixas de entrada) de cada usuário — pré-preenche
        # a seção "Caixas de entrada" do editor e permite mostrar no card.
        by_user = await asyncio.to_thread(inbox_member_repo.inbox_ids_by_user)
        for u in users:
            u["inbox_ids"] = by_user.get(u.get("id"), [])
        return _ok({"users": users})

    @app.get("/api/roles")
    async def list_roles(request: Request):
        denied = permission_denied(request, "users.manage")
        if denied:
            return denied
        roles = await asyncio.to_thread(rbac_repo.list_roles_with_permissions)
        for r in roles:
            r["label"] = ROLE_LABELS.get(r["key"], r["name"])
        # Catálogo de inboxes p/ a seção "Caixas de entrada" da tela de usuários
        # (servido aqui, gated por users.manage, evitando acoplar com inbox.manage).
        # list_with_channel: só caixas vivas (sem órfãs) e com o nome atual do canal
        # (plano inboxes/canais §4.6 — corrige o Bug 1 da tela de Usuários).
        inboxes = await asyncio.to_thread(inbox_repo.list_with_channel)
        # Catálogo efetivo: core (estático) + perms de plugin (linhas da tabela
        # permissions). Cada item carrega plugin_id/group_label p/ o picker agrupar.
        catalog = await asyncio.to_thread(rbac_repo.list_catalog)
        return _ok({
            "roles": roles,
            "permissions": catalog,
            "inboxes": inboxes,
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
            valid_perms = await asyncio.to_thread(_valid_permission_keys)
            perms = [p for p in (body.get("permissions") or []) if p in valid_perms]
            if not perms:
                return _err("Selecione ao menos uma permissão.", status=400)
        else:
            valid_roles = await asyncio.to_thread(_valid_role_keys)
            roles = [r for r in (body.get("roles") or []) if r in valid_roles]
            if not roles:
                return _err("Selecione ao menos um grupo de permissão.", status=400)
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
        # Caixas de entrada (inbox_members): opcional no body — atribui após criar.
        if isinstance(body.get("inbox_ids"), list):
            user["inbox_ids"] = await asyncio.to_thread(
                inbox_member_repo.set_inboxes_for_user, user["id"], body["inbox_ids"])
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
                valid_perms = await asyncio.to_thread(_valid_permission_keys)
                perms = [p for p in (body.get("permissions") or [])
                         if p in valid_perms]
                if not perms:
                    return _err("Selecione ao menos uma permissão.", status=400)
                await asyncio.to_thread(user_repo.set_custom_permissions, user_id, perms)
            else:
                valid_roles = await asyncio.to_thread(_valid_role_keys)
                valid = [r for r in (roles or []) if r in valid_roles]
                if not valid:
                    return _err("Selecione ao menos um grupo de permissão.", status=400)
                await asyncio.to_thread(user_repo.clear_custom_permissions, user_id, valid)

        # Caixas de entrada (inbox_members): substitui o conjunto se vier no body.
        if isinstance(body.get("inbox_ids"), list):
            await asyncio.to_thread(
                inbox_member_repo.set_inboxes_for_user, user_id, body["inbox_ids"])

        user = await asyncio.to_thread(user_repo.get, user_id)
        user["inbox_ids"] = await asyncio.to_thread(
            inbox_member_repo.inbox_ids_for_user, user_id)
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
