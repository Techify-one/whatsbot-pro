"""Cofre de credenciais das tools de IA (plano 177).

Hoje a única casa de uma chave de API usada por uma tool é a config de um
plugin, ou o próprio código da tool (que fica versionado em ``ai_tools_history``).
``ai_variables`` não serve: o valor sai em claro no GET, é reescrito no prompt e
cada edição vira snapshot.

Duas tabelas, separadas de tudo isso:

* ``ai_credentials`` — uma credencial por linha, com vários campos em JSONB
  (``{campo: {"value": ..., "secret": bool}}``). Sem tabela de histórico.
* ``ai_credential_tools`` — vínculo credencial ↔ tool. ``tool_name`` é texto SEM
  FK para ``ai_tools``: excluir e recriar uma tool é rotina e não pode levar o
  vínculo junto.

⚠️ O executor agêntico (plano 51/74) lê o banco por SQL com os papéis
``whatsbot_ro``/``whatsbot_rw``, e o ``pg_default_acl`` do schema faz tabela
nova nascer legível/gravável por eles. Sem o ``REVOKE`` abaixo, "só-escrita na
API" seria só na API. O bloco é guardado por ``pg_roles`` porque em
dev/teste esses papéis não existem.

A permissão ``agent.credentials.manage`` é copiada para quem já tem
``agent.tools.manage`` (``role_permissions`` E ``user_permissions``): o boot só
insere a LINHA da permissão e ``ROLE_DEFAULTS`` só vale para banco novo, então
sem a cópia o gestor de um banco já semeado não a receberia.

Revision ID: 0071_ai_credentials
Revises: 0070_team_access_rbac
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0071_ai_credentials"
down_revision: Union[str, Sequence[str], None] = "0070_team_access_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERM_KEY = "agent.credentials.manage"
_PERM_DESC = "IA: gerenciar credenciais das tools"
_SRC_KEY = "agent.tools.manage"

_VAULT_TABLES = ("ai_credentials", "ai_credential_tools")
_EXECUTOR_ROLES = ("whatsbot_ro", "whatsbot_rw")


def _revoke_executor_access() -> None:
    """Tira das roles do executor agêntico o acesso às tabelas do cofre.

    ``REVOKE ALL`` desfaz o que o ``pg_default_acl`` concedeu no CREATE TABLE.
    Guardado por ``pg_roles``: sem a role (dev/teste) não há nada a revogar e o
    REVOKE falharia.
    """
    tables = ", ".join(f"public.{t}" for t in _VAULT_TABLES)
    for role in _EXECUTOR_ROLES:
        op.execute(sa.text(
            "DO $do$ BEGIN "
            f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"EXECUTE 'REVOKE ALL ON TABLE {tables} FROM {role}'; "
            "END IF; END $do$"
        ))


def _copy_tools_manage_grants(conn) -> None:
    perm_ids = {r[0]: r[1] for r in conn.execute(sa.text("SELECT key, id FROM permissions"))}
    src, dst = perm_ids.get(_SRC_KEY), perm_ids.get(_PERM_KEY)
    if src is None or dst is None:
        return
    conn.execute(sa.text(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT role_id, :dst FROM role_permissions WHERE permission_id = :src "
        "ON CONFLICT DO NOTHING"), {"src": src, "dst": dst})
    conn.execute(sa.text(
        "INSERT INTO user_permissions (user_id, permission_id) "
        "SELECT user_id, :dst FROM user_permissions WHERE permission_id = :src "
        "ON CONFLICT DO NOTHING"), {"src": src, "dst": dst})


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        "ai_credentials",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("fields", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.Column("updated_by", sa.Integer, nullable=True),
        sa.Column("updated_by_name", sa.Text, nullable=True),
    )
    op.create_table(
        "ai_credential_tools",
        sa.Column("credential_name", sa.Text,
                  sa.ForeignKey("ai_credentials.name", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("tool_name", sa.Text, primary_key=True),
    )
    op.create_index("idx_ai_credential_tools_tool", "ai_credential_tools", ["tool_name"])

    _revoke_executor_access()

    existing = {r[0] for r in conn.execute(sa.text("SELECT key FROM permissions"))}
    if _PERM_KEY not in existing:
        op.bulk_insert(
            sa.table("permissions", sa.column("key"), sa.column("description")),
            [{"key": _PERM_KEY, "description": _PERM_DESC}])
    _copy_tools_manage_grants(conn)


def downgrade() -> None:
    conn = op.get_bind()
    pid = conn.execute(sa.text("SELECT id FROM permissions WHERE key = :k"),
                       {"k": _PERM_KEY}).scalar()
    if pid is not None:
        conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p"), {"p": pid})
        conn.execute(sa.text("DELETE FROM user_permissions WHERE permission_id = :p"), {"p": pid})
        conn.execute(sa.text("DELETE FROM permissions WHERE id = :p"), {"p": pid})
    op.drop_index("idx_ai_credential_tools_tool", table_name="ai_credential_tools")
    op.drop_table("ai_credential_tools")
    op.drop_table("ai_credentials")
