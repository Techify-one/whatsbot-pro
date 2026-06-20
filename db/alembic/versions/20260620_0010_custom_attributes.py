"""custom attributes (plano 05) — definitions table + contacts.custom_attributes

First NATIVE JSON/JSONB column in the project. ``custom_attribute_definitions``
holds metadata (key, type, scope, options…); values live in a per-entity JSON
column. This plan owns the contact scope; conversations.custom_attributes is
owned by plano 01 (also _json_type()/JSONB, never TEXT).

Revision ID: 0010_custom_attributes
Revises: 0009_quick_replies
Create Date: 2026-06-20

NOTE (P82): down_revision is the real head at implementation time — 0009_quick_replies
was added just before this. Chains linearly; never reuse a consumed slot.
Written by hand (autogenerate does not handle with_variant/JSON server_default).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010_custom_attributes"
down_revision: Union[str, Sequence[str], None] = "0009_quick_replies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "custom_attribute_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attribute_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False, server_default="text"),
        sa.Column("applies_to", sa.Text(), nullable=False),
        sa.Column("options", _json(), nullable=True),
        sa.Column("required", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("regex_pattern", sa.Text(), nullable=True),
        sa.Column("regex_cue", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("deleted_at", sa.Float(), nullable=True),
        sa.UniqueConstraint("attribute_key", "applies_to", name="uq_attr_key_scope"),
    )
    op.create_index("idx_cad_applies_to", "custom_attribute_definitions", ["applies_to"])
    op.add_column(
        "contacts",
        sa.Column("custom_attributes", _json(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("contacts", "custom_attributes")
    op.drop_index("idx_cad_applies_to", table_name="custom_attribute_definitions")
    op.drop_table("custom_attribute_definitions")
