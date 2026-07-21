"""Preferências de som por usuário (plano 63 F1).

Tabela ``user_sound_prefs``: override ESPARSO do padrão global
``config.sound_settings``. ``user_id`` é NULL no modo aberto (sem identidade de
usuário) → preferência compartilhada naquela instalação. Índice único em
``user_id`` = chave de upsert (o caminho NULL é tratado à mão no repo, pois no
Postgres NULLs são distintos num índice único). Molde: ``saved_conversation_filters``.

Guardada + idempotente + reversível.

Revision ID: 0059_user_sound_prefs
Revises: 0058_merge_p50_p57
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0059_user_sound_prefs"
down_revision: Union[str, Sequence[str], None] = "0058_merge_p50_p57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, table: str) -> bool:
    return sa.inspect(conn).has_table(table)


def _json_type():
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_table(conn, "user_sound_prefs"):
        op.create_table(
            "user_sound_prefs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("prefs", _json_type(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index(
            "ux_user_sound_prefs_user",
            "user_sound_prefs",
            ["user_id"],
            unique=True,
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_table(conn, "user_sound_prefs"):
        op.drop_index("ux_user_sound_prefs_user", table_name="user_sound_prefs")
        op.drop_table("user_sound_prefs")
