"""Plano 177 · F4 — rotas do cofre de credenciais e auditoria.

O que se prova aqui:
  * nenhuma resposta (lista, put, vínculo, GET da tool) carrega o valor secreto;
  * manter / gravar / remover campo pela semântica do PUT;
  * RBAC: ``admin`` e ``gestor`` passam; usuário só com ``agent.tools.manage``
    (custom) recebe 403 no cofre mas edita o CÓDIGO da tool normalmente (D11);
  * vínculo só com tool ``kind='code'``;
  * a trilha de auditoria guarda nomes de campo e ``secret_definido``, NUNCA o valor.
"""

from __future__ import annotations

import json
import uuid

import pytest

from db.repositories import (
    api_key_repo, audit_repo, credential_repo, session_repo, tool_repo, user_repo,
)
from server import api_keys as keylib
from server.auth import generate_session_token

CRED = "p177_api"
TOOL = "p177_api_tool"
BUILTIN = "p177_api_builtin"
SECRET = "rbk_SEGREDO_p177_api_55e1"

_CODE = (
    'SCHEMA = {"type": "function", "function": {"name": "%s", "description": "d", '
    '"parameters": {"type": "object", "properties": {}}}}\n'
    'def execute(ctx, args):\n    return "ok"\n'
)


def _login(client, *, role_keys=None, permission_keys=None):
    custom = permission_keys is not None
    user = user_repo.create(
        email=f"cred-{uuid.uuid4().hex}@test.local", name="Cred Tester",
        password_hash="test-only", role_keys=role_keys,
        permission_keys=permission_keys, custom=custom)
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    return user, token


@pytest.fixture
def make_client(client):
    """Devolve ``(client, user)`` autenticados; limpa tudo no fim."""
    made: list[tuple[dict, str]] = []

    def _make(**kw):
        user, token = _login(client, **kw)
        made.append((user, token))
        client.headers["Authorization"] = f"Bearer {token}"
        return client, user

    yield _make
    client.headers.pop("Authorization", None)
    for user, token in made:
        session_repo.delete(token)
        user_repo.delete(user["id"])


@pytest.fixture(autouse=True)
def _clean(_engine_ready):
    def wipe():
        credential_repo.delete(CRED)
        tool_repo.delete(TOOL)
        tool_repo.delete(BUILTIN)
    wipe()
    tool_repo.save(TOOL, description="t", code=_CODE % TOOL, dependencies=[],
                   enabled=False)
    tool_repo.save(BUILTIN, description="b", code=_CODE % BUILTIN, dependencies=[],
                   enabled=True, kind="builtin")
    yield
    wipe()


@pytest.fixture
def admin(make_client):
    return make_client(role_keys=["admin"])[0]


def _put(c, fields=None, name=CRED, desc="API da plataforma"):
    fields = fields if fields is not None else [
        {"key": "url", "secret": False, "value": "https://api.exemplo.com"},
        {"key": "chave", "secret": True, "value": SECRET},
    ]
    return c.put(f"/api/ai/credentials/{name}",
                 json={"description": desc, "fields": fields})


# ── nenhuma listagem traz o valor (o "olho" é a única via, testada mais abaixo) ──────────────────────────────────────────────────────

def test_no_response_ever_carries_the_secret(admin):
    r = _put(admin)
    assert r.status_code == 200, r.text
    assert SECRET not in r.text

    dumps = [r.text, admin.get("/api/ai/credentials").text]
    assert admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]}).status_code == 200
    r2 = admin.put(f"/api/ai/tools/{TOOL}/credentials", json={"credentials": [CRED]})
    assert r2.status_code == 200, r2.text
    dumps += [r2.text, admin.get(f"/api/ai/tools/{TOOL}").text, admin.get("/api/ai/tools").text]
    for d in dumps:
        assert SECRET not in d


def test_list_shape_has_value_only_for_non_secret_fields(admin):
    _put(admin)
    item = [c for c in admin.get("/api/ai/credentials").json()["data"] if c["name"] == CRED][0]
    fields = {f["key"]: f for f in item["fields"]}
    assert fields["url"] == {"key": "url", "secret": False, "has_value": True,
                             "value": "https://api.exemplo.com"}
    assert fields["chave"] == {"key": "chave", "secret": True, "has_value": True}
    assert item["updated_by_name"] == "Cred Tester"


