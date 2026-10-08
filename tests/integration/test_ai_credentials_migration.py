"""Plano 177 · F1 — migration ``0071_ai_credentials`` (round-trip e grants).

Roda o Alembic de verdade contra o Postgres de TESTE: ``downgrade`` para a
revisão anterior, estado semeado, ``upgrade`` e conferência. O ``finally``
devolve o banco ao head, porque a engine é global ao processo.

O que se prova:
  * ``upgrade → downgrade → upgrade`` sem erro;
  * quem tem ``agent.tools.manage`` ganha ``agent.credentials.manage`` —
    em ``role_permissions`` E ``user_permissions`` — e quem não tem NÃO ganha;
  * o ``downgrade`` leva a permissão e os grants embora;
  * a migration roda onde as roles ``whatsbot_ro``/``whatsbot_rw`` NÃO existem
    (dev/teste): o ``REVOKE`` é guardado por ``pg_roles``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from db.engine import get_engine
from tests.paths import PROJECT_ROOT

PREV = "0070_team_access_rbac"
HEAD_REV = "0071_ai_credentials"
SRC = "agent.tools.manage"
DST = "agent.credentials.manage"


def _cfg() -> Config:
    cfg = Config(str(Path(PROJECT_ROOT) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(PROJECT_ROOT) / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url).replace("%", "%%"))
    return cfg


def _tables() -> set[str]:
    with get_engine().connect() as conn:
        return {r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename LIKE 'ai_credential%'"))}


def _perm_id(conn, key):
    return conn.execute(text("SELECT id FROM permissions WHERE key = :k"),
                        {"k": key}).scalar()


def _role_has(conn, role_key, perm_key) -> bool:
    return bool(conn.execute(text(
        "SELECT 1 FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
        "JOIN permissions p ON p.id = rp.permission_id "
        "WHERE r.key = :r AND p.key = :p"), {"r": role_key, "p": perm_key}).first())


@pytest.fixture
def at_head(_engine_ready):
    """Garante o head antes e DEPOIS: o round-trip mexe no banco compartilhado."""
    cfg = _cfg()
    command.upgrade(cfg, "head")
    try:
        yield cfg
    finally:
        command.upgrade(cfg, "head")


def test_this_migration_is_in_the_applied_chain(at_head):
    """Não exige que seja o head (uma migration futura pode vir depois): exige que
    a revisão esteja na cadeia aplicada e que as tabelas existam."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(at_head)
    with get_engine().connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    chain = {rev.revision for rev in script.walk_revisions(base="base", head=current)}
    assert HEAD_REV in chain
    assert _tables() == {"ai_credentials", "ai_credential_tools"}


def test_round_trip_up_down_up(at_head):
    command.downgrade(at_head, PREV)
    assert _tables() == set()
    with get_engine().connect() as conn:
        assert _perm_id(conn, DST) is None
    command.upgrade(at_head, "head")
    assert _tables() == {"ai_credentials", "ai_credential_tools"}
    with get_engine().connect() as conn:
        assert _perm_id(conn, DST) is not None


def test_upgrade_copies_tools_manage_grants_and_only_those(at_head):
    command.downgrade(at_head, PREV)
    email_a = f"mig-a-{uuid.uuid4().hex}@test.local"
    email_b = f"mig-b-{uuid.uuid4().hex}@test.local"
    try:
        with get_engine().begin() as conn:
            src = _perm_id(conn, SRC)
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
            gestor_had = _role_has(conn, "gestor", SRC)
            atendente_had = _role_has(conn, "atendente", SRC)

        command.upgrade(at_head, "head")

        with get_engine().connect() as conn:
            dst = _perm_id(conn, DST)
            assert dst is not None
            # papéis: quem tinha tools.manage ganha; atendente (que não tinha) não
            assert _role_has(conn, "gestor", DST) is gestor_had
            assert _role_has(conn, "atendente", DST) is atendente_had
            assert atendente_had is False        # o seed não dá tools.manage ao atendente
            # usuários (custom): só o que tinha tools.manage
            got = {r[0] for r in conn.execute(text(
                "SELECT user_id FROM user_permissions WHERE permission_id = :p "
                "AND user_id IN (:a, :b)"), {"p": dst, "a": uid_with, "b": uid_without})}
            assert got == {uid_with}
    finally:
        with get_engine().begin() as conn:
            conn.execute(text("DELETE FROM users WHERE email IN (:a, :b)"),
                         {"a": email_a, "b": email_b})


def test_downgrade_removes_permission_and_every_grant(at_head):
    with get_engine().connect() as conn:
        assert _perm_id(conn, DST) is not None
    command.downgrade(at_head, PREV)
    with get_engine().connect() as conn:
        assert _perm_id(conn, DST) is None
        # nenhum grant órfão apontando para uma permissão que não existe mais
        assert conn.execute(text(
            "SELECT count(*) FROM role_permissions rp LEFT JOIN permissions p "
            "ON p.id = rp.permission_id WHERE p.id IS NULL")).scalar() == 0


def test_runs_where_the_executor_roles_do_not_exist(at_head):
    """Em dev/teste ``whatsbot_ro``/``whatsbot_rw`` não existem; sem o guarda de
    ``pg_roles`` o REVOKE falharia e a migration não subiria."""
    with get_engine().connect() as conn:
        roles = conn.execute(text(
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('whatsbot_ro','whatsbot_rw')")
        ).scalar()
    if roles:
        pytest.skip("este banco de teste tem as roles do executor; o caso 'sem roles' não se aplica")
    command.downgrade(at_head, PREV)
    command.upgrade(at_head, "head")          # não pode levantar
    assert _tables() == {"ai_credentials", "ai_credential_tools"}


def test_revoke_block_targets_both_vault_tables_and_both_roles():
    """Sem as roles no banco de teste o REVOKE nunca executa — então trave o TEXTO:
    se alguém tirar uma tabela ou uma role da lista, este teste acusa."""
    src = (Path(PROJECT_ROOT) / "db" / "alembic" / "versions"
           / "20261008_0071_ai_credentials.py").read_text(encoding="utf-8")
    assert '_VAULT_TABLES = ("ai_credentials", "ai_credential_tools")' in src
    assert '_EXECUTOR_ROLES = ("whatsbot_ro", "whatsbot_rw")' in src
    assert "REVOKE ALL ON TABLE" in src
    assert "pg_roles" in src
    # o REVOKE vem DEPOIS do CREATE TABLE (a ACL padrão só existe depois dele)
    assert src.index("op.create_table(") < src.index("_revoke_executor_access()\n\n    existing")
