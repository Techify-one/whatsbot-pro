"""Repository for users + their role assignments (plano 03 Fase 2)."""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update

from db.engine import get_engine
from db.tables import users, user_roles, roles, user_permissions, permissions


def count() -> int:
    with get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(users)).scalar() or 0


def get(user_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
    return _with_roles(dict(row)) if row else None


def get_by_email(email: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users).where(users.c.email == email.strip().lower())
        ).mappings().first()
    return _with_roles(dict(row)) if row else None


def get_auth_row(email: str) -> dict | None:
    """Internal: returns {id, password_hash, is_active} for login verification only.

    Unlike :func:`get_by_email`, this keeps the hash — never expose it to the API.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users.c.id, users.c.password_hash, users.c.is_active)
            .where(users.c.email == email.strip().lower())
        ).mappings().first()
    return dict(row) if row else None


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(users).order_by(users.c.id)).mappings().all()
    return [_with_roles(dict(r)) for r in rows]


def create(*, email: str, name: str, password_hash: str,
           role_keys: list[str] | None = None,
           permission_keys: list[str] | None = None,
           custom: bool = False) -> dict:
    """Create a user. ``custom=True`` ⇒ store explicit ``permission_keys`` and
    skip roles entirely (the per-user set replaces roles)."""
    now = time.time()
    email = email.strip().lower()
    with get_engine().begin() as conn:
        result = conn.execute(insert(users).values(
            email=email, name=name, password_hash=password_hash,
            is_active=1, custom_permissions=1 if custom else 0,
            created_at=now, updated_at=now,
        ))
        uid = result.inserted_primary_key[0]
        if custom:
            _assign_permissions(conn, uid, permission_keys or [])
        elif role_keys:
            _assign_roles(conn, uid, role_keys)
    return get(uid)


def update_info(user_id: int, *, name: str | None = None,
                is_active: int | None = None) -> dict | None:
    values: dict = {"updated_at": time.time()}
    if name is not None:
        values["name"] = name
    if is_active is not None:
        values["is_active"] = is_active
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(**values))
    return get(user_id)


def set_password(user_id: int, password_hash: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id)
                     .values(password_hash=password_hash, updated_at=time.time()))


def touch_login(user_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id)
                     .values(last_login_at=time.time()))


def set_roles(user_id: int, role_keys: list[str]) -> dict | None:
    with get_engine().begin() as conn:
        conn.execute(sa_delete(user_roles).where(user_roles.c.user_id == user_id))
        _assign_roles(conn, user_id, role_keys)
    return get(user_id)


def set_custom_permissions(user_id: int, permission_keys: list[str]) -> dict | None:
    """Switch a user to custom mode and replace their explicit permission set.

    Clears any role assignments (custom replaces roles) and sets the flag.
    """
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id)
                     .values(custom_permissions=1, updated_at=time.time()))
        conn.execute(sa_delete(user_roles).where(user_roles.c.user_id == user_id))
        conn.execute(sa_delete(user_permissions)
                     .where(user_permissions.c.user_id == user_id))
        _assign_permissions(conn, user_id, permission_keys)
    return get(user_id)


def clear_custom_permissions(user_id: int, role_keys: list[str]) -> dict | None:
    """Switch a user back to role mode: drop the flag + explicit grants, set roles."""
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id)
                     .values(custom_permissions=0, updated_at=time.time()))
        conn.execute(sa_delete(user_permissions)
                     .where(user_permissions.c.user_id == user_id))
        conn.execute(sa_delete(user_roles).where(user_roles.c.user_id == user_id))
        _assign_roles(conn, user_id, role_keys)
    return get(user_id)


def delete(user_id: int) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(users).where(users.c.id == user_id))
    return (result.rowcount or 0) > 0


# ── helpers ───────────────────────────────────────────────────────────────

def _assign_roles(conn, user_id: int, role_keys: list[str]) -> None:
    role_rows = conn.execute(
        select(roles.c.id, roles.c.key).where(roles.c.key.in_(role_keys))
    ).all()
    for rid, _key in role_rows:
        conn.execute(insert(user_roles).values(user_id=user_id, role_id=rid))


def _assign_permissions(conn, user_id: int, permission_keys: list[str]) -> None:
    if not permission_keys:
        return
    perm_ids = [r[0] for r in conn.execute(
        select(permissions.c.id).where(permissions.c.key.in_(permission_keys)))]
    for pid in perm_ids:
        conn.execute(insert(user_permissions).values(user_id=user_id, permission_id=pid))


def _with_roles(user: dict) -> dict:
    is_custom = bool(user.get("custom_permissions"))
    with get_engine().connect() as conn:
        role_keys = [r[0] for r in conn.execute(
            select(roles.c.key).join(user_roles, user_roles.c.role_id == roles.c.id)
            .where(user_roles.c.user_id == user["id"])
        )]
        perm_keys = [p[0] for p in conn.execute(
            select(permissions.c.key)
            .join(user_permissions, user_permissions.c.permission_id == permissions.c.id)
            .where(user_permissions.c.user_id == user["id"])
        )]
    user["roles"] = role_keys
    user["custom_permissions"] = is_custom
    # Explicit per-user grants (only meaningful in custom mode); sorted for the UI.
    user["permissions"] = sorted(perm_keys)
    user["is_admin"] = "admin" in role_keys
    user.pop("password_hash", None)  # never leak the hash to callers/API
    return user
