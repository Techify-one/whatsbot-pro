"""RBAC: users/roles/permissions/role_permissions/user_roles/user_sessions (plano 03 Fase 1)

Data foundation only — no enforcement yet (Fases 2-3 add hashing/sessions/middleware
additively, keeping the single-password auth working). Seeds the 3 system roles, the
16-permission catalog, and the default role_permissions matrix. admin is a
short-circuit (bypass), so it is NOT seeded into role_permissions.

Revision ID: 0012_rbac_users
Revises: 0011_channels
Create Date: 2026-06-20

NOTE (P82): down_revision is the real head at implementation time.
Catalog is inlined here (migration snapshot); server/permissions.py is the runtime
source of truth. created_at in epoch float (P56).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_rbac_users"
down_revision: Union[str, Sequence[str], None] = "0011_channels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_TS = 1750000000.0

_ROLES = [
    ("admin", "Administrador", 1),
    ("gestor", "Gestor", 1),
    ("atendente", "Atendente", 1),
]

_PERMISSIONS = [
    ("conversation.read", "Ler conversas dos inboxes em que é membro"),
    ("conversation.read_all", "Ler conversas de qualquer inbox (ignora membership)"),
    ("conversation.reply", "Responder conversa"),
    ("conversation.assign", "Atribuir/transferir conversa"),
    ("conversation.resolve", "Encerrar/reabrir conversa"),
    ("contact.read", "Ler dados de contato"),
    ("contact.write", "Editar dados de contato"),
    ("inbox.manage", "Criar/editar inboxes e membros"),
    ("channel.manage", "Configurar canais/números"),
    ("settings.manage", "Configurações globais"),
    ("plugins.manage", "Ativar/desativar/configurar plugins"),
    ("billing.manage", "Recargas/saldo (Techify)"),
    ("agent.manage", "Prompt/modelo/tools do agente"),
    ("quickreply.manage", "Respostas rápidas"),
    ("users.manage", "Criar/editar/desativar usuários e papéis"),
    ("audit.read", "Ler trilha de auditoria"),
]

_ROLE_DEFAULTS = {
    "gestor": {
        "conversation.read", "conversation.reply", "conversation.assign",
        "conversation.resolve", "contact.read", "contact.write", "channel.manage",
        "settings.manage", "plugins.manage", "billing.manage", "agent.manage",
        "quickreply.manage", "audit.read",
    },
    "atendente": {
        "conversation.read", "conversation.reply", "conversation.resolve",
        "contact.read", "quickreply.manage",
    },
}


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_login_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_users_email", "users", ["email"])

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer(),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_id", sa.Integer(),
                  sa.ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.Integer(),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
    )
    op.create_index("idx_sessions_user", "user_sessions", ["user_id"])
    op.create_index("idx_sessions_expires", "user_sessions", ["expires_at"])

    # ── Seed ──────────────────────────────────────────────────────────
    roles_t = sa.table("roles", sa.column("id"), sa.column("key"),
                       sa.column("name"), sa.column("is_system"), sa.column("created_at"))
    perms_t = sa.table("permissions", sa.column("id"), sa.column("key"),
                       sa.column("description"))
    rp_t = sa.table("role_permissions", sa.column("role_id"), sa.column("permission_id"))

    op.bulk_insert(roles_t, [
        {"key": k, "name": n, "is_system": s, "created_at": _SEED_TS}
        for k, n, s in _ROLES
    ])
    op.bulk_insert(perms_t, [
        {"key": k, "description": d} for k, d in _PERMISSIONS
    ])

    conn = op.get_bind()
    role_ids = {r.key: r.id for r in conn.execute(sa.text("SELECT id, key FROM roles"))}
    perm_ids = {p.key: p.id for p in conn.execute(sa.text("SELECT id, key FROM permissions"))}
    rows = []
    for role_key, perm_keys in _ROLE_DEFAULTS.items():
        rid = role_ids[role_key]
        for pk in perm_keys:
            rows.append({"role_id": rid, "permission_id": perm_ids[pk]})
    if rows:
        op.bulk_insert(rp_t, rows)


def downgrade() -> None:
    op.drop_index("idx_sessions_expires", table_name="user_sessions")
    op.drop_index("idx_sessions_user", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