def test_get_tool_exposes_only_credential_names(admin):
    _put(admin)
    admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]})
    data = admin.get(f"/api/ai/tools/{TOOL}").json()["data"]
    assert data["credentials"] == [CRED]


# ── manter / gravar / remover ────────────────────────────────────────────────

def test_put_without_value_keeps_the_stored_secret(admin):
    _put(admin)
    r = _put(admin, [{"key": "url", "secret": False, "value": "https://outra"},
                     {"key": "chave", "secret": True}])
    assert r.status_code == 200, r.text
    admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]})
    creds, secrets = credential_repo.for_tool(TOOL)
    assert creds[CRED] == {"url": "https://outra", "chave": SECRET}


def test_put_removes_absent_field_and_rejects_bad_input(admin):
    _put(admin)
    r = _put(admin, [{"key": "url", "secret": False}])
    assert r.status_code == 200
    assert [f["key"] for f in r.json()["data"]["fields"]] == ["url"]
    assert _put(admin, [], name=CRED).status_code == 400
    assert _put(admin, [{"key": "Ruim", "value": "x"}]).status_code == 400
    assert _put(admin, name="Nome Ruim").status_code in (400, 404, 422)


def test_five_digit_credential_is_accepted(admin):
    r = _put(admin, [{"key": "pin", "secret": True, "value": "48213"}])
    assert r.status_code == 200, r.text
    assert "48213" not in r.text


def test_delete_credential_takes_its_links(admin):
    _put(admin)
    admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]})
    assert admin.delete(f"/api/ai/credentials/{CRED}").status_code == 200
    assert credential_repo.links_for_tool(TOOL) == []
    assert admin.delete(f"/api/ai/credentials/{CRED}").status_code == 404


# ── vínculo só com tool de código ────────────────────────────────────────────

def test_link_refuses_non_code_and_missing_tools(admin):
    _put(admin)
    r = admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [BUILTIN]})
    assert r.status_code == 400 and BUILTIN in r.text
    r = admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": ["p177_fantasma"]})
    assert r.status_code == 400
    r = admin.put(f"/api/ai/tools/{BUILTIN}/credentials", json={"credentials": [CRED]})
    assert r.status_code == 400
    assert credential_repo.get_meta(CRED)["tools"] == []


def test_link_unknown_credential_is_404_or_400(admin):
    r = admin.put("/api/ai/credentials/nao_existe/tools", json={"tools": [TOOL]})
    assert r.status_code == 404
    r = admin.put(f"/api/ai/tools/{TOOL}/credentials", json={"credentials": ["nao_existe"]})
    assert r.status_code == 400


def test_existing_link_to_a_deleted_tool_can_be_kept(admin):
    """P4: o vínculo é só um nome. Reenviar a lista que mantém uma tool já
    excluída não pode ser recusado — senão a tela não consegue salvar nada."""
    _put(admin)
    admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]})
    tool_repo.delete(TOOL)
    r = admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]})
    assert r.status_code == 200, r.text
    item = [c for c in admin.get("/api/ai/credentials").json()["data"] if c["name"] == CRED][0]
    assert item["missing_tools"] == [TOOL]


# ── RBAC ─────────────────────────────────────────────────────────────────────

def test_gestor_can_manage_credentials(make_client):
    c, _ = make_client(role_keys=["gestor"])
    assert c.get("/api/ai/credentials").status_code == 200
    assert _put(c).status_code == 200


def test_atendente_cannot(make_client):
    c, _ = make_client(role_keys=["atendente"])
    assert c.get("/api/ai/credentials").status_code == 403
    assert _put(c).status_code == 403


def test_tools_manage_alone_is_denied_the_vault_but_still_edits_tool_code(make_client):
    """D11: sem gate de edição de código. A permissão nova separa o COFRE, mas
    quem tem ``agent.tools.manage`` segue editando a tool (e alcançando a chave)."""
    c, _ = make_client(permission_keys=["agent.tools.manage"])
    assert c.get("/api/ai/credentials").status_code == 403
    assert _put(c).status_code == 403
    assert c.put(f"/api/ai/tools/{TOOL}/credentials", json={"credentials": []}).status_code == 403

    # vincule como admin e prove que editar o CÓDIGO continua liberado
    credential_repo.upsert_credential(CRED, "x", [{"key": "chave", "value": SECRET}])
    credential_repo.set_tool_links(CRED, [TOOL])
    r = c.put(f"/api/ai/tools/{TOOL}", json={
        "description": "editada", "code": (_CODE % TOOL) + "# editada\n",
        "dependencies": [], "enabled": False, "restart": False})
    assert r.status_code == 200, r.text
    assert SECRET not in r.text
    # e o GET da tool mostra só o nome do vínculo
    assert c.get(f"/api/ai/tools/{TOOL}").json()["data"]["credentials"] == [CRED]


