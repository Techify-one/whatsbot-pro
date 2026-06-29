"""Migrate Email/Profissão/Empresa/Endereço from fixed contact columns into the
``custom_attributes`` JSON, so they become managed custom attributes (seeded as
editable/deletable defaults by ``db.system_attributes``).

For each contact, any non-empty ``email/profession/company/address`` column value
is copied into ``custom_attributes[<key>]`` UNLESS that key is already present
(idempotent; never clobbers a value the user/AI already wrote into the JSON). The
legacy columns are left in place (harmless) — the app now reads/writes these four
through ``custom_attributes`` exclusively.

Revision ID: 0028_default_contact_attrs
Revises: 0027_messages_conversation_fk
Create Date: 2026-06-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0028_default_contact_attrs"
down_revision: Union[str, Sequence[str], None] = "0027_messages_conversation_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FIELDS = ("email", "profession", "company", "address")


def upgrade() -> None:
    # Use the real contacts Table so the JSON column (de)serializes correctly on
    # both SQLite and Postgres. Only the long-stable columns id/email/profession/
    # company/address/custom_attributes are referenced, so this is safe to run on
    # any DB at-or-after this revision.
    from db.tables import contacts
    bind = op.get_bind()
    rows = bind.execute(sa.select(
        contacts.c.id, contacts.c.email, contacts.c.profession,
        contacts.c.company, contacts.c.address, contacts.c.custom_attributes,
    )).mappings().all()

    for row in rows:
        attrs = row["custom_attributes"]
        if not isinstance(attrs, dict):
            attrs = {}
        merged = dict(attrs)
        changed = False
        for key in _FIELDS:
            val = (row[key] or "").strip() if row[key] is not None else ""
            if val and key not in merged:
                merged[key] = val
                changed = True
        if changed:
            bind.execute(
                sa.update(contacts)
                .where(contacts.c.id == row["id"])
                .values(custom_attributes=merged)
            )


def downgrade() -> None:
    # Data migration is not reversed: the legacy columns were never dropped, so
    # the original values remain intact for rollback.
    pass
