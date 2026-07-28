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
    roles, permissions, role_permissions, user_roles, users, plugins,
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


def sync_core_permissions() -> int:
    """Reconcile the ``permissions`` table with the static core catalog (idempotent).

    Core permission rows are otherwise seeded only by migrations. If a
    permission-adding migration is ever skipped (e.g. ``alembic_version``
    reconciled past it — the "orphan migration" case), its keys stay ABSENT from
    the table. That silently breaks granting them: ``_insert_role_permissions``
    resolves keys to ``permissions.id`` and just skips any key without a row — so
    the checkbox saves as a no-op and the permission count never moves.

    Called at boot (mirrors how plugins self-heal via ``upsert_plugin_permission``)
    so a missing core key is always backfilled from ``PERMISSION_CATALOG`` — the
    runtime source of truth. INSERT-only: never touches existing rows or grants.
    Returns the number of rows inserted."""
    from domain.permission_catalog import PERMISSION_CATALOG
    with get_engine().begin() as conn:
        have = {r[0] for r in conn.execute(select(permissions.c.key))}
        missing = [(k, d) for k, d in PERMISSION_CATALOG if k not in have]
        if missing:
            conn.execute(insert(permissions), [
                {"key": k, "description": d, "plugin_id": None} for k, d in missing])
    return len(missing)


# ── Plugin permissions (plano "RBAC para Plugins" §3.3-3.4) ────────────────

def upsert_plugin_permission(
    key: str, description: str, plugin_id: str, group_label: str | None
) -> None:
    """Upsert one plugin permission row (idempotent, dialect-agnostic).

    Called at plugin load for each ``plugin.<id>.<key>`` declared in the
    ``rbac:`` block. On conflict (existing key) it refreshes the description,
    plugin_id and group_label so manifest edits propagate. Existing
    role/user grants survive (they FK on ``permissions.id``, untouched here)."""
    from db.upsert import upsert
    with get_engine().begin() as conn:
        conn.execute(upsert(
            permissions,
            values={
                "key": key, "description": description,
                "plugin_id": plugin_id, "group_label": group_label,
            },
            conflict_cols=["key"],
            update_cols=["description", "plugin_id", "group_label"],
        ))


def delete_plugin_permissions(plugin_id: str) -> int:
    """Delete every permission row of a plugin. Returns the rows removed.

    ``role_permissions`` / ``user_permissions`` grants cascade via their FK to
    ``permissions.id``. Used when a plugin is deleted."""
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_delete(permissions).where(permissions.c.plugin_id == plugin_id))
    return result.rowcount or 0


def list_plugin_permissions() -> list[dict]:
    """All permission rows that belong to a plugin (``plugin_id IS NOT NULL``)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(permissions)
            .where(permissions.c.plugin_id.isnot(None))
            .order_by(permissions.c.group_label, permissions.c.key)
        ).mappings().all()
    return [dict(r) for r in rows]


def plugin_permission_keys() -> set[str]:
    """The set of keys of every plugin permission (for validation in users/roles).

    Inclui plugins desativados de propósito: um grant existente de um plugin off
    continua sendo uma chave VÁLIDA (não some da validação), então sobrevive a
    edições e volta a aparecer quando o plugin é reativado."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(permissions.c.key).where(permissions.c.plugin_id.isnot(None)))
    return {r[0] for r in rows}


def enabled_plugin_ids() -> set[str]:
    """Ids dos plugins atualmente ATIVOS (``plugins.enabled = 1``)."""
    with get_engine().connect() as conn:
        rows = conn.execute(select(plugins.c.id).where(plugins.c.enabled == 1))
    return {r[0] for r in rows}


def hidden_plugin_permission_keys() -> set[str]:
    """Chaves de permissão de plugin que NÃO aparecem no PermissionPicker agora —
    plugin desativado (ou ausente, com linha remanescente). Os grants delas são
    PRESERVADOS nas edições de cargo/usuário para sobreviverem ao ciclo
    desativar→editar→reativar (o toggle nunca perde atribuição)."""
    enabled = enabled_plugin_ids()
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(permissions.c.key, permissions.c.plugin_id)
            .where(permissions.c.plugin_id.isnot(None)))
    return {k for k, pid in rows if pid not in enabled}


def list_catalog() -> list[dict]:
    """Effective permission catalog: core (static) + plugin rows (from DB).

    Each entry is ``{key, description, plugin_id, group_label, tier, group}``.
    Core perms have ``plugin_id``/``group_label`` = ``None`` but carry a display
    ``tier`` ("core"/"plugin") and ``group`` (subheader) from
    ``permission_catalog.group_for`` — the PermissionPicker renders two tiers
    (Sistema vs Plugins) grouped by ``group``. Plugin rows are always
    ``tier="plugin"`` with ``group`` = their ``group_label``. This replaces the
    static ``PERMISSION_CATALOG`` in the ``/api/roles`` response so plugin perms
    appear in the PermissionPicker automatically."""
    from domain.permission_catalog import PERMISSION_CATALOG, group_for
    catalog = []
    for k, d in PERMISSION_CATALOG:
        tier, group = group_for(k)
        catalog.append({"key": k, "description": d, "plugin_id": None,
                        "group_label": None, "tier": tier, "group": group})
    # Só permissões de plugins ATIVOS aparecem na tela. Ao desativar um plugin as
    # linhas em `permissions` são mantidas (grants sobrevivem ao toggle — ver
    # plugins/rbac.py), mas ficam escondidas do picker; reativar volta a exibi-las.
    enabled = enabled_plugin_ids()
    for p in list_plugin_permissions():
        if p.get("plugin_id") not in enabled:
            continue
        catalog.append({**p, "tier": "plugin",
                        "group": p.get("group_label") or p.get("plugin_id")})
    return catalog


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


def _replace_role_permissions(conn, role_id: int, permission_keys: list[str]) -> None:
    """Delete+reinsert dos grants de um cargo, PRESERVANDO as chaves de plugin
    DESATIVADO já concedidas (escondidas do PermissionPicker) — senão editar o
    cargo com o plugin off apagaria a atribuição e reativar não a traria de volta
    (quebra o "sobrevive ao toggle"). Usado por set_role_permissions e update_role."""
    hidden = hidden_plugin_permission_keys()
    if hidden:
        existing = {r[0] for r in conn.execute(
            select(permissions.c.key).select_from(
                role_permissions.join(
                    permissions, permissions.c.id == role_permissions.c.permission_id))
            .where(role_permissions.c.role_id == role_id))}
        permission_keys = list(set(permission_keys) | (existing & hidden))
    conn.execute(sa_delete(role_permissions)
                 .where(role_permissions.c.role_id == role_id))
    _insert_role_permissions(conn, role_id, permission_keys)


def set_role_permissions(role_id: int, permission_keys: list[str]) -> None:
    """Replace a role's permission grants with the given keys (unknown keys ignored).
    Grants de plugins desativados são preservados (ver _replace_role_permissions)."""
    with get_engine().begin() as conn:
        _replace_role_permissions(conn, role_id, permission_keys)


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
            _replace_role_permissions(conn, role_id, permission_keys)
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
