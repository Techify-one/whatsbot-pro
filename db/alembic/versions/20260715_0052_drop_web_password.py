"""Drop the retired legacy panel-password config rows (plano 48).

The single-password scheme (``web_password_hash`` / ``web_password_salt``, SHA-256
+ deterministic token) was retired — login is RBAC-only (email + senha) and the
API/WS gate closes on ``has_users``. These two ``config`` rows have no readers
left; delete them. Non-destructive to RBAC/users/data. Idempotent (a fresh install
never seeds them, so the DELETE is a no-op there).

Revision ID: 0052_drop_web_password
Revises: 0051_fix_conversation_origin
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0052_drop_web_password"
down_revision: Union[str, Sequence[str], None] = "0051_fix_conversation_origin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM config WHERE key IN ('web_password_hash', 'web_password_salt')"
    ))


def downgrade() -> None:
    # Re-create the (empty) rows so a downgrade round-trips. The value is the
    # JSON-encoded empty string, matching how config_repo persisted them.
    op.execute(sa.text(
        "INSERT INTO config (key, value) VALUES "
        "('web_password_hash', '\"\"'), ('web_password_salt', '\"\"') "
        "ON CONFLICT (key) DO NOTHING"
    ))
