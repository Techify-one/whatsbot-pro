"""rename atendimentos plugin -> protocolos (existing-install reconciliation)

Renames the LIVE plugin_atendimentos_* schema, config keys, RBAC catalog rows,
and registry rows to the new `protocolos` id, preserving all data. Guarded +
idempotent: no-op on fresh installs (nothing to rename) and safe to re-run.

Grants live in role_permissions/user_permissions keyed by permission_id (numeric),
so renaming permissions.key IN-PLACE preserves every grant automatically.

Revision ID: 0032_plugin_protocolos
Revises: 0031_ai_agent_prompt_history
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_plugin_protocolos"          # <=32 chars (alembic_version is VARCHAR(32))
down_revision = "0031_ai_agent_prompt_history"
branch_labels = None
depends_on = None

# --- table renames (old -> new) ---------------------------------------------
TABLES = [
    ("plugin_atendimentos_atendimentos",       "plugin_protocolos_protocolos"),
    ("plugin_atendimentos_conversas",          "plugin_protocolos_atendimentos"),
    ("plugin_atendimentos_campos_extras",      "plugin_protocolos_campos_extras"),
    ("plugin_atendimentos_atendimento_extras", "plugin_protocolos_protocolo_extras"),
    ("plugin_atendimentos_kanban_views",       "plugin_protocolos_kanban_views"),
    ("plugin_atendimentos_user_view_prefs",    "plugin_protocolos_user_view_prefs"),
]
# --- column renames: (new_table_name, old_col, new_col) ----------------------
COLUMNS = [
    ("plugin_protocolos_atendimentos",     "atendimento_id", "protocolo_id"),  # was conversas.atendimento_id (FK->main)
    ("plugin_protocolos_campos_extras",    "conversa_id",    "atendimento_id"),
    ("plugin_protocolos_protocolo_extras", "atendimento_id", "protocolo_id"),
]
# --- index renames (old -> new); IF EXISTS covers 002-dropped indexes --------
INDEXES = [
    ("plugin_atendimentos_one_open_per_contact", "plugin_protocolos_one_open_per_contact"),
    ("plugin_atendimentos_atend_contact",        "plugin_protocolos_proto_contact"),
    ("plugin_atendimentos_atend_status",         "plugin_protocolos_proto_status"),
    ("plugin_atendimentos_conv_unique",          "plugin_protocolos_atend_unique"),
    ("plugin_atendimentos_conv_conv",            "plugin_protocolos_atend_atend"),
    ("plugin_atendimentos_conv_atend",           "plugin_protocolos_atend_proto"),
    ("plugin_atendimentos_campos_extras_uniq",   "plugin_protocolos_campos_extras_uniq"),
    ("plugin_atendimentos_campos_extras_conv",   "plugin_protocolos_campos_extras_atend"),
    ("plugin_atendimentos_atend_extras_uniq",    "plugin_protocolos_proto_extras_uniq"),
    ("plugin_atendimentos_atend_extras_owner",   "plugin_protocolos_proto_extras_owner"),
    ("plugin_atendimentos_kv_scope",             "plugin_protocolos_kv_scope"),
    ("plugin_atendimentos_kv_owner",             "plugin_protocolos_kv_owner"),
    ("plugin_atendimentos_kv_pos",               "plugin_protocolos_kv_pos"),
    ("plugin_atendimentos_uvp_uniq",             "plugin_protocolos_uvp_uniq"),
    ("plugin_atendimentos_uvp_user",             "plugin_protocolos_uvp_user"),
    ("plugin_atendimentos_uvp_view",             "plugin_protocolos_uvp_view"),
]


def _rename_indexes(bind, pairs):
    """Best-effort index rename. Postgres: ALTER INDEX IF EXISTS. SQLite: no
    ALTER INDEX -> table rename carries indexes under old names (harmless);
    skip. Never fatal (index names are not referenced by runtime code)."""
    if bind.dialect.name != "postgresql":
        return
    for old, new in pairs:
        op.execute(f'ALTER INDEX IF EXISTS "{old}" RENAME TO "{new}"')


def _config_rekey(old_key, new_key):
    op.execute(
        sa.text("UPDATE config SET key=:n WHERE key=:o AND NOT EXISTS "
                "(SELECT 1 FROM config c2 WHERE c2.key=:n)")
        .bindparams(o=old_key, n=new_key)
    )


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "plugin_atendimentos_atendimentos" not in tables:
        return  # fresh install / already migrated -> nothing to reconcile

    # 1) rename tables (guarded: skip any whose target already exists)
    for old, new in TABLES:
        if old in tables and new not in tables:
            op.rename_table(old, new)

    # 2) rename entity columns
    for tbl, oldc, newc in COLUMNS:
        cols = {c["name"] for c in insp.get_columns(tbl)} if tbl in set(sa.inspect(bind).get_table_names()) else set()
        if oldc in cols and newc not in cols:
            op.alter_column(tbl, oldc, new_column_name=newc)

    # 3) rename indexes (best-effort, Postgres only)
    _rename_indexes(bind, INDEXES)

    # 4) config keys: blanket prefix, then ordered scope-suffix rotation
    op.execute(
        "UPDATE config SET key = 'plugin.protocolos.' || substr(key, length('plugin.atendimentos.')+1) "
        "WHERE key LIKE 'plugin.atendimentos.%'"
    )
    #   scope rotation (order matters: free the *_atendimento slot before writing it)
    _config_rekey("plugin.protocolos.field_defs_atendimento",     "plugin.protocolos.field_defs_protocolo")
    _config_rekey("plugin.protocolos.field_defs_conversa",        "plugin.protocolos.field_defs_atendimento")
    _config_rekey("plugin.protocolos.fixed_required_atendimento", "plugin.protocolos.fixed_required_protocolo")
    _config_rekey("plugin.protocolos.fixed_required_conversa",    "plugin.protocolos.fixed_required_atendimento")

    # 5) RBAC catalog: rename key + plugin_id + group_label IN-PLACE
    #    (grants reference permission_id -> preserved automatically)
    op.execute(
        "UPDATE permissions SET "
        "key = 'plugin.protocolos.' || substr(key, length('plugin.atendimentos.')+1), "
        "plugin_id = 'protocolos', group_label = 'Protocolos' "
        "WHERE plugin_id = 'atendimentos'"
    )

    # 6) registry: plugins row id + plugin_migrations rows (marks 001-009 applied)
    op.execute("UPDATE plugins SET id='protocolos' WHERE id='atendimentos' "
               "AND NOT EXISTS (SELECT 1 FROM plugins p2 WHERE p2.id='protocolos')")
    op.execute("UPDATE plugin_migrations SET plugin_id='protocolos' WHERE plugin_id='atendimentos'")
    # if a racing discovery re-created an empty 'atendimentos' plugins row, drop it
    op.execute("DELETE FROM plugins WHERE id='atendimentos'")


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "plugin_protocolos_protocolos" not in tables:
        return

    op.execute("UPDATE plugin_migrations SET plugin_id='atendimentos' WHERE plugin_id='protocolos'")
    op.execute("UPDATE plugins SET id='atendimentos' WHERE id='protocolos'")
    op.execute(
        "UPDATE permissions SET "
        "key = 'plugin.atendimentos.' || substr(key, length('plugin.protocolos.')+1), "
        "plugin_id = 'atendimentos', group_label = 'Atendimentos' "
        "WHERE plugin_id = 'protocolos'"
    )
    # reverse scope rotation (reverse order)
    _config_rekey("plugin.protocolos.fixed_required_atendimento", "plugin.protocolos.fixed_required_conversa")
    _config_rekey("plugin.protocolos.fixed_required_protocolo",   "plugin.protocolos.fixed_required_atendimento")
    _config_rekey("plugin.protocolos.field_defs_atendimento",     "plugin.protocolos.field_defs_conversa")
    _config_rekey("plugin.protocolos.field_defs_protocolo",       "plugin.protocolos.field_defs_atendimento")
    op.execute(
        "UPDATE config SET key = 'plugin.atendimentos.' || substr(key, length('plugin.protocolos.')+1) "
        "WHERE key LIKE 'plugin.protocolos.%'"
    )
    if bind.dialect.name == "postgresql":
        for old, new in INDEXES:
            op.execute(f'ALTER INDEX IF EXISTS "{new}" RENAME TO "{old}"')
    for tbl, oldc, newc in COLUMNS:
        cols = {c["name"] for c in insp.get_columns(tbl)}
        if newc in cols and oldc not in cols:
            op.alter_column(tbl, newc, new_column_name=oldc)
    for old, new in TABLES:
        if new in tables and old not in tables:
            op.rename_table(new, old)
