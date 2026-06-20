"""Trilha de auditoria: tabela audit_log append-only + índices (plano 07 Fase 0)

Aditivo. Registra "quem fez o quê, quando, sobre qual recurso, antes/depois".
FK para users.id é LÓGICA (sem ForeignKey/cascade — a trilha sobrevive à
remoção do usuário). before/after são TEXT JSON mascarados na ingestão. Não há
UPDATE/DELETE por id no repo — só purge() de retenção.

Revision ID: 0017_audit_log
Revises: 0016_routing_steps_hooks
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_audit_log"
down_revision: Union[str, Sequence[str], None] = "0016_routing_steps_hooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False, server_default="system"),
        sa.Column("actor_label", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("idx_audit_created", "audit_log", ["created_at"])
    op.create_index("idx_audit_actor", "audit_log", ["actor_user_id", "created_at"])
    op.create_index("idx_audit_resource", "audit_log", ["resource_type", "resource_id"])
    op.create_index("idx_audit_action", "audit_log", ["action", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_audit_action", table_name="audit_log")
    op.drop_index("idx_audit_resource", table_name="audit_log")
    op.drop_index("idx_audit_actor", table_name="audit_log")
    op.drop_index("idx_audit_created", table_name="audit_log")
    op.drop_table("audit_log")
