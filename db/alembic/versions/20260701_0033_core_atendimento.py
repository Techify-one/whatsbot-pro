"""rename core conversation tables -> atendimento (daily-interaction entity)

Renames the 5 core conversation tables + their indexes, preserving all rows.
FROZEN (NOT touched): every ``conversation_id`` COLUMN, the ``conversation_event``
message role, ``applies_to="conversation"`` values, all WS/bus/RBAC event names,
the ``uq_conv_display_id`` constraint and ``idx_msg_conversation_ts`` index (on the
un-renamed messages table). These stay as stable internal / plugin-contract tokens.

db/tables.py keeps ``conversations = atendimentos`` (etc.) aliases so the live
``protocolos`` plugin and all legacy imports keep working unchanged.

Guarded + idempotent + reversible.

Revision ID: 0033_core_atendimento
Revises: 0032_plugin_protocolos
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_core_atendimento"           # <=32 chars (alembic_version is VARCHAR(32))
down_revision = "0032_plugin_protocolos"
branch_labels = None
depends_on = None

TABLES = [
    ("conversations",              "atendimentos"),
    ("conversation_counters",      "atendimento_counters"),
    ("conversation_labels",        "atendimento_labels"),
    ("conversation_label_links",   "atendimento_label_links"),
    ("saved_conversation_filters", "saved_atendimento_filters"),
]
INDEXES = [
    ("idx_conv_inbox_status",      "idx_atend_inbox_status"),
    ("idx_conv_assignee_status",   "idx_atend_assignee_status"),
    ("idx_conv_contact",           "idx_atend_contact"),
    ("idx_conv_contact_inbox",     "idx_atend_contact_inbox"),
    ("idx_conv_last_activity",     "idx_atend_last_activity"),
    ("idx_conv_archived",          "idx_atend_archived"),
    ("idx_conv_label_links_label", "idx_atend_label_links_label"),
]


def _rename_indexes(bind, pairs):
    if bind.dialect.name != "postgresql":
        return
    for old, new in pairs:
        op.execute(f'ALTER INDEX IF EXISTS "{old}" RENAME TO "{new}"')


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "conversations" not in tables:
        return  # fresh DB already at new names / already migrated
    for old, new in TABLES:
        if old in tables and new not in tables:
            op.rename_table(old, new)
    _rename_indexes(bind, INDEXES)


def downgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "atendimentos" not in tables:
        return
    if bind.dialect.name == "postgresql":
        for old, new in INDEXES:
            op.execute(f'ALTER INDEX IF EXISTS "{new}" RENAME TO "{old}"')
    for old, new in TABLES:
        if new in tables and old not in tables:
            op.rename_table(new, old)
