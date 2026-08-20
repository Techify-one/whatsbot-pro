"""Webhooks de saída (plano "Sistema de API com chave por usuário" · fase 8).

Todo o resto da API é *pull*. Um CRM que precise saber "chegou mensagem",
"conversa resolvida" ou "contato criado" teria de fazer polling — o único push
existente é o ``/ws``, que exige sessão de painel e não é escopado.

O núcleo de entrega é do CORE e assina o MESMO barramento de eventos, então
evento emitido por PLUGIN viaja de graça, sem transporte próprio. Um plugin que
precise de formato de terceiro implementa o seu, sem passar por aqui.

Estado em TABELA, nunca em memória: um toggle de plugin derruba o processo, e
uma entrega pendente não pode morrer com ele.

Revision ID: 0065_outbound_webhooks
Revises: 0064_api_keys
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0065_outbound_webhooks"
down_revision: Union[str, Sequence[str], None] = "0064_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_PERMISSIONS = [
    ("webhook.manage", "Cadastrar/remover webhooks de saída"),
]


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("secret", sa.Text, nullable=False),
        sa.Column("events", JSONB, nullable=False),
        sa.Column("enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Float, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer, nullable=True),
        sa.Column("last_delivery_at", sa.Float, nullable=True),
        sa.Column("last_status", sa.Integer, nullable=True),
        sa.Column("failure_streak", sa.Integer, nullable=False, server_default="0"),
        sa.Column("disabled_reason", sa.Text, nullable=True),
    )
    op.create_index("idx_webhook_endpoints_enabled", "webhook_endpoints", ["enabled"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("endpoint_id", sa.Integer,
                  sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.Float, nullable=False, server_default="0"),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.Float, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("idx_webhook_deliveries_due", "webhook_deliveries",
                    ["status", "next_attempt_at"])
    op.create_index("idx_webhook_deliveries_endpoint", "webhook_deliveries",
                    ["endpoint_id"])

    existing = {r[0] for r in conn.execute(sa.text("SELECT key FROM permissions"))}
    rows = [{"key": k, "description": d}
            for k, d in _NEW_PERMISSIONS if k not in existing]
    if rows:
        op.bulk_insert(
            sa.table("permissions", sa.column("key"), sa.column("description")),
            rows)


def downgrade() -> None:
    conn = op.get_bind()
    for key, _ in _NEW_PERMISSIONS:
        pid = conn.execute(sa.text("SELECT id FROM permissions WHERE key = :k"),
                           {"k": key}).scalar()
        if pid is not None:
            conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p"), {"p": pid})
            conn.execute(sa.text("DELETE FROM user_permissions WHERE permission_id = :p"), {"p": pid})
            conn.execute(sa.text("DELETE FROM permissions WHERE id = :p"), {"p": pid})
    op.drop_index("idx_webhook_deliveries_endpoint", table_name="webhook_deliveries")
    op.drop_index("idx_webhook_deliveries_due", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("idx_webhook_endpoints_enabled", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
