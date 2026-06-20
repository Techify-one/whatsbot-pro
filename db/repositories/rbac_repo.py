"""Repository for roles/permissions resolution (plano 03 Fase 2).

``admin`` is a short-circuit: a user with the ``admin`` role has every
permission, regardless of role_permissions (which is intentionally not seeded
for admin).

A user with ``users.custom_permissions = 1`` has an explicit per-user permission
set (table ``user_permissions``) that REPLACES roles entirely — no role, no admin
short-circuit. This lets an admin build a fully custom permission set for one
user, independent of any role.

This module also exposes the write helpers used by the runtime role editor
(create/update/delete roles and their permission grants).
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update

from db.engine import get_engine
from db.tables import (
    roles, permissions, role_permissions, user_roles, users,
    # aliased: the module also defines a function named ``user_permissions``,
    # which would shadow the table name at module scope.
    user_permissions as user_permissions_t,
)


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
    """Resolve the effective permission set for a user.

    Precedence: custom (explicit grants) > admin (all + ``*``) > role union.
    """
    with get_engine().connect() as conn:
        is_custom = conn.execute(
            select(users.c.custom_permissions).where(users.c.id == user_id)
        ).scalar()
        if is_custom:
            return {p[0] for p in conn.execute(
                select(permissions.c.key)
                .join(user_permissions_t,
                      user_permissions_t.c.permission_id == permissions.c.id)
                .where(user_permissions_t.c.user_id == user_id)
            )}

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


# ── Role editor (runtime writes) ──────────────────────────────────────────

def list_roles_with_permissions() -> list[dict]:
    """Every role plus its ``permission_keys`` list (sorted). admin → all keys."""
    with get_engine().connect() as conn:
        role_rows = conn.execute(select(roles).order_by(roles.c.id)).mappings().all()
        all_keys = sorted(p[0] for p in conn.execute(select(permissions.c.key)))
        grants: dict[int, list[str]] = {}
        for rid, pkey in conn.execute(
            select(role_permissions.c.role_id, permissions.c.key)
            .join(permissions, permissions.c.id == role_permissions.c.permission_id)
        ):
            grants.setdefault(rid, []).append(pkey)
    out = []
    for r in role_rows:
        d = dict(r)
        # admin is a short-circuit — present it as holding every permission.
        d["permission_keys"] = (all_keys if d["key"] == "admin"
                                else sorted(grants.get(d["id"], [])))
        out.append(d)
    return out


def get_role(role_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(roles).where(roles.c.id == role_id)).mappings().first()
        if not row:
            return None
        d = dict(row)
        if d["key"] == "admin":
            d["permission_keys"] = sorted(p[0] for p in conn.execute(select(permissions.c.key)))
        else:
            d["permission_keys"] = sorted(
                pk[0] for pk in conn.execute(
                    select(permissions.c.key)
                    .join(role_permissions,
                          role_permissions.c.permission_id == permissions.c.id)
                    .where(role_permissions.c.role_id == role_id)
                ))
    return d


def get_role_by_key(role_key: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(roles.c.id).where(roles.c.key == role_key)).first()
    return get_role(row[0]) if row else None


def role_assignment_count(role_id: int) -> int:
    """How many users currently hold this role."""
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(user_roles)
            .where(user_roles.c.role_id == role_id)
        ).scalar() or 0


def set_role_permissions(role_id: int, permission_keys: list[str]) -> None:
    """Replace a role's permission grants with the given keys (unknown keys ignored)."""
    with get_engine().begin() as conn:
        conn.execute(sa_delete(role_permissions)
                     .where(role_permissions.c.role_id == role_id))
        _insert_role_permissions(conn, role_id, permission_keys)


def create_role(key: str, name: str, permission_keys: list[str]) -> dict:
    with get_engine().begin() as conn:
        result = conn.execute(insert(roles).values(
            key=key, name=name, is_system=0, created_at=time.time()))
        rid = result.inserted_primary_key[0]
        _insert_role_permissions(conn, rid, permission_keys)
    return get_role(rid)


def update_role(role_id: int, *, name: str | None = None,
                permission_keys: list[str] | None = None) -> dict | None:
    with get_engine().begin() as conn:
        if name is not None:
            conn.execute(update(roles).where(roles.c.id == role_id).values(name=name))
        if permission_keys is not None:
            conn.execute(sa_delete(role_permissions)
                         .where(role_permissions.c.role_id == role_id))
            _insert_role_permissions(conn, role_id, permission_keys)
    return get_role(role_id)


def delete_role(role_id: int) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(roles).where(roles.c.id == role_id))
    return (result.rowcount or 0) > 0


def _insert_role_permissions(conn, role_id: int, permission_keys: list[str]) -> None:
    if not permission_keys:
        return
    perm_ids = [r[0] for r in conn.execute(
        select(permissions.c.id).where(permissions.c.key.in_(permission_keys)))]
    for pid in perm_ids:
        conn.execute(insert(role_permissions).values(role_id=role_id, permission_id=pid))
