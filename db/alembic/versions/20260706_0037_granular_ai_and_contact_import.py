"""IA granular + contact.import: split agent.manage e nova permissão de import.

Substitui o genérico ``agent.manage`` ("Prompt/modelo/tools do agente") por quatro
permissões granulares de IA e adiciona ``contact.import`` (importar lista de
contatos por CSV, hoje protegido por ``contact.write``).

Preserva o acesso atual: **quem tinha a chave-fonte ganha as chaves-destino**
(em ``role_permissions`` E ``user_permissions``), respeitando customizações — um
admin que tenha revogado ``agent.manage`` de um cargo NÃO recebe as novas de IA.
Depois de copiar os grants, ``agent.manage`` é removido (FK ``ON DELETE CASCADE``
limpa os grants órfãos; removemos explicitamente por segurança).

Defensivo/idempotente: insere só o que falta e usa ``ON CONFLICT DO NOTHING``, então
é seguro em banco compartilhado. server/permissions.py é a fonte de verdade em
runtime; isto é o snapshot de seed (espelha o padrão de 0021/0032).

Revision ID: 0037_granular_ai_perms
Revises: 0036_atend_open_unique
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0037_granular_ai_perms"
down_revision: Union[str, Sequence[str], None] = "0036_atend_open_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AI_KEYS = [
    "agent.config.manage",
    "agent.prompts.manage",
    "agent.tools.manage",
    "agent.variables.manage",
]

_NEW_PERMISSIONS = [
    ("contact.import", "Importar lista de contatos (CSV)"),
    ("agent.config.manage", "IA: configuração do agente (modelo, tools, roteamento)"),
    ("agent.prompts.manage", "IA: editar prompts do agente"),
    ("agent.tools.manage", "IA: gerenciar tools (código e overrides)"),
    ("agent.variables.manage", "IA: gerenciar variáveis"),
]

# (chave-fonte, [chaves-destino]) — copia os grants existentes da fonte p/ destino.
_GRANT_MIGRATIONS = [
    ("agent.manage", _AI_KEYS),
    ("contact.write", ["contact.import"]),
]

_OLD_KEY = "agent.manage"
_OLD_KEY_DESC = "Prompt/modelo/tools do agente"


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
    for src, dsts in _GRANT_MIGRATIONS:
        _copy_grants(conn, perm_by_key, src, dsts)
    _drop_perm(conn, perm_by_key, _OLD_KEY)


def downgrade() -> None:
    conn = op.get_bind()
    perm_by_key = _insert_missing(conn, [(_OLD_KEY, _OLD_KEY_DESC)])
    # Reconstitui agent.manage p/ quem detém qualquer uma das granulares de IA.
    _copy_grants(conn, perm_by_key, "agent.config.manage", [_OLD_KEY])
    for key, _ in _NEW_PERMISSIONS:
        _drop_perm(conn, perm_by_key, key)
