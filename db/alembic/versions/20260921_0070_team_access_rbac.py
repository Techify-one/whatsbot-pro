"""Team access modes, lifecycle and RBAC permissions.

Existing restricted teams remain ``list_hidden``: ``enforce_team_access`` is
backfilled to zero.  ``private`` is therefore opt-in and never introduced by
an upgrade alone.

Revision ID: 0070_team_access_rbac
Revises: 0069_team_visible_to_assignee
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0070_team_access_rbac"
down_revision: Union[str, Sequence[str], None] = "0069_team_visible_to_assignee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSIONS = [
    ("team.manage", "Criar, editar, desativar e gerenciar membros de times"),
    ("conversation.team.assign", "Atribuir conversas entre times dos quais participa"),
    ("conversation.team.assign_any", "Atribuir conversas a qualquer time"),
    ("conversation.team.read_any", "Ler conversas privadas de qualquer time"),
]
_GESTOR_GRANTS = {"team.manage", "conversation.team.assign", "conversation.team.assign_any"}


def upgrade() -> None:
    conn = op.get_bind()
    duplicate = conn.execute(sa.text(
        "SELECT lower(btrim(name)) FROM teams "
        "GROUP BY lower(btrim(name)) HAVING count(*) > 1 LIMIT 1"
    )).scalar()
    if duplicate is not None:
        raise RuntimeError(
            "Existem times com nomes duplicados (ignorando maiúsculas e espaços). "
            "Renomeie-os antes de aplicar a migration 0070."
        )

    op.add_column("teams", sa.Column(
        "enforce_team_access", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("teams", sa.Column(
        "is_active", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint(
        "ck_teams_private_requires_restricted", "teams",
        "enforce_team_access = 0 OR restrict_visibility = 1")
    op.create_index(
        "uq_teams_name_normalized", "teams", [sa.text("lower(btrim(name))")],
        unique=True)

    existing = {r[0] for r in conn.execute(sa.text("SELECT key FROM permissions"))}
    rows = [{"key": key, "description": description}
            for key, description in _PERMISSIONS if key not in existing]
    if rows:
        op.bulk_insert(
            sa.table("permissions", sa.column("key"), sa.column("description")), rows)

    gestor_id = conn.execute(
        sa.text("SELECT id FROM roles WHERE key = 'gestor'")).scalar()
    if gestor_id is not None:
        permission_ids = {row[0]: row[1] for row in conn.execute(sa.text(
            "SELECT key, id FROM permissions"
        )) if row[0] in _GESTOR_GRANTS}
        existing_grants = {r[0] for r in conn.execute(sa.text(
            "SELECT permission_id FROM role_permissions WHERE role_id = :role_id"
        ), {"role_id": gestor_id})}
        grant_rows = [
            {"role_id": gestor_id, "permission_id": permission_ids[key]}
            for key in _GESTOR_GRANTS
            if key in permission_ids and permission_ids[key] not in existing_grants
        ]
        if grant_rows:
            op.bulk_insert(sa.table(
                "role_permissions", sa.column("role_id"),
                sa.column("permission_id")), grant_rows)


def downgrade() -> None:
    conn = op.get_bind()
    for key, _description in _PERMISSIONS:
        permission_id = conn.execute(sa.text(
            "SELECT id FROM permissions WHERE key = :key"), {"key": key}).scalar()
        if permission_id is not None:
            conn.execute(sa.text(
                "DELETE FROM role_permissions WHERE permission_id = :permission_id"),
                {"permission_id": permission_id})
            conn.execute(sa.text(
                "DELETE FROM user_permissions WHERE permission_id = :permission_id"),
                {"permission_id": permission_id})
            conn.execute(sa.text(
                "DELETE FROM permissions WHERE id = :permission_id"),
                {"permission_id": permission_id})
    op.drop_index("uq_teams_name_normalized", table_name="teams")
    op.drop_constraint("ck_teams_private_requires_restricted", "teams", type_="check")
    op.drop_column("teams", "is_active")
    op.drop_column("teams", "enforce_team_access")
