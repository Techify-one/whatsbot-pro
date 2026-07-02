"""Postgres "do zero": ``alembic upgrade head`` num banco LIMPO + FKs enforced.

Plano 29 · Eixo C — repurpose do antigo round-trip SQLite→Postgres (o caminho
SQLite e o ``db/migration_postgres.py`` morreram com o Postgres-only). O que
este módulo agora garante:

* um Postgres recém-criado chega à head do Alembic sem batch-mode (C2);
* a FK ``messages.conversation_id → atendimentos.id ON DELETE CASCADE`` existe
  e DISPARA no Postgres (E3 preservado);
* o índice único parcial do roteador único (0035) materializa.

O banco efêmero ``<test>_fresh`` é criado/derrubado via a conexão admin do
próprio servidor de teste; sem privilégio ``CREATEDB`` o módulo dá skip.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from tests.pg import test_db_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
            "INSERT INTO atendimentos (display_id, inbox_id, contact_id, contact_inbox_id, status, "
            "opened_at, last_activity_at, custom_attributes, created_at, updated_at) "
            "VALUES (90099,:i,:c,:ci,'open',:n,:n,'{}',:n,:n)"
        ), {"i": ib, "c": cid, "ci": ci, "n": now})
        conv = conn.execute(text("SELECT id FROM atendimentos WHERE display_id=90099")).scalar()

        for role, body in (("user", "rt hi"), ("assistant", "rt ok")):
            conn.execute(text(
                "INSERT INTO messages (contact_id, role, content, ts, conversation_id) "
                "VALUES (:c,:r,:b,:t,:cv)"
            ), {"c": cid, "r": role, "b": body, "t": now, "cv": conv})
    return conv, cid


@pytest.fixture(scope="module")
def fresh_pg():
    """CREATE DATABASE ``<test>_fresh`` → alembic head → yield engine → DROP.

    O upgrade roda com o engine global TEMPORARIAMENTE apontado pro banco fresh
    (o env.py do Alembic reusa o engine do app), restaurado no teardown — mesmo
    padrão save/restore do antigo fixture ``sqlite_source``.
    """
    from alembic import command
    from db import engine as engine_module

    base = make_url(test_db_url())
    fresh_name = f"{base.database}_fresh"
    admin = create_engine(
        base.set(database="postgres"), future=True,
        isolation_level="AUTOCOMMIT", connect_args={"prepare_threshold": None})
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{fresh_name}'")
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{fresh_name}"')
            conn.exec_driver_sql(f'CREATE DATABASE "{fresh_name}"')
    except Exception as e:
        admin.dispose()
        pytest.skip(f"sem privilégio para criar o banco efêmero no PG de teste: {e}")

    saved_engine = engine_module._engine
    saved_url = engine_module._db_url

    url = base.set(database=fresh_name).render_as_string(hide_password=False)
    eng = engine_module.init_engine(url)
    command.upgrade(_alembic_cfg(eng), "head")

    try:
        yield eng
    finally:
        eng.dispose()
        engine_module._engine = saved_engine
        engine_module._db_url = saved_url
        try:
            with admin.connect() as conn:
                conn.exec_driver_sql(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{fresh_name}'")
                conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{fresh_name}"')
        finally:
            admin.dispose()


def test_fresh_pg_reaches_head_with_fks(fresh_pg):
    """Postgres limpo chega à head; FKs de messages materializam com CASCADE."""
    insp = inspect(fresh_pg)
    fks = insp.get_foreign_keys("messages")
    by_col = {tuple(f["constrained_columns"]): f for f in fks}
    conv_fk = by_col.get(("conversation_id",))
    assert conv_fk is not None, "conversation_id FK missing on fresh Postgres"
    assert conv_fk["referred_table"] == "atendimentos"
    assert (conv_fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE"
    contact_fk = by_col.get(("contact_id",))
    assert contact_fk is not None and contact_fk["referred_table"] == "contacts"

    with fresh_pg.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version, "alembic_version vazio após upgrade head"


def test_fresh_pg_has_single_router_index(fresh_pg):
    """O índice único parcial do roteador único (migration 0035) existe."""
    insp = inspect(fresh_pg)
    names = {ix["name"] for ix in insp.get_indexes("ai_agents")}
    assert "ux_ai_agents_single_router" in names


def test_cascade_fires_on_postgres(fresh_pg):
    """ON DELETE CASCADE dispara nativamente no Postgres."""
    conv_id, _cid = _seed_one_conversation_with_messages(fresh_pg)
    with fresh_pg.connect() as conn:
        n_before = conn.execute(
            text("SELECT COUNT(*) FROM messages WHERE conversation_id = :cv"),
            {"cv": conv_id}).scalar()
    assert n_before == 2
    with fresh_pg.begin() as conn:
        conn.execute(text("DELETE FROM atendimentos WHERE id = :cv"), {"cv": conv_id})
    with fresh_pg.connect() as conn:
        n_after = conn.execute(
            text("SELECT COUNT(*) FROM messages WHERE conversation_id = :cv"),
            {"cv": conv_id}).scalar()
    assert n_after == 0, "ON DELETE CASCADE did not remove the conversation's messages"
