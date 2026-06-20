"""Template management permissions (template.create / template.delete).

Adds the two RBAC permissions that gate template creation/deletion in the
WhatsApp Cloud picker, and grants both to the seeded ``gestor`` role. ``admin``
holds everything via the '*' short-circuit; ``atendente`` intentionally does not
get template management (it can still send via ``conversation.reply``).

Defensive/idempotent: only inserts keys and grants that are missing, so it is
safe on a shared DB that may already have them. server/permissions.py is the
runtime source of truth; this is the seed snapshot (mirrors 0012's pattern).

Revision ID: 0021_template_permissions
Revises: 0020_conversation_labels
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_template_permissions"
down_revision: Union[str, Sequence[str], None] = "0020_conversation_labels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_PERMISSIONS = [
    ("template.create", "Criar templates (WhatsApp Cloud)"),
    ("template.delete", "Apagar templates (WhatsApp Cloud)"),
]
_GRANT_TO_ROLE = "gestor"


def upgrade() -> None:
    conn = op.get_bind()
    perm_by_key = {r[0]: r[1] for r in conn.execute(sa.text("SELECT key, id FROM permissions"))}

    perms_t = sa.table("permissions", sa.column("key"), sa.column("description"))
    new_rows = [{"key": k, "description": d}
                for k, d in _NEW_PERMISSIONS if k not in perm_by_key]
    if new_rows:
        op.bulk_insert(perms_t, new_rows)
        perm_by_key = {r[0]: r[1]
                       for r in conn.execute(sa.text("SELECT key, id FROM permissions"))}

    role_id = conn.execute(
        sa.text("SELECT id FROM roles WHERE key = :k"), {"k": _GRANT_TO_ROLE}
    ).scalar()
    if role_id is None:
        return
    existing_grants = {r[0] for r in conn.execute(
        sa.text("SELECT permission_id FROM role_permissions WHERE role_id = :r"),
        {"r": role_id})}
    rp_t = sa.table("role_permissions", sa.column("role_id"), sa.column("permission_id"))
    grant_rows = []
    for key, _ in _NEW_PERMISSIONS:
        pid = perm_by_key.get(key)
        if pid is not None and pid not in existing_grants:
            grant_rows.append({"role_id": role_id, "permission_id": pid})
    if grant_rows:
        op.bulk_insert(rp_t, grant_rows)


def downgrade() -> None:
    conn = op.get_bind()
    for key, _ in _NEW_PERMISSIONS:
        pid = conn.execute(
            sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": key}
        ).scalar()
        if pid is not None:
            conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p"),
                         {"p": pid})
            conn.execute(sa.text("DELETE FROM permissions WHERE id = :p"), {"p": pid})
