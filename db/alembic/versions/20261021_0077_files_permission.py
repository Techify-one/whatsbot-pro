"""Permissão ``files.manage`` — tela Arquivos (plano 179).

Ver, baixar e apagar a mídia das conversas. Apagar é irreversível e atinge
conversas de outras pessoas (a bolha vira "Arquivo removido" para todos), então tem
permissão própria em vez de ir de carona no ``settings.manage``.

A permissão é copiada para quem já tem ``settings.manage`` (``role_permissions`` E
``user_permissions``): o boot só insere a LINHA e ``ROLE_DEFAULTS`` só vale para
banco novo, então sem a cópia o gestor de um banco já semeado — e todo usuário com
permissões personalizadas — não veria a tela. Mesmo padrão da 0074.

Defensivo/idempotente: insere só o que falta e usa ``ON CONFLICT DO NOTHING``.

Revision ID: 0077_files_permission
Revises: 0076_msg_media_path_idx
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0077_files_permission"
down_revision: Union[str, Sequence[str], None] = "0076_msg_media_path_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERM_KEY = "files.manage"
_PERM_DESC = "Arquivos: ver, baixar e apagar mídia de conversas"
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
