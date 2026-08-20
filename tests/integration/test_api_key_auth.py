"""A chave de API é um CRACHÁ que resolve para o mesmo usuário (fases 3, 4 e 6).

O que esta suíte trava é a tese do plano: feita a resolução no middleware, RBAC,
escopo por inbox, auditoria e o gating das rotas de plugin funcionam **sem
alteração** — a chave "vira o usuário". Cada teste abaixo é um item da §9 do
plano.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from db.repositories import api_key_repo, rbac_repo, session_repo, user_repo
from server import api_keys as keylib
from server.auth import generate_session_token


def _mk_user(*, admin: bool = False, custom_perms=None):
    email = f"apikey-{uuid.uuid4().hex}@test.local"
    user = user_repo.create(
        email=email, name="Integração", password_hash="test-only",
        role_keys=["admin"] if admin else [])
    if custom_perms is not None:
        user_repo.set_custom_permissions(user["id"], list(custom_perms))
    return user_repo.get(user["id"])


def _issue(user_id: int, **over):
    raw, prefix, key_hash = keylib.generate_key()
    row = api_key_repo.create(
        user_id=user_id, label="suite", key_hash=key_hash, prefix=prefix,
        last4=keylib.last4(raw), **over)
    return raw, row


@pytest.fixture
def owner():
    """Usuário dedicado com ``custom_permissions`` — o padrão recomendado §4."""
    keylib._verify_cache.clear()
    user = _mk_user(custom_perms=["contact.read", "conversation.read"])
    yield user
    user_repo.delete(user["id"])


@pytest.fixture
def admin_session(client):
    """Sessão de admin para as rotas de gestão (a chave não se cunha sozinha)."""
    user = _mk_user(admin=True)
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    yield {"user": user, "headers": {"Authorization": f"Bearer {token}"}}
    session_repo.delete(token)
    user_repo.delete(user["id"])


# ── §9.5 — autenticar pela chave ────────────────────────────────────────────

def test_key_authenticates_v1(client, owner):
    raw, _ = _issue(owner["id"])
    r = client.get("/api/v1/contacts", headers={"X-Api-Key": raw})
    assert r.status_code == 200, r.text
    assert "items" in r.json()


def test_no_header_is_401(client, owner):
    _issue(owner["id"])
    r = client.get("/api/v1/contacts")
    assert r.status_code == 401


def test_garbage_key_is_401(client, owner):
    _issue(owner["id"])
    r = client.get("/api/v1/contacts", headers={"X-Api-Key": "wsk_live_dead.beef"})
    assert r.status_code == 401


def test_key_authenticates_the_legacy_panel_api_too(client, owner):
    """D2: a chave vale em TODO ``/api/*``, não só na fachada."""
    raw, _ = _issue(owner["id"])
    r = client.get("/api/contacts", headers={"X-Api-Key": raw})
    assert r.status_code == 200


# ── §9.6 — o escopo é o do USUÁRIO ──────────────────────────────────────────

def test_permissions_are_the_owners(client, owner):
    """Dono só tem ``contact.read`` ⇒ GET 200 e POST 403."""
    raw, _ = _issue(owner["id"])
    h = {"X-Api-Key": raw}
    assert client.get("/api/v1/contacts", headers=h).status_code == 200
    r = client.post("/api/v1/contacts", headers=h, json={"phone": "5511900000001"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# ── §9.10 — revogação e expiração ───────────────────────────────────────────

def test_revoked_key_is_401_immediately(client, owner):
    raw, row = _issue(owner["id"])
    h = {"X-Api-Key": raw}
    assert client.get("/api/v1/contacts", headers=h).status_code == 200
    api_key_repo.revoke(row["id"])
    assert client.get("/api/v1/contacts", headers=h).status_code == 401


def test_expired_key_is_401(client, owner):
    raw, _ = _issue(owner["id"], expires_at=time.time() - 1)
    assert client.get("/api/v1/contacts",
                      headers={"X-Api-Key": raw}).status_code == 401


def test_inactive_owner_is_401(client, owner):
    raw, _ = _issue(owner["id"])
    user_repo.update_info(owner["id"], is_active=0)
    assert client.get("/api/v1/contacts",
                      headers={"X-Api-Key": raw}).status_code == 401
    user_repo.update_info(owner["id"], is_active=1)


# ── §9.3/§9.4 — emissão e o guardrail de admin ──────────────────────────────

def test_create_key_returns_the_secret_once(client, admin_session, owner):
    r = client.post("/api/api-keys", headers=admin_session["headers"],
                    json={"label": "CRM", "user_id": owner["id"]})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["key"].startswith("wsk_live_")
    # A listagem NUNCA devolve o segredo de volta.
    listed = client.get("/api/api-keys", headers=admin_session["headers"]).json()["data"]
    mine = [k for k in listed if k["id"] == data["id"]][0]
    assert "key" not in mine
    assert mine["masked"].startswith("wsk_live_")
    assert data["key"] not in repr(listed)


def test_admin_owner_requires_explicit_confirmation(client, admin_session):
    """§4.1 — chave de admin vazada = instalação inteira."""
    target = _mk_user(admin=True)
    try:
        r = client.post("/api/api-keys", headers=admin_session["headers"],
                        json={"label": "perigosa", "user_id": target["id"]})
        assert r.status_code == 409
        assert r.json()["data"]["reason"] == "admin_owner_requires_confirm"
        # Com o "sim" explícito, passa.
        r2 = client.post("/api/api-keys", headers=admin_session["headers"],
                         json={"label": "perigosa", "user_id": target["id"],
                               "confirm": True})
        assert r2.status_code == 200
    finally:
        user_repo.delete(target["id"])


def test_default_expiry_is_filled(client, admin_session, owner):
    """§4.4 — validade preenchida por padrão, nunca nula."""
    r = client.post("/api/api-keys", headers=admin_session["headers"],
                    json={"label": "CRM", "user_id": owner["id"]})
    assert r.json()["data"]["expires_at"] > time.time()


def test_never_expires_requires_confirmation(client, admin_session, owner):
    r = client.post("/api/api-keys", headers=admin_session["headers"],
                    json={"label": "eterna", "user_id": owner["id"],
                          "expires_in_days": "never"})
    assert r.status_code == 409
    assert r.json()["data"]["reason"] == "never_expires_requires_confirm"


def test_issuing_requires_apikey_manage(client, owner):
    """Um usuário sem ``apikey.manage`` não cunha chave — nem com uma chave."""
    raw, _ = _issue(owner["id"])
    r = client.post("/api/api-keys", headers={"X-Api-Key": raw},
                    json={"label": "auto-promoção", "user_id": owner["id"]})
    assert r.status_code == 403


# ── §9.9 — auditoria distingue a procedência ────────────────────────────────

_CORE_AUDIT_ID = "__core_audit__"


@pytest.fixture
def audit_wired(client):
    """Liga o listener ``*`` de auditoria no loop do TestClient.

    O fixture ``app`` roda com lifespan no-op, então o listener (que o
    ``server/app.py`` registra no lifespan) não existe. O loop TEM de ser o do
    portal do TestClient: é nele que o middleware seta o contextvar do ator, e é
    esse contexto que o ``create_task`` do barramento tira foto.
    """
    from plugins import events as bus
    from server.audit_listener import register_audit_listener

    prev_loop = getattr(bus, "_loop", None)
    prev_handler = getattr(bus, "_agent_handler", None)
    loop = client.portal.call(asyncio.get_running_loop)
    bus.set_runtime(loop, prev_handler)
    if not any(pid == _CORE_AUDIT_ID for pid, _ in bus._handlers.get("*", [])):
        register_audit_listener()

    def _drain():
        async def _d():
            cur = asyncio.current_task()
            for _ in range(10):
                pending = [t for t in asyncio.all_tasks()
                           if t is not cur and not t.done()]
                if not pending:
                    break
                try:
                    await asyncio.wait(pending, timeout=2.0)
                except Exception:
                    pass
                await asyncio.sleep(0)
        client.portal.call(_d)

    yield _drain

    bucket = bus._handlers.get("*")
    if bucket:
        bus._handlers["*"] = [t for t in bucket if t[0] != _CORE_AUDIT_ID]
    bus.set_runtime(prev_loop, prev_handler)


def test_audit_marks_the_key_as_provenance(client, audit_wired):
    """O ator continua sendo o usuário DONO; a chave é a procedência (D4)."""
    from db.repositories import audit_repo

    holder = _mk_user(custom_perms=["tag.manage", "audit.read"])
    try:
        raw, key_row = _issue(holder["id"])
        name = f"tag-{uuid.uuid4().hex[:8]}"
        r = client.post("/api/v1/tags", headers={"X-Api-Key": raw},
                        json={"name": name, "color": "#112233"})
        assert r.status_code == 201, r.text
        audit_wired()
        rows = audit_repo.query(api_key_id=key_row["id"], limit=10)
        assert rows, "nenhuma linha de auditoria carimbada com a chave"
        row = rows[0]
        assert row["actor_type"] == "apikey"
        assert row["actor_user_id"] == holder["id"]
        assert row["api_key_id"] == key_row["id"]
    finally:
        user_repo.delete(holder["id"])


# ── §9.2 — as peças de dados existem ────────────────────────────────────────

def test_apikey_manage_permission_exists_in_the_catalog():
    """Sem linha em ``permissions``, o grant vira no-op silencioso."""
    keys = {p["key"] for p in rbac_repo.list_permissions()}
    assert "apikey.manage" in keys
    assert "webhook.manage" in keys
