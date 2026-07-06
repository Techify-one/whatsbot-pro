"""Seed da permissão ``audit.manage`` na tabela ``permissions``.

A permissão ``audit.manage`` (ativar/desativar a trilha de auditoria) foi
adicionada ao catálogo de código (``domain.permission_catalog``) e ao gate de UI
(``AuditLog.js`` — hide/don't-disable) pela lane ``origin/developer``, mas SEM uma
migration que a inserisse na tabela ``permissions``. Como a tabela é semeada
exclusivamente por migrations (não há sync runtime catálogo→tabela), a chave
ficava no catálogo (aparecia no PermissionPicker) mas **não era atribuível** a
nenhum cargo/usuário — o FK ``role_permissions/user_permissions → permissions.id``
não tinha destino. Este seed completa a feature.

Não concede a chave a nenhum cargo (``server/permissions.py`` não a lista em
ROLE_DEFAULTS — segue admin-only por padrão, via short-circuit '*'); só cria a
linha para que possa ser atribuída pela UI. Idempotente (``ON CONFLICT``).

Revision ID: 0041_seed_audit_manage
Revises: 0040_merge_granular_jsonb
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0041_seed_audit_manage"
down_revision: Union[str, Sequence[str], None] = "0040_merge_granular_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO permissions (key, description) VALUES (:k, :d) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"k": "audit.manage", "d": "Ativar/desativar a trilha de auditoria"},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM permissions WHERE key = :k"),
        {"k": "audit.manage"},
    )
