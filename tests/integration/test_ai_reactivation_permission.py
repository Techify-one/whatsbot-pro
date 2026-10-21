"""Plano 178 — a permissão ``agent.reactivation.manage`` (religar a IA automaticamente).

O card "Automações" devolve a IA a conversas de OUTRAS pessoas e tira o responsável
humano, então não basta o ``settings.manage`` que abre a aba:

* ``PUT /api/config`` exige ``agent.reactivation.manage`` para as chaves
  ``ai_reactivation_*`` — inclusive de quem tem ``settings.manage``;
* sem ela essas chaves são DESCARTADAS (a tela salva tudo junto) e o resto grava;
* um corpo com SÓ essas chaves, sem a permissão, é 403;
* a migration ``0074`` dá a permissão a quem já tinha ``settings.manage`` (papéis E
  usuários customizados), senão o gestor de um banco já semeado perderia a tela;
* ela aparece no grupo "IA e agente" do catálogo.

    venv/bin/python -m pytest tests/integration/test_ai_reactivation_permission.py -q
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from db.engine import get_engine
from db.repositories import config_repo, session_repo, user_repo
from domain.permission_catalog import PERMISSION_CATALOG, group_for
from server.auth import generate_session_token
from tests.paths import PROJECT_ROOT

PERM = "agent.reactivation.manage"
PREV = "0073_atendimento_ai_disabled_at"
HEAD_REV = "0074_ai_reactivation_perm"

_DEFAULTS = {
    "ai_reactivation_enabled": False, "ai_reactivation_unit": "hours",
    "ai_reactivation_value": 24, "ai_reactivation_weekdays": [0, 1, 2, 3, 4],
    "ai_reactivation_time": "08:00", "ai_reactivation_last_sender": "both",
    "ai_reactivation_include_groups_archived": False, "ai_reactivation_timezone": "",
}
_ON = {"ai_reactivation_enabled": True, "ai_reactivation_value": 6}

# ── catálogo ────────────────────────────────────────────────────────────────

def test_permission_is_in_the_catalog_under_the_ai_group():
    assert PERM in {k for k, _ in PERMISSION_CATALOG}
    assert group_for(PERM) == ("core", "IA e agente")

def test_gestor_gets_it_by_default_but_atendente_does_not():
    from server.permissions import ROLE_DEFAULTS
    assert PERM in ROLE_DEFAULTS["gestor"]
    assert PERM not in ROLE_DEFAULTS["atendente"]

# ── rota ────────────────────────────────────────────────────────────────────

def _login(client, *, role_keys=None, permission_keys=None):
    custom = permission_keys is not None
    user = user_repo.create(
        email=f"react-{uuid.uuid4().hex}@test.local", name="React Tester",
        password_hash="test-only", role_keys=role_keys,
        permission_keys=permission_keys, custom=custom)
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    return user, token

@pytest.fixture
def make_client(client):
    made: list[tuple[dict, str]] = []

    def _make(**kw):
        user, token = _login(client, **kw)
        made.append((user, token))
        client.headers["Authorization"] = f"Bearer {token}"
        return client

    yield _make
    client.headers.pop("Authorization", None)
    for user, token in made:
        session_repo.delete(token)
        user_repo.delete(user["id"])

@pytest.fixture(autouse=True)
def _reset(_engine_ready):
    config_repo.set_many(_DEFAULTS)
    config_repo.set("auto_reply", True)
    yield
    config_repo.set_many(_DEFAULTS)
    config_repo.set("auto_reply", True)

def _stored(key):
    return config_repo.get(key)

def test_admin_can_write_the_rule(make_client):
    c = make_client(role_keys=["admin"])
    assert c.put("/api/config", json=_ON).status_code == 200
    assert _stored("ai_reactivation_enabled") is True
    assert _stored("ai_reactivation_value") == 6

def test_gestor_can_write_the_rule(make_client):
    """O papel gestor tem ``settings.manage`` + a nova (migration/seed)."""
    c = make_client(role_keys=["gestor"])
    assert c.put("/api/config", json=_ON).status_code == 200
    assert _stored("ai_reactivation_enabled") is True

def test_settings_manage_alone_is_not_enough(make_client):
    """O ponto da permissão: o genérico NÃO basta. Corpo só com as chaves ⇒ 403."""
    c = make_client(permission_keys=["settings.manage"])
    r = c.put("/api/config", json=_ON)
    assert r.status_code == 403, r.text
    assert _stored("ai_reactivation_enabled") is False

def test_without_the_permission_the_rest_of_a_mixed_save_still_applies(make_client):
    """A tela salva tudo junto: as chaves do religamento caem, o resto grava."""
    c = make_client(permission_keys=["settings.manage"])
    r = c.put("/api/config", json={**_ON, "auto_reply": False})
    assert r.status_code == 200, r.text
    assert _stored("auto_reply") is False
    assert _stored("ai_reactivation_enabled") is False        # descartada
    assert _stored("ai_reactivation_value") == 24

def test_the_permission_alone_can_write_only_the_rule(make_client):
    c = make_client(permission_keys=[PERM])
    assert c.put("/api/config", json=_ON).status_code == 200
    assert _stored("ai_reactivation_enabled") is True
    # …mas não ganha o resto de graça: auto_reply exige settings.manage.
    r = c.put("/api/config", json={**_ON, "ai_reactivation_value": 9, "auto_reply": False})
    assert r.status_code == 200
    assert _stored("ai_reactivation_value") == 9
    assert _stored("auto_reply") is True

def test_a_user_with_neither_is_denied(make_client):
    c = make_client(permission_keys=["conversation.read"])
    assert c.put("/api/config", json=_ON).status_code == 403
    assert _stored("ai_reactivation_enabled") is False

def test_everyone_can_still_read_the_rule(make_client):
    """Somente leitura: o GET não exige a permissão (a tela mostra o card travado)."""
    config_repo.set("ai_reactivation_enabled", True)
    c = make_client(permission_keys=["settings.manage"])
    data = c.get("/api/config").json()["data"]
    assert data["ai_reactivation_enabled"] is True

def test_open_install_without_users_can_still_configure(client):
    """Sem identidade (instalação aberta, antes do 1º usuário) tudo é permitido."""
    client.headers.pop("Authorization", None)
    r = client.put("/api/config", json=_ON)
    assert r.status_code == 200
    assert _stored("ai_reactivation_enabled") is True

# ── migration ───────────────────────────────────────────────────────────────

def _cfg() -> Config:
    cfg = Config(str(Path(PROJECT_ROOT) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(PROJECT_ROOT) / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url).replace("%", "%%"))
    return cfg

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
    email_a = f"mig-a-{uuid.uuid4().hex}@test.local"
    email_b = f"mig-b-{uuid.uuid4().hex}@test.local"
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
            # quem tinha settings.manage (papéis) mantém o acesso à tela; quem não, não
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
    """Rodar duas vezes (ou com a linha já criada pelo boot) não duplica nem falha."""
    command.downgrade(at_head, PREV)
    with get_engine().begin() as conn:
        # o boot (``sync_core_permissions``) pode ter inserido a linha antes da migration
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
