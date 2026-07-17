"""merge plano50 (paginacao/teto) na developer

Revision ID: 3faf187705a3
Revises: 0052_message_edited_ts, 0056_unbind_ai_off_agent
Create Date: 2026-07-17 14:40:06.475189+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3faf187705a3'
down_revision: Union[str, Sequence[str], None] = ('0052_message_edited_ts', '0056_unbind_ai_off_agent')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
