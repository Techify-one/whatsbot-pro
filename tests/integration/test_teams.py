"""Times (plano 153) — CRUD, RBAC, atribuição de conversa e filtro server-side.

Cobre os itens da Fase 8 do plano e do plano 166/01: CRUD via REST gated por
``team.manage``; ``team_ids_for_user`` com um usuário em 2+ times (requisito #3, prova
direta); ``assign_team``/``assign-team`` gravando e emitindo
``conversation.team_assigned``/``.team_unassigned`` conforme o valor;
``assignable-agents`` trazendo ``teams``; filtro server-side (``registry`` +
``translate``) via ``/api/atendimentos/filter``; e RBAC nos dois endpoints
novos.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from plugins import events as bus

_CREATED_USER_IDS: list[int] = []
_CREATED_TEAM_IDS: list[int] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_teams_users(_engine_ready):
    yield
    from db.repositories import session_repo, user_repo, team_repo

    for user_id in reversed(_CREATED_USER_IDS):
        session_repo.delete_for_user(user_id)
        assert user_repo.delete(user_id), f"could not remove teams test user {user_id}"
    _CREATED_USER_IDS.clear()
    for team_id in reversed(_CREATED_TEAM_IDS):
        try:
            team_repo.delete(team_id)
        except team_repo.TeamHasConversations:
            # Lifecycle contract: linked teams are historical records and cannot
            # be hard-deleted by test cleanup either.
            team_repo.deactivate(team_id)
    _CREATED_TEAM_IDS.clear()


def _mk_user(email: str, *, admin: bool = False, custom_perms=None, role_keys=None):
    from db.repositories import user_repo
    user = user_repo.get_by_email(email)
    if user is None:
        user = user_repo.create(
            email=email, name="Teams Test",
            password_hash="x",
            role_keys=(["admin"] if admin else (role_keys or [])),
        )
        _CREATED_USER_IDS.append(user["id"])
        if custom_perms is not None:
            user_repo.set_custom_permissions(user["id"], list(custom_perms))
    return user_repo.get(user["id"])


def _auth(client, user):
    from db.repositories import session_repo
    from server.auth import generate_session_token
    tok = generate_session_token()
    session_repo.create(tok, user["id"], user_agent="test", ip="127.0.0.1")
    client.headers["Authorization"] = f"Bearer {tok}"
    return client


def _open_conv(phone: str):
    from db.repositories import contact_repo, conversation_repo
    contact = contact_repo.get_or_create(phone)
    conv, _ = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net")
    return conv["id"], contact["id"]


# ── CRUD via REST (gated by team.manage) ────────────────────────────────────

def test_team_crud_via_rest(client):
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)

    r = client.post("/api/teams", json={"name": "Suporte", "description": "N1"})
    assert r.status_code == 200, r.text
    team = r.json()["data"]["team"]
    _CREATED_TEAM_IDS.append(team["id"])
    assert team["name"] == "Suporte"
    assert team["description"] == "N1"
    assert team["member_user_ids"] == []

    r = client.get("/api/teams")
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()["data"]["teams"]}
    assert "Suporte" in names

    r = client.put(f"/api/teams/{team['id']}", json={"description": "N2"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["team"]["description"] == "N2"
    assert r.json()["data"]["team"]["name"] == "Suporte"  # unchanged

    r = client.delete(f"/api/teams/{team['id']}")
    assert r.status_code == 200, r.text

    r = client.get("/api/teams")
    assert team["id"] not in {t["id"] for t in r.json()["data"]["teams"]}


def test_team_create_requires_name(client):
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    r = client.post("/api/teams", json={"name": "  "})
    assert r.status_code == 400, r.text


def test_team_crud_requires_team_manage(client):
    """``users.manage`` não substitui a permissão dedicada de times."""
    legacy_manager = _mk_user(
        "teams_users_manage_only@test.com", custom_perms=["users.manage"])
    _auth(client, legacy_manager)

    assert client.get("/api/teams").status_code == 403
    assert client.post("/api/teams", json={"name": "X"}).status_code == 403

    team_manager = _mk_user(
        "teams_manage_only@test.com", custom_perms=["team.manage"])
    _auth(client, team_manager)

    assert client.get("/api/teams").status_code == 200


def test_team_delete_deactivates_and_preserves_conversation_policy(client):
    from db.repositories import team_repo, conversation_repo
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)

    team = team_repo.create("Efêmero")
    conv_id, _ = _open_conv("5511970000001")
    conversation_repo.set_team(conv_id, team["id"])
    status_before_delete = conversation_repo.get(conv_id)["status"]

    r = client.delete(f"/api/teams/{team['id']}")
    assert r.status_code == 200, r.text

    conv = conversation_repo.get(conv_id)
    assert conv["team_id"] == team["id"]
    assert conv["status"] == status_before_delete
    assert team_repo.get(team["id"])["is_active"] == 0
    enriched = conversation_repo.get_with_channel(conv_id)
    assert enriched["team_name"] == "Efêmero"
    assert enriched["team_is_active"] == 0


def test_last_active_private_member_cannot_be_deactivated_or_deleted(client):
    from db.repositories import team_repo

    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    member = _mk_user("teams_private_last_member@test.com")
    team = team_repo.create(
        "Privado com último membro", enforce_team_access=True,
        member_user_ids=[member["id"]])
    _CREATED_TEAM_IDS.append(team["id"])
    _auth(client, admin)

    response = client.put(
        f"/api/users/{member['id']}", json={"is_active": False})
    assert response.status_code == 409, response.text
    response = client.delete(f"/api/users/{member['id']}")
    assert response.status_code == 409, response.text


# ── requisito #3: usuário em vários times ao mesmo tempo ────────────────────

def test_user_can_belong_to_multiple_teams():
    from db.repositories import team_repo, team_member_repo

    user = _mk_user("teams_multi_member@test.com")
    t1 = team_repo.create("Time A")
    t2 = team_repo.create("Time B")
    _CREATED_TEAM_IDS.extend([t1["id"], t2["id"]])

    team_member_repo.set_teams_for_user(user["id"], [t1["id"], t2["id"]])
    assert sorted(team_member_repo.team_ids_for_user(user["id"])) == sorted([t1["id"], t2["id"]])
    assert user["id"] in team_member_repo.member_ids(t1["id"])
    assert user["id"] in team_member_repo.member_ids(t2["id"])


# ── assignable-agents traz teams ─────────────────────────────────────────────

def test_assignable_agents_includes_teams(client):
    from db.repositories import team_repo
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    team = team_repo.create("Vendas")
    _CREATED_TEAM_IDS.append(team["id"])

    r = client.get("/api/atendimentos/assignable-agents")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "teams" in data
    row = next(t for t in data["teams"] if t["id"] == team["id"])
    assert row["name"] == "Vendas"
    assert row["readable"] is True
    assert row["assignable"] is True


# ── assign-team: grava, reflete na resposta, gated por conversation.assign ──

def test_assign_team_via_rest_sets_and_clears(client):
    from db.repositories import team_repo
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    team = team_repo.create("Financeiro")
    _CREATED_TEAM_IDS.append(team["id"])
    conv_id, _ = _open_conv("5511970000002")

    r = client.post(f"/api/atendimentos/{conv_id}/assign-team", json={"team_id": team["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["conversation"]["team_id"] == team["id"]

    r = client.post(f"/api/atendimentos/{conv_id}/assign-team", json={"team_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["conversation"]["team_id"] is None


def test_assign_team_requires_conversation_assign(client):
    user = _mk_user("teams_no_assign_perm@test.com", custom_perms=["conversation.read"])
    _auth(client, user)
    conv_id, _ = _open_conv("5511970000003")
    r = client.post(f"/api/atendimentos/{conv_id}/assign-team", json={"team_id": None})
    assert r.status_code == 403


def test_assign_team_independent_of_assignee(client):
    """D1: atribuir time não mexe no assignee_user_id, e vice-versa."""
    from db.repositories import team_repo, user_repo
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    team = team_repo.create("Independente")
    _CREATED_TEAM_IDS.append(team["id"])
    agent = user_repo.create(email="teams_indep_agent@test.com", name="Agente",
                             password_hash="x", role_keys=["atendente"])
    _CREATED_USER_IDS.append(agent["id"])
    conv_id, _ = _open_conv("5511970000004")

    r = client.post(f"/api/atendimentos/{conv_id}/assign", json={"assignee_user_id": agent["id"]})
    assert r.status_code == 200, r.text

    r = client.post(f"/api/atendimentos/{conv_id}/assign-team", json={"team_id": team["id"]})
    assert r.status_code == 200, r.text
    conv = r.json()["data"]["conversation"]
    assert conv["team_id"] == team["id"]
    assert conv["assignee_user_id"] == agent["id"], "assign_team não pode limpar o assignee (D1)"


# ── Eventos do bus: conversation.team_assigned / .team_unassigned ──────────

@pytest.fixture
def captured_events():
    """Mesmo padrão de tests/integration/api/test_conversation_events.py: wire o
    bus a um loop real e capture cada evento via filter.event.before_emit."""
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        started.set()
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    started.wait()

    bus.reset()
    bus.set_runtime(loop, agent_handler=None)

    records: list[tuple[str, dict]] = []

    def _capture(ctx, payload):
        records.append((ctx.extras.get("event_name"), payload))
        return payload

    bus.register_filter("teams_test", "filter.event.before_emit", _capture, priority=1)

    yield records

    bus.reset()
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2.0)


def _drain():
    time.sleep(0.05)


def test_assign_team_emits_team_assigned_and_unassigned(client, captured_events):
    from db.repositories import team_repo
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    team = team_repo.create("Bus Team")
    _CREATED_TEAM_IDS.append(team["id"])
    conv_id, _ = _open_conv("5511970000005")
    captured_events.clear()

    r = client.post(f"/api/atendimentos/{conv_id}/assign-team", json={"team_id": team["id"]})
    assert r.status_code == 200, r.text
    _drain()
    names = [n for n, _ in captured_events]
    assigned = [p for n, p in captured_events if n == "conversation.team_assigned"]
    assert len(assigned) == 1, f"expected 1 team_assigned, got {names}"
    assert assigned[0]["conversation_id"] == conv_id
    assert assigned[0]["team_id"] == team["id"]
    assert "conversation.team_unassigned" not in names

    captured_events.clear()
    r = client.post(f"/api/atendimentos/{conv_id}/assign-team", json={"team_id": None})
    assert r.status_code == 200, r.text
    _drain()
    names = [n for n, _ in captured_events]
    unassigned = [p for n, p in captured_events if n == "conversation.team_unassigned"]
    assert len(unassigned) == 1, f"expected 1 team_unassigned, got {names}"
    assert unassigned[0]["previous_team_id"] == team["id"]
    assert "conversation.team_assigned" not in names


# ── Filtro server-side (registry + translate) via /api/atendimentos/filter ──

def test_team_filter_equal_to_and_is_not_present(client):
    from db.repositories import team_repo
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    team = team_repo.create("Filtro Team")
    _CREATED_TEAM_IDS.append(team["id"])

    c1, _ = _open_conv("5511970000006")
    c2, _ = _open_conv("5511970000007")
    client.post(f"/api/atendimentos/{c1}/assign-team", json={"team_id": team["id"]})

    r = client.get(f"/api/atendimentos/filter?team={team['id']}&limit=50")
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()["data"]["conversations"]}
    assert c1 in ids and c2 not in ids

    r = client.get(f"/api/atendimentos/filter?team=none&status=open&limit=200")
    assert r.status_code == 200, r.text
    ids_none = {c["id"] for c in r.json()["data"]["conversations"]}
    assert c1 not in ids_none
    assert c2 in ids_none


def test_team_dimension_listed_in_filter_schema(client):
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    r = client.get("/api/atendimentos/filter-schema")
    assert r.status_code == 200, r.text
    keys = {d["key"] for d in r.json()["data"]["dimensions"]}
    assert "team" in keys


# ── Visibilidade por time restrito (plano 154) ───────────────────────────────

def _mk_inbox(channel_id: str) -> int:
    """Inbox dedicado (canal próprio) para não mexer na membership do inbox
    default compartilhado com o resto da suíte — mesmo padrão de
    tests/integration/test_conversation_read_isolation.py."""
    from db.repositories import channel_repo, inbox_repo
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    inbox = inbox_repo.get_by_channel(channel_id) or inbox_repo.create(
        channel_id=channel_id, name=channel_id)
    return inbox["id"]


def _open_conv_in_inbox(phone: str, inbox_id: int):
    from db.repositories import contact_repo, conversation_repo
    contact = contact_repo.get_or_create(phone)
    conv, _ = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net", inbox_id=inbox_id)
    return conv["id"], contact["id"]


def test_team_crud_restrict_visibility(client):
    """I2/I6: create/update gravam e devolvem o campo; PUT parcial preserva."""
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)

    r = client.post("/api/teams", json={"name": "Restrito CRUD", "restrict_visibility": True})
    assert r.status_code == 200, r.text
    team = r.json()["data"]["team"]
    _CREATED_TEAM_IDS.append(team["id"])
    assert team["restrict_visibility"] == 1

    r = client.get("/api/teams")
    assert r.status_code == 200, r.text
    listed = next(t for t in r.json()["data"]["teams"] if t["id"] == team["id"])
    assert listed["restrict_visibility"] == 1

    # PUT parcial (só nome) NÃO mexe no valor (D2/I6: None = não mexe)
    r = client.put(f"/api/teams/{team['id']}", json={"name": "Restrito CRUD 2"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["team"]["restrict_visibility"] == 1

    # desligar explicitamente
    r = client.put(f"/api/teams/{team['id']}", json={"restrict_visibility": False})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["team"]["restrict_visibility"] == 0


def test_team_default_restrict_visibility_is_off(client):
    """D2: time criado sem o campo nasce com restrict_visibility=0."""
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    r = client.post("/api/teams", json={"name": "Sem opt-in"})
    assert r.status_code == 200, r.text
    team = r.json()["data"]["team"]
    _CREATED_TEAM_IDS.append(team["id"])
    assert team["restrict_visibility"] == 0


def test_restrict_visibility_hides_from_listing_not_from_direct_access(client):
    """Cenário completo do §5: time restrito some da LISTAGEM para quem está na
    mesma caixa mas não é do time (D3) — mas o acesso direto por ID continua
    200 (não 404); quem tem conversation.read_all vê tudo; time NÃO restrito e
    conversa sem time nunca somem (D4). /filter e /count (I4) não podem
    divergir da listagem simples."""
    from db.repositories import (team_repo, team_member_repo, inbox_member_repo,
                                 conversation_repo)

    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)

    inbox_id = _mk_inbox("plan154_vis_ch")

    team_restricted = team_repo.create("Plan154 Restrito", restrict_visibility=True)
    team_open = team_repo.create("Plan154 Aberto", restrict_visibility=False)
    _CREATED_TEAM_IDS.extend([team_restricted["id"], team_open["id"]])

    u_member = _mk_user("plan154_member@test.com", custom_perms=["conversation.read"])
    u_other = _mk_user("plan154_other@test.com", custom_perms=["conversation.read"])
    u_readall = _mk_user("plan154_readall@test.com",
                         custom_perms=["conversation.read", "conversation.read_all"])

    inbox_member_repo.set_members(inbox_id, [u_member["id"], u_other["id"]])
    team_member_repo.set_members(team_restricted["id"], [u_member["id"]])

    conv_a, _ = _open_conv_in_inbox("5511970100001", inbox_id)  # time restrito
    conv_b, _ = _open_conv_in_inbox("5511970100002", inbox_id)  # time NÃO restrito
    conv_c, _ = _open_conv_in_inbox("5511970100003", inbox_id)  # sem time
    conversation_repo.set_team(conv_a, team_restricted["id"])
    conversation_repo.set_team(conv_b, team_open["id"])

    # U1 — membro do time restrito: vê as 3 na listagem simples
    _auth(client, u_member)
    r = client.get(f"/api/atendimentos?inbox_id={inbox_id}&limit=200")
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()["data"]["conversations"]}
    assert {conv_a, conv_b, conv_c} <= ids

    # U2 — mesma caixa, NÃO é do time restrito: A some da listagem, B e C continuam
    _auth(client, u_other)
    r = client.get(f"/api/atendimentos?inbox_id={inbox_id}&limit=200")
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()["data"]["conversations"]}
    assert conv_a not in ids, "não-membro do time restrito não pode ver A na listagem"
    assert {conv_b, conv_c} <= ids, "time aberto e conversa sem time nunca somem (D4)"

    # D3 — mas o acesso DIRETO por ID continua liberado (200, nunca 404)
    r = client.get(f"/api/atendimentos/{conv_a}")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["conversation"]["id"] == conv_a

    # I4 — /filter (POST) e /count (POST) não podem divergir da listagem simples
    r = client.post("/api/atendimentos/filter", json={"limit": 200})
    assert r.status_code == 200, r.text
    ids_filter = {c["id"] for c in r.json()["data"]["conversations"]}
    assert conv_a not in ids_filter
    assert {conv_b, conv_c} <= ids_filter

    r = client.post("/api/atendimentos/count", json={})
    assert r.status_code == 200, r.text
    count_other = r.json()["data"]["all"]

    _auth(client, u_member)
    r = client.post("/api/atendimentos/count", json={})
    assert r.status_code == 200, r.text
    count_member = r.json()["data"]["all"]
    assert count_member == count_other + 1, (
        "count diverge da listagem em exatamente 1 (conv_a) — se sobrar/faltar mais, "
        "a cláusula não foi aplicada nos 3 pontos (I4/R1)"
    )

    # read_all ignora inbox, não a cerca de time (override é explícito/read_any).
    _auth(client, u_readall)
    r = client.get("/api/atendimentos?limit=200")
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()["data"]["conversations"]}
    assert conv_a not in ids
    assert {conv_b, conv_c} <= ids


# ── Exceção do assignee, aninhada em restrict_visibility (plano 155) ────────

def test_team_crud_visible_to_assignee(client):
    """I2/I6 (plano 155): create/update gravam e devolvem o campo; PUT parcial
    preserva o valor (None = não mexe, mesmo padrão do 154)."""
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)

    r = client.post("/api/teams", json={
        "name": "Assignee CRUD", "restrict_visibility": True, "visible_to_assignee": True})
    assert r.status_code == 200, r.text
    team = r.json()["data"]["team"]
    _CREATED_TEAM_IDS.append(team["id"])
    assert team["visible_to_assignee"] == 1

    r = client.get("/api/teams")
    assert r.status_code == 200, r.text
    listed = next(t for t in r.json()["data"]["teams"] if t["id"] == team["id"])
    assert listed["visible_to_assignee"] == 1

    r = client.put(f"/api/teams/{team['id']}", json={"name": "Assignee CRUD 2"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["team"]["visible_to_assignee"] == 1

    r = client.put(f"/api/teams/{team['id']}", json={"visible_to_assignee": False})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["team"]["visible_to_assignee"] == 0


def test_team_default_visible_to_assignee_is_off(client):
    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)
    r = client.post("/api/teams", json={"name": "Sem opt-in 155"})
    assert r.status_code == 200, r.text
    team = r.json()["data"]["team"]
    _CREATED_TEAM_IDS.append(team["id"])
    assert team["visible_to_assignee"] == 0


def test_visible_to_assignee_exempts_only_the_assignee(client):
    """Cenário central do plano 155: com restrict_visibility=1 E
    visible_to_assignee=1 num time, um atendente de FORA do time que seja o
    ASSIGNEE de uma conversa daquele time continua vendo ELA na listagem — mas
    outro atendente de fora do time, não atribuído a nada, continua sem ver
    (a exceção é POR CONVERSA/POR PESSOA, não reabre o time inteiro). O flag é
    ANINHADO: sem restrict_visibility, a exceção é moot (já visível a todos);
    sem visible_to_assignee, o assignee de fora do time também some (D1/154
    original — nenhuma hierarquia extra). Link direto continua SEMPRE 200
    (D3, intocado). conversation.read_all vê tudo, com ou sem o flag."""
    from db.repositories import (team_repo, team_member_repo, inbox_member_repo,
                                 conversation_repo)

    admin = _mk_user("teams_crud_admin@test.com", admin=True)
    _auth(client, admin)

    inbox_id = _mk_inbox("plan155_assignee_ch")

    team_exempt = team_repo.create("Plan155 Exceção",
                                   restrict_visibility=True, visible_to_assignee=True)
    team_no_exempt = team_repo.create("Plan155 Sem Exceção",
                                      restrict_visibility=True, visible_to_assignee=False)
    _CREATED_TEAM_IDS.extend([team_exempt["id"], team_no_exempt["id"]])

    u_member = _mk_user("plan155_member@test.com", custom_perms=["conversation.read"])
    u_assignee = _mk_user("plan155_assignee@test.com", custom_perms=["conversation.read"])
    u_bystander = _mk_user("plan155_bystander@test.com", custom_perms=["conversation.read"])
    u_readall = _mk_user("plan155_readall@test.com",
                         custom_perms=["conversation.read", "conversation.read_all"])

    inbox_member_repo.set_members(
        inbox_id, [u_member["id"], u_assignee["id"], u_bystander["id"]])
    team_member_repo.set_members(team_exempt["id"], [u_member["id"]])
    # u_assignee e u_bystander ficam de fora dos dois times (nem team_exempt, nem team_no_exempt)

    conv_a, _ = _open_conv_in_inbox("5511970200001", inbox_id)  # time COM exceção
    conv_b, _ = _open_conv_in_inbox("5511970200002", inbox_id)  # time SEM exceção
    conversation_repo.set_team(conv_a, team_exempt["id"])
    conversation_repo.set_team(conv_b, team_no_exempt["id"])
    # admin (ainda autenticado) atribui as duas a u_assignee via REST
    r = client.post(f"/api/atendimentos/{conv_a}/assign",
                    json={"assignee_user_id": u_assignee["id"]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/atendimentos/{conv_b}/assign",
                    json={"assignee_user_id": u_assignee["id"]})
    assert r.status_code == 200, r.text

    # u_assignee: fora dos dois times, mas é O ASSIGNEE das duas conversas.
    # Só vê A (time com a exceção ligada) — B continua escondida (D1/154 original).
    _auth(client, u_assignee)
    r = client.get(f"/api/atendimentos?inbox_id={inbox_id}&limit=200")
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()["data"]["conversations"]}
    assert conv_a in ids, "assignee de fora do time deve ver A (time com visible_to_assignee=1)"
    assert conv_b not in ids, "sem a exceção ligada no time B, nem o assignee vê (154 original)"

    # u_bystander: fora do time E não é assignee de nada — não vê nem A nem B,
    # mesmo com a exceção ligada em A (a exceção é individual, não reabre o time).
    _auth(client, u_bystander)
    r = client.get(f"/api/atendimentos?inbox_id={inbox_id}&limit=200")
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()["data"]["conversations"]}
    assert conv_a not in ids, "exceção é só do assignee — outro de fora do time não pode ver"
    assert conv_b not in ids

    # membro do time: vê A normalmente (member_team_ids, sem depender da exceção)
    _auth(client, u_member)
    r = client.get(f"/api/atendimentos?inbox_id={inbox_id}&limit=200")
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()["data"]["conversations"]}
    assert conv_a in ids

    # D3 — link direto sempre 200, pra qualquer um da caixa, com ou sem a exceção
    _auth(client, u_bystander)
    r = client.get(f"/api/atendimentos/{conv_a}")
    assert r.status_code == 200, r.text
    r = client.get(f"/api/atendimentos/{conv_b}")
    assert r.status_code == 200, r.text

    # /filter e /count não podem divergir da listagem simples (mesmo achado I4/154)
    _auth(client, u_assignee)
    r = client.post("/api/atendimentos/filter", json={"limit": 200})
    assert r.status_code == 200, r.text
    ids_filter = {c["id"] for c in r.json()["data"]["conversations"]}
    assert conv_a in ids_filter
    assert conv_b not in ids_filter

    # conversation.read_all não substitui conversation.team.read_any.
    _auth(client, u_readall)
    r = client.get(f"/api/atendimentos?limit=200")
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()["data"]["conversations"]}
    assert conv_a not in ids
    assert conv_b not in ids


def test_private_team_blocks_direct_messages_and_write_for_non_member(client):
    from db.repositories import inbox_member_repo, team_repo

    inbox_id = _mk_inbox("plan166_private_ch")
    member = _mk_user(
        "plan166_private_member@test.com",
        custom_perms=["conversation.read", "conversation.reply", "conversation.assign"])
    outsider = _mk_user(
        "plan166_private_outsider@test.com",
        custom_perms=["conversation.read", "conversation.reply",
                      "contact.read", "contact.write", "contact.delete"])
    override = _mk_user(
        "plan166_private_override@test.com",
        custom_perms=["conversation.read", "conversation.reply",
                      "conversation.team.read_any"])
    inbox_member_repo.set_members(
        inbox_id, [member["id"], outsider["id"], override["id"]])
    team = team_repo.create(
        "Plan166 Privado", enforce_team_access=True,
        member_user_ids=[member["id"]])
    _CREATED_TEAM_IDS.append(team["id"])
    assert team["access_mode"] == "private"
    assert team["restrict_visibility"] == 1

    conv_id, _contact_id = _open_conv_in_inbox("5511970300001", inbox_id)
    from db.repositories import conversation_repo
    conversation_repo.set_team(conv_id, team["id"])

    _auth(client, outsider)
    assert client.get(f"/api/atendimentos/{conv_id}").status_code == 404
    assert client.get(f"/api/atendimentos/{conv_id}/messages").status_code == 404
    assert client.post(
        f"/api/atendimentos/{conv_id}/read").status_code == 404
    assert client.patch(
        "/api/v1/contacts/5511970300001",
        json={"name": "Não deve alterar"}).status_code == 404

    _auth(client, member)
    assert client.get(f"/api/atendimentos/{conv_id}").status_code == 200
    assert client.get(f"/api/atendimentos/{conv_id}/messages").status_code == 200
    assert client.post(
        f"/api/atendimentos/{conv_id}/assign",
        json={"assignee_user_id": outsider["id"]}).status_code == 409

    _auth(client, override)
    assert client.get(f"/api/atendimentos/{conv_id}").status_code == 200


def test_team_assignment_requires_membership_or_assign_any(client):
    from db.repositories import inbox_member_repo, team_repo

    inbox_id = _mk_inbox("plan166_assignment_ch")
    own_user = _mk_user(
        "plan166_assign_own@test.com",
        custom_perms=["conversation.read", "conversation.team.assign"])
    outsider = _mk_user(
        "plan166_assign_out@test.com",
        custom_perms=["conversation.read", "conversation.team.assign"])
    any_user = _mk_user(
        "plan166_assign_any@test.com",
        custom_perms=["conversation.read", "conversation.team.assign_any"])
    inbox_member_repo.set_members(
        inbox_id, [own_user["id"], outsider["id"], any_user["id"]])
    team = team_repo.create("Plan166 Destino", member_user_ids=[own_user["id"]])
    _CREATED_TEAM_IDS.append(team["id"])
    conv_id, _contact_id = _open_conv_in_inbox("5511970300002", inbox_id)

    _auth(client, outsider)
    assert client.post(
        f"/api/atendimentos/{conv_id}/assign-team",
        json={"team_id": team["id"]}).status_code == 403

    _auth(client, own_user)
    assert client.post(
        f"/api/atendimentos/{conv_id}/assign-team",
        json={"team_id": team["id"]}).status_code == 200

    _auth(client, any_user)
    assert client.post(
        f"/api/atendimentos/{conv_id}/assign-team",
        json={"team_id": None}).status_code == 200


def test_private_team_media_requires_current_conversation_access(client):
    from db.repositories import (inbox_member_repo, message_repo, team_repo,
                                 conversation_repo)

    inbox_id = _mk_inbox("plan166_media_ch")
    member = _mk_user(
        "plan166_media_member@test.com", custom_perms=["conversation.read"])
    outsider = _mk_user(
        "plan166_media_outsider@test.com", custom_perms=["conversation.read"])
    inbox_member_repo.set_members(inbox_id, [member["id"], outsider["id"]])
    team = team_repo.create(
        "Plan166 Mídia Privada", enforce_team_access=True,
        member_user_ids=[member["id"]])
    _CREATED_TEAM_IDS.append(team["id"])
    conv_id, contact_id = _open_conv_in_inbox("5511970300003", inbox_id)
    conversation_repo.set_team(conv_id, team["id"])

    outbox = client.app.state.deps.statics_outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    media_file = outbox / "plan166-private.txt"
    media_file.write_bytes(b"conteudo privado")
    try:
        # Provedores Meta fazem pull do upload durante o envio, antes de existir
        # uma mensagem dona do path; essa janela estreita continua funcional.
        assert client.get(f"/statics/outbox/{media_file.name}").status_code == 200
        saved = message_repo.add(
            contact_id, "assistant", "arquivo", media_type="document",
            media_path=f"statics/outbox/{media_file.name}",
            conversation_id=conv_id)
        message_id = saved["id"]

        _auth(client, outsider)
        assert client.get(f"/api/messages/{message_id}/media").status_code == 404

        _auth(client, member)
        response = client.get(f"/api/messages/{message_id}/media")
        assert response.status_code == 200
        assert response.content == b"conteudo privado"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert client.get(f"/statics/outbox/{media_file.name}").status_code == 404

        # Reenvio/template reutilizado: o router abre somente a janela curta em
        # que o provedor externo precisa fazer pull do mesmo arquivo.
        from domain.media_access import grant_public_outbox
        grant_public_outbox(f"statics/outbox/{media_file.name}")
        assert client.get(f"/statics/outbox/{media_file.name}").status_code == 200
    finally:
        media_file.unlink(missing_ok=True)


def test_private_team_websocket_fanout_tracks_membership_changes():
    from db.repositories import (inbox_member_repo, team_member_repo, team_repo,
                                 conversation_repo)
    from server.state import ConnectionManager

    inbox_id = _mk_inbox("plan166_ws_ch")
    member = _mk_user(
        "plan166_ws_member@test.com", custom_perms=["conversation.read"])
    remaining_member = _mk_user(
        "plan166_ws_remaining@test.com", custom_perms=["conversation.read"])
    outsider = _mk_user(
        "plan166_ws_outsider@test.com", custom_perms=["conversation.read"])
    inbox_member_repo.set_members(
        inbox_id, [member["id"], remaining_member["id"], outsider["id"]])
    team = team_repo.create(
        "Plan166 Realtime Privado", enforce_team_access=True,
        member_user_ids=[member["id"], remaining_member["id"]])
    _CREATED_TEAM_IDS.append(team["id"])
    conv_id, _ = _open_conv_in_inbox("5511970300004", inbox_id)
    conversation_repo.set_team(conv_id, team["id"])

    class FakeSocket:
        def __init__(self):
            self.sent = []

        async def accept(self):
            return None

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            return None

    async def scenario():
        manager = ConnectionManager()
        allowed = FakeSocket()
        denied = FakeSocket()
        await manager.connect(allowed, member["id"])
        await manager.connect(denied, outsider["id"])

        await manager.broadcast(
            "new_message", {"conversation_id": conv_id, "content": "segredo"})
        assert [item["event"] for item in allowed.sent] == ["new_message"]
        assert denied.sent == []

        team_member_repo.set_members(team["id"], [remaining_member["id"]])
        await manager.broadcast(
            "new_message", {"conversation_id": conv_id, "content": "novo segredo"})
        assert len(allowed.sent) == 1, "a audiência deve ser recalculada por evento"
        assert denied.sent == []

    asyncio.run(scenario())
