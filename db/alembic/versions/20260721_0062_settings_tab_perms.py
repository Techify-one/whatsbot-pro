"""Permissões por ABA de Configurações Gerais + biblioteca de sons importados.

Duas coisas, ambas da mesma entrega (separar Notificações de Sons):

1. **Permissões de aba** — ``settings.general`` / ``settings.advanced`` /
   ``settings.notifications``. Antes as abas eram gateadas pelo genérico
   ``settings.manage``; agora cada uma tem a sua (a aba "Sons" é PESSOAL e não
   exige permissão nenhuma). Preserva o acesso atual: **quem tem
   ``settings.manage`` recebe as três** (em ``role_permissions`` E
   ``user_permissions``), respeitando customizações. ``settings.manage`` NÃO é
   removida — segue liberando tudo (inclusive as outras telas que a usam).

2. **``custom_sounds``** — biblioteca de sons importados pela equipe (nome
   escolhido pelo operador). O arquivo em si vive em ``statics/sounds/``; a linha
   guarda o nome amigável, o arquivo e a origem.

Defensivo/idempotente: insere só o que falta e usa ``ON CONFLICT DO NOTHING``.

Revision ID: 0062_settings_tab_perms
Revises: 0061_user_sound_prefs
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0062_settings_tab_perms"
down_revision: Union[str, Sequence[str], None] = "0061_user_sound_prefs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_PERMISSIONS = [
    ("settings.general", "Configurações: aba Geral (avisos de sistema no chat)"),
    ("settings.advanced", "Configurações: aba Avançado (domínio, execuções, auditoria)"),
    ("settings.notifications", "Configurações: aba Notificações"),
]
_SRC_KEY = "settings.manage"


def _perm_ids(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute(sa.text("SELECT key, id FROM permissions"))}


def _insert_missing(conn, perms) -> dict:
    perm_by_key = _perm_ids(conn)
    perms_t = sa.table("permissions", sa.column("key"), sa.column("description"))
    rows = [{"key": k, "description": d} for k, d in perms if k not in perm_by_key]
    if rows:
        op.bulk_insert(perms_t, rows)
        perm_by_key = _perm_ids(conn)
    return perm_by_key


def _copy_grants(conn, perm_by_key, src_key, dst_keys) -> None:
    """Copia os grants (role + user) de ``src_key`` p/ cada uma de ``dst_keys``."""
    src_id = perm_by_key.get(src_key)
    if src_id is None:
        return
    dst_ids = [perm_by_key[k] for k in dst_keys if k in perm_by_key]
    if not dst_ids:
        return
    for table, owner in (("role_permissions", "role_id"), ("user_permissions", "user_id")):
        owner_ids = [r[0] for r in conn.execute(
            sa.text(f"SELECT {owner} FROM {table} WHERE permission_id = :p"),
            {"p": src_id})]
        for oid in owner_ids:
            for did in dst_ids:
                conn.execute(sa.text(
                    f"INSERT INTO {table} ({owner}, permission_id) "
                    "VALUES (:o, :p) ON CONFLICT DO NOTHING"), {"o": oid, "p": did})


def _drop_perm(conn, perm_by_key, key) -> None:
    pid = perm_by_key.get(key)
    if pid is None:
        return
    conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p"), {"p": pid})
    conn.execute(sa.text("DELETE FROM user_permissions WHERE permission_id = :p"), {"p": pid})
    conn.execute(sa.text("DELETE FROM permissions WHERE id = :p"), {"p": pid})


def upgrade() -> None:
    conn = op.get_bind()
    perm_by_key = _insert_missing(conn, _NEW_PERMISSIONS)
    _copy_grants(conn, perm_by_key, _SRC_KEY, [k for k, _ in _NEW_PERMISSIONS])

    op.create_table(
        "custom_sounds",
        sa.Column("id", sa.Integer, primary_key=True),
        # Nome escolhido pelo operador no import (é o rótulo do seletor de som).
        sa.Column("name", sa.String(80), nullable=False),
        # Arquivo em statics/sounds/ — nome gerado (nunca o do upload).
        sa.Column("filename", sa.String(120), nullable=False),
        sa.Column("mime", sa.String(80), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer, nullable=True),
        sa.Column("created_at", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("ix_custom_sounds_name", "custom_sounds", ["name"])


def downgrade() -> None:
    conn = op.get_bind()
    op.drop_index("ix_custom_sounds_name", table_name="custom_sounds")
    op.drop_table("custom_sounds")
    perm_by_key = _perm_ids(conn)
    for key, _ in _NEW_PERMISSIONS:
        _drop_perm(conn, perm_by_key, key)
