"""Contatos: coluna ``contact_type`` (tipo do contato por canal de origem).

Cada contato passa a registrar o TIPO herdado do canal que o materializou: o
provider declara o tipo via ``Channel.contact_type()`` (GOWA + WhatsApp Cloud →
``whatsapp``; Telegram → ``telegram``; sem override → ``outros``) e o core grava
no INSERT do contato. A coluna vira uma marca no painel + uma dimensão de filtro.

- ``server_default = 'outros'`` — o default universal (contato sem tipo definido).
- Backfill provider-aware: o produto era WhatsApp-first, então o blanket marca tudo
  ``whatsapp``; EM SEGUIDA os contatos cujo canal é um provider NÃO-WhatsApp são
  corrigidos pelo join ``conversations → inboxes → channels.provider`` (Telegram foi
  bundled no plano 33, então contatos ``telegram`` podem preexistir e não podem
  ficar rotulados ``whatsapp`` — o tipo só é escrito no INSERT, sem self-heal em
  runtime, então um rótulo errado aqui seria permanente). Idempotente.

Revision ID: 0050_contact_type
Revises: 0049_merge_exec_reuse
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0050_contact_type"
down_revision: Union[str, Sequence[str], None] = "0049_merge_exec_reuse"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn) -> set[str]:
    insp = sa.inspect(conn)
    if not insp.has_table("contacts"):
        return set()
    return {c["name"] for c in insp.get_columns("contacts")}


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("contacts"):
        return
    if "contact_type" not in _columns(conn):
        op.add_column(
            "contacts",
            sa.Column("contact_type", sa.Text(), nullable=False,
                      server_default="outros"),
        )
        # Blanket WhatsApp-first (a esmagadora maioria das rows legadas), depois
        # corrige os contatos ligados a um provider NÃO-WhatsApp pelo canal da sua
        # conversa. Telegram é o único não-WhatsApp bundled até aqui; um provider
        # futuro herda o 'whatsapp' do blanket (aceitável — o INSERT em runtime já
        # grava o tipo certo dele; só as rows PRÉ-migração passam por aqui).
        op.execute("UPDATE contacts SET contact_type = 'whatsapp'")
        insp = sa.inspect(conn)
        if all(insp.has_table(t) for t in ("conversations", "inboxes", "channels")):
            op.execute(
                """
                UPDATE contacts SET contact_type = 'telegram'
                WHERE id IN (
                    SELECT DISTINCT cv.contact_id
                    FROM conversations cv
                    JOIN inboxes i  ON i.id = cv.inbox_id
                    JOIN channels ch ON ch.id = i.channel_id
                    WHERE ch.provider = 'telegram'
                )
                """
            )


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table("contacts"):
        return
    if "contact_type" in _columns(conn):
        op.drop_column("contacts", "contact_type")
