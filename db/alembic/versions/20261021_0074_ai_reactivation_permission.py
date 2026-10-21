"""Permissão ``agent.reactivation.manage`` — quem configura o religamento da IA (plano 178).

O card "Automações" (religar a IA automaticamente) devolve a IA a conversas de OUTRAS
pessoas e tira o responsável humano, então tem permissão própria em vez de ir de carona
no ``settings.manage``. Em "Permissões personalizadas" ela aparece no grupo "IA e agente".

A permissão é copiada para quem já tem ``settings.manage`` (``role_permissions`` E
``user_permissions``): o boot só insere a LINHA da permissão e ``ROLE_DEFAULTS`` só vale
para banco novo, então sem a cópia o gestor de um banco já semeado — e todo usuário com
permissões personalizadas — perderia o acesso a uma tela que hoje abre. A partir daí o
administrador concede ou retira por usuário/grupo.

Defensivo/idempotente: insere só o que falta e usa ``ON CONFLICT DO NOTHING``.

Revision ID: 0074_ai_reactivation_perm
Revises: 0073_atendimento_ai_disabled_at
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0074_ai_reactivation_perm"
down_revision: Union[str, Sequence[str], None] = "0073_atendimento_ai_disabled_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERM_KEY = "agent.reactivation.manage"
_PERM_DESC = "IA: configurar o religamento automático da IA"
_SRC_KEY = "settings.manage"

def _perm_ids(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute(sa.text("SELECT key, id FROM permissions"))}

def upgrade() -> None:
    conn = op.get_bind()
    perm_ids = _perm_ids(conn)
    if _PERM_KEY not in perm_ids:
        conn.execute(sa.text(
            "INSERT INTO permissions (key, description) VALUES (:k, :d) "
            "ON CONFLICT (key) DO NOTHING"), {"k": _PERM_KEY, "d": _PERM_DESC})
        perm_ids = _perm_ids(conn)
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

def downgrade() -> None:
    conn = op.get_bind()
    pid = _perm_ids(conn).get(_PERM_KEY)
    if pid is None:
        return
    conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p"), {"p": pid})
    conn.execute(sa.text("DELETE FROM user_permissions WHERE permission_id = :p"), {"p": pid})
    conn.execute(sa.text("DELETE FROM permissions WHERE id = :p"), {"p": pid})
