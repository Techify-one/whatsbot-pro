"""Repository for roles/permissions resolution (plano 03 Fase 2).

``admin`` is a short-circuit: a user with the ``admin`` role has every
permission, regardless of role_permissions (which is intentionally not seeded
for admin).
"""

from __future__ import annotations

from sqlalchemy import select

from db.engine import get_engine
from db.tables import roles, permissions, role_permissions, user_roles


def list_roles() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(roles).order_by(roles.c.id)).mappings().all()
    return [dict(r) for r in rows]


def list_permissions() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(permissions).order_by(permissions.c.id)).mappings().all()
    return [dict(r) for r in rows]


def get_role_permissions(role_key: str) -> set[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(permissions.c.key)
            .join(role_permissions, role_permissions.c.permission_id == permissions.c.id)
            .join(roles, roles.c.id == role_permissions.c.role_id)
            .where(roles.c.key == role_key)
        )
    return {r[0] for r in rows}


def user_permissions(user_id: int) -> set[str]:
    """Resolve the effective permission set for a user (admin ⇒ all)."""
    with get_engine().connect() as conn:
        role_keys = {r[0] for r in conn.execute(
            select(roles.c.key).join(user_roles, user_roles.c.role_id == roles.c.id)
            .where(user_roles.c.user_id == user_id)
        )}
        if "admin" in role_keys:
            return {p[0] for p in conn.execute(select(permissions.c.key))} | {"*"}
        perms = {p[0] for p in conn.execute(
            select(permissions.c.key)
            .join(role_permissions, role_permissions.c.permission_id == permissions.c.id)
            .join(user_roles, user_roles.c.role_id == role_permissions.c.role_id)
            .where(user_roles.c.user_id == user_id)
        )}
    return perms


def user_has_permission(user_id: int, permission_key: str) -> bool:
    perms = user_permissions(user_id)
    return "*" in perms or permission_key in perms
