"""Team access modes, routing state and RBAC permissions.

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
from sqlalchemy.dialects.postgresql import JSONB


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
    op.add_column("teams", sa.Column(
        "routing_mode", sa.Text(), nullable=False, server_default="manual"))
    op.add_column("teams", sa.Column("default_user_id", sa.Integer(), nullable=True))
    op.add_column("teams", sa.Column("default_agent_key", sa.Text(), nullable=True))
    op.add_column("teams", sa.Column(
        "ai_assignable", sa.Integer(), nullable=False, server_default="0"))
    op.create_foreign_key(
        "fk_teams_default_user_id_users", "teams", "users",
        ["default_user_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(
        "fk_teams_default_agent_key_ai_agents", "teams", "ai_agents",
        ["default_agent_key"], ["agent_key"], ondelete="SET NULL")
    op.create_check_constraint(
        "ck_teams_private_requires_restricted", "teams",
        "enforce_team_access = 0 OR restrict_visibility = 1")
    op.create_check_constraint(
        "ck_teams_routing_mode", "teams",
        "routing_mode IN ('manual', 'round_robin', 'fixed_user', 'fixed_ai')")
    op.create_check_constraint(
        "ck_teams_routing_user_target", "teams",
        "default_user_id IS NULL OR routing_mode = 'fixed_user'")
    op.create_check_constraint(
        "ck_teams_routing_agent_target", "teams",
        "default_agent_key IS NULL OR routing_mode = 'fixed_ai'")
    op.create_check_constraint(
        "ck_teams_ai_assignable_bool", "teams", "ai_assignable IN (0, 1)")
    op.create_index(
        "uq_teams_name_normalized", "teams", [sa.text("lower(btrim(name))")],
        unique=True)

    op.create_table(
        "team_routing_state",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("last_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_team_routing_state_team",
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["inbox_id"], ["inboxes.id"], name="fk_team_routing_state_inbox",
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["last_user_id"], ["users.id"],
            name="fk_team_routing_state_last_user", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("team_id", "inbox_id"),
    )
    op.create_index(
        "idx_team_routing_state_last_user", "team_routing_state", ["last_user_id"])

    op.add_column(
        "ai_agents", sa.Column("routing_team_ids", JSONB(), nullable=True))

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
    op.drop_column("ai_agents", "routing_team_ids")
    op.drop_index("idx_team_routing_state_last_user", table_name="team_routing_state")
    op.drop_table("team_routing_state")
    op.drop_index("uq_teams_name_normalized", table_name="teams")
    op.drop_constraint("ck_teams_ai_assignable_bool", "teams", type_="check")
    op.drop_constraint("ck_teams_routing_agent_target", "teams", type_="check")
    op.drop_constraint("ck_teams_routing_user_target", "teams", type_="check")
    op.drop_constraint("ck_teams_routing_mode", "teams", type_="check")
    op.drop_constraint("ck_teams_private_requires_restricted", "teams", type_="check")
    op.drop_constraint(
        "fk_teams_default_agent_key_ai_agents", "teams", type_="foreignkey")
    op.drop_constraint(
        "fk_teams_default_user_id_users", "teams", type_="foreignkey")
    op.drop_column("teams", "ai_assignable")
    op.drop_column("teams", "default_agent_key")
    op.drop_column("teams", "default_user_id")
    op.drop_column("teams", "routing_mode")
    op.drop_column("teams", "is_active")
    op.drop_column("teams", "enforce_team_access")
