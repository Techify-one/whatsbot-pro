"""Cursor do round-robin passa a ser UM por time (era por time + inbox).

O rodízio é entre os membros do TIME, não entre as caixas de entrada: a mesma
rodada percorre todas as inboxes. A elegibilidade continua filtrando pela inbox
da conversa (quem não é da caixa é pulado), mas o cursor deixa de ser repartido.

``team_routing_state`` tinha PK ``(team_id, inbox_id)``. Esta migration:

* colapsa as linhas existentes em UMA por time, ficando com a mais recente
  (``updated_at``, desempate pela maior ``inbox_id``) — o cursor é só estado de
  justiça, então perder as demais é inofensivo;
* remove ``inbox_id`` (e com ele a FK para ``inboxes``) e refaz a PK em
  ``team_id``.

O ``downgrade`` não tem como reconstruir um cursor por inbox: esvazia a tabela
(a próxima rodada reinicia no primeiro elegível) e restaura o shape antigo.

Revision ID: 0072_team_rr_cursor
Revises: 0071_ai_credentials
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0072_team_rr_cursor"
down_revision: Union[str, Sequence[str], None] = "0071_ai_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Uma linha por time: a mais recente fica, as outras saem.
    op.execute(sa.text(
        "DELETE FROM team_routing_state a USING team_routing_state b "
        "WHERE a.team_id = b.team_id AND ("
        "a.updated_at < b.updated_at OR "
        "(a.updated_at = b.updated_at AND a.inbox_id < b.inbox_id))"))
    op.execute(sa.text(
        "ALTER TABLE team_routing_state "
        "DROP CONSTRAINT IF EXISTS team_routing_state_pkey"))
    op.drop_constraint(
        "fk_team_routing_state_inbox", "team_routing_state", type_="foreignkey")
    op.drop_column("team_routing_state", "inbox_id")
    op.create_primary_key(
        "team_routing_state_pkey", "team_routing_state", ["team_id"])

def downgrade() -> None:
    op.execute(sa.text("DELETE FROM team_routing_state"))
    op.execute(sa.text(
        "ALTER TABLE team_routing_state "
        "DROP CONSTRAINT IF EXISTS team_routing_state_pkey"))
    op.add_column(
        "team_routing_state", sa.Column("inbox_id", sa.Integer(), nullable=False))
    op.create_foreign_key(
        "fk_team_routing_state_inbox", "team_routing_state", "inboxes",
        ["inbox_id"], ["id"], ondelete="CASCADE")
    op.create_primary_key(
        "team_routing_state_pkey", "team_routing_state", ["team_id", "inbox_id"])
