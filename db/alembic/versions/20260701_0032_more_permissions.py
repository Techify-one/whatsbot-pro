"""Plano 24 — maximizar permissões (CRUD) na tela de Cargos.

Adds 10 new core RBAC permissions and grants 9 of them to the seeded ``gestor``
role. ``database.manage`` is intentionally **admin-only** (no role grant — only
the ``admin`` '*' short-circuit holds it), hardening DB migrate/repair.

Defensive/idempotent: only inserts keys and grants that are missing, so it is
safe on a shared DB that may already have them. server/permissions.py is the
runtime source of truth; this is the seed snapshot (mirrors 0021's pattern).

Revision ID: 0032_more_permissions
Revises: 0031_ai_agent_prompt_history
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032_more_permissions"
down_revision: Union[str, Sequence[str], None] = "0031_ai_agent_prompt_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_PERMISSIONS = [
    ("contact.delete", "Excluir contato (e todas as conversas)"),
    ("conversation.delete", "Excluir conversa (apaga histórico)"),
    ("tag.manage", "Criar/editar/excluir etiquetas (de contato)"),
    ("conversation_label.manage", "Criar/editar/excluir etiquetas de conversa"),
    ("sandbox.use", "Usar o chat de teste (sandbox)"),
    ("usage.read", "Ver custos e uso da API"),
    ("custom_attribute.manage", "Definir campos personalizados"),
    ("execution.read", "Ver trilha de execuções"),
    ("execution.delete", "Expurgar execuções"),
    ("database.manage", "Migração e manutenção do banco"),
]
# All but database.manage are granted to gestor (database.manage = admin-only).
_GRANT_KEYS = [k for k, _ in _NEW_PERMISSIONS if k != "database.manage"]
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
    for key in _GRANT_KEYS:
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
