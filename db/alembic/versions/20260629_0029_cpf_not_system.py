"""Unlock the built-in CPF custom attribute: flip ``is_system`` 1 → 0.

CPF used to ship as a locked system attribute (no rename/delete, "Sistema"
badge). It now behaves like the other default contact attributes
(Email/Profissão/Empresa/Endereço): fully editable AND deletable, no badge. The
boot seeder already ships it with ``is_system=0`` going forward; this migration
fixes installations that already seeded it locked.

Only the active CPF contact definition is touched — a user who already deleted it
(soft-deleted) keeps it deleted, and any user-created ``cpf`` is left untouched
because the seeder never created a duplicate.

Revision ID: 0029_cpf_not_system
Revises: 0028_default_contact_attrs
Create Date: 2026-06-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0029_cpf_not_system"
down_revision: Union[str, Sequence[str], None] = "0028_default_contact_attrs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from db.tables import custom_attribute_definitions as cad
    op.get_bind().execute(
        sa.update(cad)
        .where(
            cad.c.attribute_key == "cpf",
            cad.c.applies_to == "contact",
            cad.c.is_system == 1,
        )
        .values(is_system=0)
    )


def downgrade() -> None:
    from db.tables import custom_attribute_definitions as cad
    op.get_bind().execute(
        sa.update(cad)
        .where(
            cad.c.attribute_key == "cpf",
            cad.c.applies_to == "contact",
        )
        .values(is_system=1)
    )
