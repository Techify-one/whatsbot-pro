"""Chaves de API por usuário (plano "Sistema de API com chave por usuário").

Três coisas, todas aditivas:

1. **``api_keys``** — a chave é um CRACHÁ novo que resolve para o mesmo
   ``request.state.user`` que uma sessão resolve. O segredo nunca é persistido:
   só o Argon2 ``key_hash``; ``prefix`` é o pedaço público (lookup indexado) e
   ``last4`` existe só para exibição. ``scopes`` nasce NULL e SEM USO (D3 — a
   chave herda as permissões do dono; o escopo é o do USUÁRIO).
2. **``audit_log.api_key_id``** — procedência. O ator continua sendo o usuário
   dono (``actor_user_id``); a coluna diz por qual chave a ação entrou.
3. **``apikey.manage``** — a ÚNICA permissão nova. Governa *emitir/revogar*
   chave, nunca *usar* a API. Admin-only: não é copiada para nenhum papel (o
   admin passa pelo short-circuit de role). A linha em ``permissions`` é inserida
   aqui por higiene — sem ela o grant vira no-op silencioso (o self-heal de boot
   ``rbac_repo.sync_core_permissions`` também cobre, mas depende de reiniciar).

Revision ID: 0064_api_keys
Revises: 0063_msg_media_caption
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0064_api_keys"
down_revision: Union[str, Sequence[str], None] = "0063_msg_media_caption"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_PERMISSIONS = [
    ("apikey.manage", "Emitir/revogar chaves de API"),
]


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.Text, nullable=False, server_default=""),
        sa.Column("key_hash", sa.Text, nullable=False),
        sa.Column("prefix", sa.Text, nullable=False),
        sa.Column("last4", sa.Text, nullable=False, server_default=""),
        sa.Column("scopes", sa.Text, nullable=True),
        sa.Column("created_at", sa.Float, nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.Float, nullable=True),
        sa.Column("expires_at", sa.Float, nullable=True),
        sa.Column("revoked_at", sa.Float, nullable=True),
        sa.Column("created_by", sa.Integer, nullable=True),
    )
    op.create_index("idx_api_keys_user", "api_keys", ["user_id"])
    op.create_index("idx_api_keys_prefix", "api_keys", ["prefix"])

    op.add_column("audit_log", sa.Column("api_key_id", sa.Integer, nullable=True))

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
    op.drop_column("audit_log", "api_key_id")
    op.drop_index("idx_api_keys_prefix", table_name="api_keys")
    op.drop_index("idx_api_keys_user", table_name="api_keys")
    op.drop_table("api_keys")