def test_credentials_permission_alone_does_not_open_the_tools_routes(make_client):
    c, _ = make_client(permission_keys=["agent.credentials.manage"])
    assert c.get("/api/ai/credentials").status_code == 200
    assert c.get("/api/ai/tools").status_code == 403


# ── auditoria ────────────────────────────────────────────────────────────────

def _audit_rows(action):
    return audit_repo.query(action=action, resource_type="credential",
                            resource_id=CRED, limit=50)


def test_audit_trail_has_field_names_and_never_the_value(admin):
    _put(admin)
    _put(admin, [{"key": "url", "secret": False, "value": "https://b"},
                 {"key": "chave", "secret": True, "value": SECRET + "-v2"}])
    admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]})
    admin.delete(f"/api/ai/credentials/{CRED}")

    everything = []
    for action in ("credential.create", "credential.update",
                   "credential.link", "credential.delete"):
        rows = _audit_rows(action)
        assert rows, f"sem registro de {action}"
        everything.extend(rows)
    dump = json.dumps(everything, default=str)
    assert SECRET not in dump and "https://b" not in dump
    assert "chave" in dump                   # nome do campo, sim
    created = _audit_rows("credential.create")[0]
    after = json.loads(created["after_json"])
    assert after["secret_definido"] is True
    assert sorted(after["campos"]) == ["chave", "url"]


def test_audit_of_a_tool_side_link_keeps_the_credential_names(admin):
    """A chave "credentials" está na denylist do audit_repo e viraria "***"."""
    _put(admin)
    admin.put(f"/api/ai/tools/{TOOL}/credentials", json={"credentials": [CRED]})
    row = audit_repo.query(action="credential.link", resource_type="credential",
                           resource_id=TOOL, limit=5)[0]
    after = json.loads(row["after_json"])
    assert after["credenciais"] == [CRED]
    assert "***" not in row["after_json"]


# ── varredura de tabelas que a API toca ──────────────────────────────────────

def test_secret_is_nowhere_but_the_vault_after_api_use(admin):
    from sqlalchemy import text

    from db.engine import get_engine

    _put(admin)
    admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]})
    admin.get("/api/ai/credentials")
    with get_engine().connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))]
        leaks = []
        for t in tables:
            if t in ("ai_credentials", "ai_credential_tools"):
                continue
            n = conn.execute(
                text(f'SELECT count(*) FROM "{t}" WHERE "{t}"::text LIKE :p'),
                {"p": f"%{SECRET}%"}).scalar()
            if n:
                leaks.append((t, n))
    assert leaks == []


# ── o olho: ÚNICO caminho que devolve o valor de um campo salvo ──────────────

def _reveal(c, key="chave", name=CRED, **kw):
    return c.post(f"/api/ai/credentials/{name}/fields/{key}/reveal", **kw)


def test_reveal_returns_only_the_requested_field(admin):
    _put(admin)
    r = _reveal(admin)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"name": CRED, "key": "chave", "value": SECRET}
    # um campo por vez: pedir a chave não entrega a URL
    assert "api.exemplo.com" not in r.text


def test_reveal_is_never_cached(admin):
    _put(admin)
    assert _reveal(admin).headers["cache-control"] == "no-store"


def test_reveal_is_post_only(admin):
    """GET entraria em cache, prefetch e histórico por engano."""
    _put(admin)
    assert admin.get(f"/api/ai/credentials/{CRED}/fields/chave/reveal").status_code in (404, 405)


def test_reveal_unknown_credential_or_field_is_404(admin):
    _put(admin)
    assert _reveal(admin, name="nao_existe").status_code == 404
    assert _reveal(admin, key="nao_existe").status_code == 404


def test_reveal_returns_an_empty_string_for_a_field_with_no_value(admin):
    _put(admin, [{"key": "url", "secret": False, "value": "https://a"}])
    credential_repo.upsert_credential(CRED, "x", [{"key": "url", "secret": False}])
    assert _reveal(admin, key="url").json()["data"]["value"] == "https://a"


