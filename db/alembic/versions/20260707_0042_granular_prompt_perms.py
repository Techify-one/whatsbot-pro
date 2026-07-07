"""Prompt do agente granular: split agent.prompts.manage em edit/version/delete.

Substitui a permissão genérica ``agent.prompts.manage`` ("editar prompts do
agente", que cobria editar + versionar + apagar versões) por três permissões
granulares do prompt do agente:

- ``agent.prompts.edit``    — editar/salvar o prompt do agente.
- ``agent.prompts.version`` — versionar (histórico, comparar, restaurar, renomear).
- ``agent.prompts.delete``  — apagar versões do histórico do prompt.

Preserva o acesso atual: **quem tinha a chave-fonte ganha as três chaves-destino**
(em ``role_permissions`` E ``user_permissions``), respeitando customizações — um
admin que tenha revogado ``agent.prompts.manage`` de um cargo NÃO recebe as novas.
Depois de copiar os grants, ``agent.prompts.manage`` é removido.

Defensivo/idempotente: insere só o que falta e usa ``ON CONFLICT DO NOTHING``, então
é seguro em banco compartilhado. server/permissions.py é a fonte de verdade em
runtime; isto é o snapshot de seed (espelha o padrão de 0037).

Revision ID: 0042_granular_prompt_perms
Revises: 0041_seed_audit_manage
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0042_granular_prompt_perms"
down_revision: Union[str, Sequence[str], None] = "0041_seed_audit_manage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROMPT_KEYS = [
    "agent.prompts.edit",
    "agent.prompts.version",
    "agent.prompts.delete",
]

_NEW_PERMISSIONS = [
    ("agent.prompts.edit", "IA: editar o prompt do agente"),
    ("agent.prompts.version", "IA: versionar o prompt (histórico, comparar, restaurar)"),
    ("agent.prompts.delete", "IA: apagar versões do histórico do prompt"),
]

_OLD_KEY = "agent.prompts.manage"
_OLD_KEY_DESC = "IA: editar prompts do agente"


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
    """Copia os grants (role + user) da permissão ``src_key`` p/ cada ``dst_keys``."""
    src_id = perm_by_key.get(src_key)
    if src_id is None:
        return
    dst_ids = [perm_by_key[k] for k in dst_keys if k in perm_by_key]
    if not dst_ids:
        return
    role_ids = [r[0] for r in conn.execute(
        sa.text("SELECT role_id FROM role_permissions WHERE permission_id = :p"),
        {"p": src_id})]
    for rid in role_ids:
        for did in dst_ids:
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "VALUES (:r, :p) ON CONFLICT DO NOTHING"), {"r": rid, "p": did})
    user_ids = [r[0] for r in conn.execute(
        sa.text("SELECT user_id FROM user_permissions WHERE permission_id = :p"),
        {"p": src_id})]
    for uid in user_ids:
        for did in dst_ids:
            conn.execute(sa.text(
                "INSERT INTO user_permissions (user_id, permission_id) "
                "VALUES (:u, :p) ON CONFLICT DO NOTHING"), {"u": uid, "p": did})


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
    _copy_grants(conn, perm_by_key, _OLD_KEY, _PROMPT_KEYS)
    _drop_perm(conn, perm_by_key, _OLD_KEY)


def downgrade() -> None:
    conn = op.get_bind()
    perm_by_key = _insert_missing(conn, [(_OLD_KEY, _OLD_KEY_DESC)])
    # Reconstitui agent.prompts.manage p/ quem detém qualquer uma das granulares.
    for key in _PROMPT_KEYS:
        _copy_grants(conn, perm_by_key, key, [_OLD_KEY])
    for key in _PROMPT_KEYS:
        _drop_perm(conn, perm_by_key, key)
