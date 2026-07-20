"""merge plano 50 (paginacao/teto) com plano 57 (is_pinned por conversa)

Revision ID: 0058_merge_p50_p57
Revises: 0057_atend_is_pinned, 3faf187705a3
Create Date: 2026-07-20 09:34:50.827507+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0058_merge_p50_p57'
down_revision: Union[str, Sequence[str], None] = ('0057_atend_is_pinned', '3faf187705a3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