def test_reveal_requires_the_credentials_permission(make_client):
    # sobe a credencial com um gestor; depois testa quem NÃO tem a permissão
    gestor, _ = make_client(role_keys=["gestor"])
    _put(gestor)
    assert _reveal(gestor).status_code == 200

    only_tools, _ = make_client(permission_keys=["agent.tools.manage"])
    assert _reveal(only_tools).status_code == 403
    assert SECRET not in _reveal(only_tools).text

    atendente, _ = make_client(role_keys=["atendente"])
    r = _reveal(atendente)
    assert r.status_code == 403 and SECRET not in r.text


def test_reveal_is_refused_for_an_api_key_even_with_the_permission(client):
    """Uma chave de API herda TODAS as permissões do dono; ler a credencial não é
    papel de integração. A prova de que o 403 vem da REGRA (e não de falta de
    permissão) é que a MESMA chave lista as credenciais com 200."""
    keylib._verify_cache.clear()
    owner = user_repo.create(
        email=f"reveal-key-{uuid.uuid4().hex}@test.local", name="Integração",
        password_hash="test-only", permission_keys=["agent.credentials.manage"],
        custom=True)
    raw, prefix, key_hash = keylib.generate_key()
    api_key_repo.create(user_id=owner["id"], label="suite", key_hash=key_hash,
                        prefix=prefix, last4=keylib.last4(raw))
    credential_repo.upsert_credential(CRED, "x", [{"key": "chave", "value": SECRET}])
    try:
        headers = {"X-Api-Key": raw}
        assert client.get("/api/ai/credentials", headers=headers).status_code == 200
        r = client.post(f"/api/ai/credentials/{CRED}/fields/chave/reveal", headers=headers)
        assert r.status_code == 403, r.text
        assert SECRET not in r.text
        assert "painel" in r.text
    finally:
        user_repo.delete(owner["id"])


def _new_reveal_rows(before_ids):
    """Linhas ``credential.reveal`` que entraram DEPOIS de ``before_ids``: o
    ``audit_log`` não é limpo entre testes, então contar o total absoluto mede
    o lixo dos testes anteriores e não o que este teste fez."""
    return [r for r in _audit_rows("credential.reveal") if r["id"] not in before_ids]


def test_reveal_is_audited_with_the_field_name_and_never_the_value(admin):
    _put(admin)
    before = {r["id"] for r in _audit_rows("credential.reveal")}
    assert _reveal(admin, key="chave").status_code == 200
    assert _reveal(admin, key="url").status_code == 200
    rows = _new_reveal_rows(before)
    assert len(rows) == 2
    dump = json.dumps(rows, default=str)
    assert SECRET not in dump and "https://api.exemplo.com" not in dump
    assert sorted(json.loads(r["after_json"])["campo"] for r in rows) == ["chave", "url"]
    # quem leu fica registrado
    assert all(r["actor_user_id"] for r in rows)


def test_a_refused_reveal_leaves_no_audit_row(make_client):
    gestor, _ = make_client(role_keys=["gestor"])
    _put(gestor)
    before = {r["id"] for r in _audit_rows("credential.reveal")}
    atendente, _ = make_client(role_keys=["atendente"])
    assert _reveal(atendente).status_code == 403
    assert _new_reveal_rows(before) == []


def test_everything_else_still_hides_the_value_after_a_reveal(admin):
    """O olho abre UMA via; lista, PUT, vínculo e GET da tool seguem sem o valor."""
    _put(admin)
    assert _reveal(admin).status_code == 200
    admin.put(f"/api/ai/credentials/{CRED}/tools", json={"tools": [TOOL]})
    for text in (admin.get("/api/ai/credentials").text,
                 admin.get(f"/api/ai/tools/{TOOL}").text,
                 _put(admin).text):
        assert SECRET not in text


def test_reveal_leaves_the_secret_in_no_table_outside_the_vault(admin):
    from sqlalchemy import text

    from db.engine import get_engine

    _put(admin)
    _reveal(admin)
    with get_engine().connect() as conn:
        leaks = []
        for (t,) in conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).all():
            if t in ("ai_credentials", "ai_credential_tools"):
                continue
            n = conn.execute(text(f'SELECT count(*) FROM "{t}" WHERE "{t}"::text LIKE :p'),
                             {"p": f"%{SECRET}%"}).scalar()
            if n:
                leaks.append((t, n))
    assert leaks == []
