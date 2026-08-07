"""Schema drift: ``db/tables.py`` metadata vs the REAL post-``upgrade head`` schema.

Regression guard for the migration-0034 "janela paralela" incident (plano 29): the
dev DB recorded a head without applying ``0034_message_sent_by``, so
``messages.sent_by_*`` existed in the metadata but not in the database — every
full ``select(messages)`` 500'd. This test runs Alembic's autogenerate comparison
against the migrated TEST database and fails on ANY divergence, both directions:

* column/table in metadata missing from the DB (the 0034 shape — a migration that
  never ran, or a ``Table`` edit without a migration);
* index/column in the DB missing from metadata (an object created out-of-band —
  the ``uq_atend_open_contact_inbox`` shape, manually applied during plano 28 and
  only materialized in migration 0036).

``plugin_<id>_*`` tables are owned by plugin migrations (not core metadata) and
are filtered out, as is Alembic's own ``alembic_version``.
"""

from __future__ import annotations


def _diff_object_name(diff) -> str:
    """Best-effort table name an autogenerate diff refers to (for filtering)."""
    if isinstance(diff, list):  # modify_* come grouped in a list
        diff = diff[0]
    kind = diff[0]
    if kind in ("add_table", "remove_table"):
        return diff[1].name
    if kind in ("add_column", "remove_column"):
        return diff[2]
    if kind in ("add_index", "remove_index", "add_constraint",
                "remove_constraint", "add_fk", "remove_fk"):
        obj = diff[1]
        return getattr(getattr(obj, "table", None), "name", "") or ""
    if kind.startswith("modify_"):
        return diff[2]
    return ""


def test_metadata_matches_migrated_schema(_engine_ready):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from db.engine import get_engine
    from db.tables import metadata

    with get_engine().connect() as conn:
        ctx = MigrationContext.configure(conn)
        diffs = compare_metadata(ctx, metadata)

    # "plugin_" is the plugin-OWNED table prefix — but core tables like
    # ``plugin_migrations`` (declared in db/tables.py) also match it, so only
    # names ABSENT from the core metadata are treated as plugin-owned.
    core_tables = set(metadata.tables)

    def _plugin_owned(name: str) -> bool:
        return name.startswith("plugin_") and name not in core_tables

    core_diffs = [
        d for d in diffs
        if not _plugin_owned(str(_diff_object_name(d)))
        and str(_diff_object_name(d)) != "alembic_version"
    ]
    assert not core_diffs, (
        "db/tables.py and the post-'alembic upgrade head' schema diverged:\n  - "
        + "\n  - ".join(repr(d) for d in core_diffs)
        + "\nEvery schema change needs BOTH the Table edit and a linear migration "
        "(and DB-only objects must be declared in db/tables.py)."
    )
