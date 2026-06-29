"""SQLite → Postgres round-trip safety for the schema (Plano 23 · Fase E3).

E3 reconciled ``db/tables.py`` ↔ the real schema by adding the FK
``messages.conversation_id → conversations.id ON DELETE CASCADE`` (migration
``0027``). Because the cross-backend copy in ``db/migration_postgres.py`` walks
``metadata.sorted_tables`` (FK-topological order) and Postgres ENFORCES FKs at
insert time, a regression in the FK or the copy ordering would surface here as a
``ForeignKeyViolation`` during the copy.

This module is SELF-CONTAINED: it builds its OWN temp SQLite source engine,
points the module-global engine at it (``migrate_sqlite_to_postgres`` reads the
source via ``db.engine.get_engine()``), runs the migration, then restores the
process-global engine the rest of the suite shares. It does NOT depend on the
``conftest`` session engine/seed.

* The SQLite leg ALWAYS runs: it proves ``alembic upgrade head`` materializes the
  ``conversation_id`` FK and that the FK-topological copy order places
  ``messages`` after ``conversations``/``contacts`` (so a parent-before-child
  insert is possible).
* The Postgres leg runs ONLY when ``WHATSBOT_TEST_DB_URL`` is a Postgres URL
  (CI / dev PG). It exercises the FULL ``migrate_sqlite_to_postgres`` and asserts
  the FK exists on the target, the rows copied, and ON DELETE CASCADE fires.

Run as part of the normal suite (``pytest``); the PG leg auto-skips with no PG.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_PG_URL = os.environ.get("WHATSBOT_TEST_DB_URL", "").strip()
_PG_AVAILABLE = _PG_URL.startswith(("postgresql://", "postgresql+", "postgres://", "postgres+"))


def _alembic_cfg(engine):
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url).replace("%", "%%"))
    return cfg


def _seed_one_conversation_with_messages(engine) -> tuple[int, int]:
    """Insert contact → inbox → contact_inbox → conversation → 2 messages.

    Hermetic SQL (no repo side-effects). Returns ``(conversation_id, contact_id)``.
    """
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO contacts (phone, name, email, profession, company, address, "
            "created_at, updated_at) VALUES ('5511000000099','RT','','','','',:n,:n)"
        ), {"n": now})
        cid = conn.execute(text("SELECT id FROM contacts WHERE phone='5511000000099'")).scalar()

        conn.execute(text(
            "INSERT INTO inboxes (name, channel_type, agent_bot_enabled, created_at, updated_at) "
            "VALUES ('RT inbox','whatsapp',1,:n,:n)"
        ), {"n": now})
        ib = conn.execute(text("SELECT id FROM inboxes ORDER BY id DESC LIMIT 1")).scalar()

        conn.execute(text(
            "INSERT INTO contact_inboxes (contact_id, inbox_id, source_id, created_at, updated_at) "
            "VALUES (:c,:i,'5511000000099@s.whatsapp.net',:n,:n)"
        ), {"c": cid, "i": ib, "n": now})
        ci = conn.execute(text("SELECT id FROM contact_inboxes ORDER BY id DESC LIMIT 1")).scalar()

        conn.execute(text(
            "INSERT INTO conversations (display_id, inbox_id, contact_id, contact_inbox_id, status, "
            "opened_at, last_activity_at, custom_attributes, created_at, updated_at) "
            "VALUES (90099,:i,:c,:ci,'open',:n,:n,'{}',:n,:n)"
        ), {"i": ib, "c": cid, "ci": ci, "n": now})
        conv = conn.execute(text("SELECT id FROM conversations WHERE display_id=90099")).scalar()

        for role, body in (("user", "rt hi"), ("assistant", "rt ok")):
            conn.execute(text(
                "INSERT INTO messages (contact_id, role, content, ts, conversation_id) "
                "VALUES (:c,:r,:b,:t,:cv)"
            ), {"c": cid, "r": role, "b": body, "t": now, "cv": conv})
    return conv, cid


@pytest.fixture
def sqlite_source():
    """Build a fresh temp SQLite DB at HEAD and make it the module-global engine.

    Saves/restores ``db.engine`` globals so the session-shared engine survives.
    """
    from alembic import command
    from db import engine as engine_module

    saved_engine = engine_module._engine
    saved_url = engine_module._db_url
    saved_sqlite = engine_module._sqlite_path

    tmp = tempfile.mkdtemp(prefix="wb_rt_")
    db_path = Path(tmp) / "whatsbot.db"
    src = engine_module.init_engine(f"sqlite:///{db_path}")
    command.upgrade(_alembic_cfg(src), "head")

    try:
        yield src
    finally:
        src.dispose()
        engine_module._engine = saved_engine
        engine_module._db_url = saved_url
        engine_module._sqlite_path = saved_sqlite


def test_sqlite_head_has_conversation_fk(sqlite_source):
    """alembic upgrade head materializes the conversation_id FK on SQLite."""
    with sqlite_source.connect() as conn:
        fks = conn.execute(text("PRAGMA foreign_key_list(messages)")).mappings().all()
    by_col = {f["from"]: (f["table"], f["on_delete"]) for f in (dict(r) for r in fks)}
    assert "conversation_id" in by_col, "FK on messages.conversation_id missing"
    assert by_col["conversation_id"] == ("conversations", "CASCADE")
    # The legacy contact_id FK must survive the batch table-recreate.
    assert by_col.get("contact_id") == ("contacts", "CASCADE")


def test_copy_order_places_messages_after_parents():
    """The cross-backend copy walks FK-topological order: messages AFTER its
    parents (contacts, conversations) so parent rows exist before the child
    insert under FK enforcement (Postgres)."""
    from db.tables import metadata

    order = [t.name for t in metadata.sorted_tables]
    assert order.index("messages") > order.index("conversations")
    assert order.index("messages") > order.index("contacts")


@pytest.mark.skipif(not _PG_AVAILABLE, reason="WHATSBOT_TEST_DB_URL is not a Postgres URL")
def test_full_roundtrip_to_postgres(sqlite_source):
    """End-to-end: seed SQLite, migrate to a fresh PG schema, assert FK + cascade.

    Uses ``force_drop=True`` to wipe the target's public schema first so the test
    is repeatable against the dev PG.
    """
    from db.migration_postgres import migrate_sqlite_to_postgres
    from sqlalchemy import create_engine

    conv_id, _cid = _seed_one_conversation_with_messages(sqlite_source)

    progress = migrate_sqlite_to_postgres(_PG_URL, force_drop=True)
    assert progress.stage == "done", f"migration failed: {progress.error}"

    target = create_engine(_PG_URL, future=True)
    try:
        # The FK exists on the target with CASCADE.
        insp = inspect(target)
        fks = insp.get_foreign_keys("messages")
        conv_fk = next((f for f in fks if f["constrained_columns"] == ["conversation_id"]), None)
        assert conv_fk is not None, "conversation_id FK missing on Postgres target"
        assert conv_fk["referred_table"] == "conversations"
        assert (conv_fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE"

        with target.connect() as conn:
            n_before = conn.execute(
                text("SELECT COUNT(*) FROM messages WHERE conversation_id = :cv"),
                {"cv": conv_id},
            ).scalar()
        assert n_before == 2, "messages did not copy under the conversation"

        # ON DELETE CASCADE fires on Postgres (FK enforced natively).
        with target.begin() as conn:
            conn.execute(text("DELETE FROM conversations WHERE id = :cv"), {"cv": conv_id})
        with target.connect() as conn:
            n_after = conn.execute(
                text("SELECT COUNT(*) FROM messages WHERE conversation_id = :cv"),
                {"cv": conv_id},
            ).scalar()
        assert n_after == 0, "ON DELETE CASCADE did not remove the conversation's messages"
    finally:
        target.dispose()
