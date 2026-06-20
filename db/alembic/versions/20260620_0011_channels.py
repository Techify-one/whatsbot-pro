"""channels + channel_credentials (plano 02 Fase 0) + 1-default-channel migration

Core channel tables. The current single-number install becomes channel
``default`` (provider ``gowa``). Credentials are TEXT in the MVP (P15 — masked at
the API edge, not encrypted at rest).

Revision ID: 0011_channels
Revises: 0010_custom_attributes
Create Date: 2026-06-20

NOTE (P82): down_revision is the real head at implementation time.
created_at/updated_at are epoch floats (P56), diverging from the DDL's TEXT for
consistency with the rest of the schema.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_channels"
down_revision: Union[str, Sequence[str], None] = "0010_custom_attributes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed timestamp for the seeded default channel (Date.now is unavailable at
# migration authoring; a constant epoch is fine for a one-time backfill row).
_SEED_TS = 1750000000.0


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gowa_device_id", sa.Text(), nullable=True),
        sa.Column("gowa_isolation", sa.Text(), nullable=False, server_default="shared"),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column("connected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("logged_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("own_phone", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "channel_credentials",
        sa.Column("channel_id", sa.Text(),
                  sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.UniqueConstraint("channel_id", "key", name="uq_channel_credentials"),
    )

    # Data migration: the existing install becomes the "default" gowa channel.
    channels = sa.table(
        "channels",
        sa.column("id", sa.Text), sa.column("provider", sa.Text),
        sa.column("display_name", sa.Text), sa.column("enabled", sa.Integer),
        sa.column("gowa_device_id", sa.Text), sa.column("gowa_isolation", sa.Text),
        sa.column("connected", sa.Integer), sa.column("logged_in", sa.Integer),
        sa.column("created_at", sa.Float), sa.column("updated_at", sa.Float),
    )
    op.bulk_insert(channels, [{
        "id": "default", "provider": "gowa", "display_name": "WhatsApp",
        "enabled": 1, "gowa_device_id": "whatsbot", "gowa_isolation": "shared",
        "connected": 0, "logged_in": 0,
        "created_at": _SEED_TS, "updated_at": _SEED_TS,
    }])


def downgrade() -> None:
    op.drop_table("channel_credentials")
    op.drop_table("channels")
