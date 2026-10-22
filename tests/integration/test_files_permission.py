"""Plano 179 — permissão ``files.manage`` e migrations 0075–0077 (tela Arquivos).

* a chave está no catálogo, no grupo "Configurações e sistema" e no gestor (não no atendente);
* a migration ``0077`` copia o grant de ``settings.manage`` (papéis E usuários), é
  idempotente e o downgrade apaga a linha e todos os grants;
* ``0075`` cria ``messages.media_state``/``media_name`` e ``0076`` o índice parcial
  ``idx_msg_media_path`` — e voltam limpos.

    venv/bin/python -m pytest tests/integration/test_files_permission.py -q
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from db.engine import get_engine
from domain.permission_catalog import PERMISSION_CATALOG, group_for
from tests.paths import PROJECT_ROOT

PERM = "files.manage"
PREV = "0076_msg_media_path_idx"
HEAD_REV = "0077_files_permission"


def test_permission_is_in_the_catalog_under_settings_group():
    assert PERM in {k for k, _ in PERMISSION_CATALOG}
    assert group_for(PERM) == ("core", "Configurações e sistema")


def test_gestor_gets_it_by_default_but_atendente_does_not():
    from server.permissions import ROLE_DEFAULTS
    assert PERM in ROLE_DEFAULTS["gestor"]
    assert PERM not in ROLE_DEFAULTS["atendente"]


def _cfg() -> Config:
    cfg = Config(str(Path(PROJECT_ROOT) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(PROJECT_ROOT) / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url).replace("%", "%%"))
    return cfg


def _perm_id(conn, key):
    return conn.execute(text("SELECT id FROM permissions WHERE key = :k"), {"k": key}).scalar()


def _role_has(conn, role_key, perm_key) -> bool:
    return bool(conn.execute(text(
        "SELECT 1 FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
        "JOIN permissions p ON p.id = rp.permission_id "
        "WHERE r.key = :r AND p.key = :p"), {"r": role_key, "p": perm_key}).first())


@pytest.fixture
def at_head(_engine_ready):
    cfg = _cfg()
    command.upgrade(cfg, "head")
    try:
        yield cfg
    finally:
        command.upgrade(cfg, "head")


def test_migration_is_in_the_applied_chain(at_head):
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(at_head)
    with get_engine().connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert HEAD_REV in {r.revision for r in script.walk_revisions(base="base", head=current)}


def test_round_trip_up_down_up(at_head):
    command.downgrade(at_head, PREV)
    with get_engine().connect() as conn:
        assert _perm_id(conn, PERM) is None
    command.upgrade(at_head, "head")
    with get_engine().connect() as conn:
        assert _perm_id(conn, PERM) is not None


def test_upgrade_copies_settings_manage_grants_and_only_those(at_head):
    command.downgrade(at_head, PREV)
    email_a = f"files-a-{uuid.uuid4().hex}@test.local"
    email_b = f"files-b-{uuid.uuid4().hex}@test.local"
    try:
        with get_engine().begin() as conn:
            src = _perm_id(conn, "settings.manage")
            other = _perm_id(conn, "agent.variables.manage")
            uid_with, uid_without = [
                conn.execute(text(
                    "INSERT INTO users (email, name, password_hash, is_active, "
                    "custom_permissions, created_at, updated_at) "
                    "VALUES (:e, 'm', 'x', 1, 1, 0, 0) RETURNING id"), {"e": e}).scalar()
                for e in (email_a, email_b)]
            conn.execute(text("INSERT INTO user_permissions (user_id, permission_id) "
                              "VALUES (:u, :p)"), {"u": uid_with, "p": src})
            conn.execute(text("INSERT INTO user_permissions (user_id, permission_id) "
                              "VALUES (:u, :p)"), {"u": uid_without, "p": other})
            gestor_had = _role_has(conn, "gestor", "settings.manage")
            atendente_had = _role_has(conn, "atendente", "settings.manage")

        command.upgrade(at_head, "head")

        with get_engine().connect() as conn:
            dst = _perm_id(conn, PERM)
            assert dst is not None
            assert _role_has(conn, "gestor", PERM) is gestor_had is True
            assert _role_has(conn, "atendente", PERM) is atendente_had is False
            got = {r[0] for r in conn.execute(text(
                "SELECT user_id FROM user_permissions WHERE permission_id = :p "
                "AND user_id IN (:a, :b)"), {"p": dst, "a": uid_with, "b": uid_without})}
            assert got == {uid_with}
    finally:
        with get_engine().begin() as conn:
            conn.execute(text("DELETE FROM users WHERE email IN (:a, :b)"),
                         {"a": email_a, "b": email_b})


def test_upgrade_is_idempotent(at_head):
    command.downgrade(at_head, PREV)
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO permissions (key, description) VALUES (:k, 'x') "
                          "ON CONFLICT (key) DO NOTHING"), {"k": PERM})
    command.upgrade(at_head, "head")
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM permissions WHERE key = :k"),
                            {"k": PERM}).scalar() == 1


def test_downgrade_removes_permission_and_every_grant(at_head):
    command.downgrade(at_head, PREV)
    with get_engine().connect() as conn:
        assert _perm_id(conn, PERM) is None
        assert conn.execute(text(
            "SELECT count(*) FROM role_permissions rp LEFT JOIN permissions p "
            "ON p.id = rp.permission_id WHERE p.id IS NULL")).scalar() == 0


# ── 0075 / 0076 ─────────────────────────────────────────────────────────────

def _columns(conn):
    return {r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'messages'"))}


def _has_index(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT 1 FROM pg_indexes WHERE tablename = 'messages' AND indexname = 'idx_msg_media_path'"
    )).first())


def test_media_columns_and_index_round_trip(at_head):
    with get_engine().connect() as conn:
        assert {"media_state", "media_name"} <= _columns(conn)
        assert _has_index(conn)
    command.downgrade(at_head, "0074_ai_reactivation_perm")
    with get_engine().connect() as conn:
        cols = _columns(conn)
        assert "media_state" not in cols and "media_name" not in cols
        assert not _has_index(conn)
    command.upgrade(at_head, "head")
    with get_engine().connect() as conn:
        assert {"media_state", "media_name"} <= _columns(conn)
        assert _has_index(conn)


def test_index_is_partial_on_media_path(at_head):
    with get_engine().connect() as conn:
        definition = conn.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_msg_media_path'")).scalar()
    assert "media_path IS NOT NULL" in definition
