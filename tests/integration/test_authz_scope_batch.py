"""Paridade e custo do escopo em lote (plano 168 — Fase 3).

``ConversationAccessScope.for_users`` precisa devolver, PARA CADA usuário, o
MESMO resultado que ``for_user`` devolveria isoladamente — só a divergência já
seria vazar (ou esconder) conversa de time privado. E o ganho inteiro da fase
só existe se o número de consultas ao Postgres deixar de crescer com o número
de sockets conectados."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import event

_CREATED_USER_IDS: list[int] = []
_CREATED_TEAM_IDS: list[int] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup(_engine_ready):
    yield
    from db.repositories import session_repo, team_repo, user_repo

    for user_id in reversed(_CREATED_USER_IDS):
        session_repo.delete_for_user(user_id)
        assert user_repo.delete(user_id), f"could not remove test user {user_id}"
    _CREATED_USER_IDS.clear()
    for team_id in reversed(_CREATED_TEAM_IDS):
        try:
            team_repo.delete(team_id)
        except team_repo.TeamHasConversations:
            team_repo.deactivate(team_id)
    _CREATED_TEAM_IDS.clear()


def _mk_user(email: str, *, admin: bool = False, custom_perms=None, role_keys=None):
    from db.repositories import user_repo
    user = user_repo.get_by_email(email)
    if user is None:
        user = user_repo.create(
            email=email, name="AuthzBatch Test", password_hash="x",
            role_keys=(["admin"] if admin else (role_keys or [])),
        )
        _CREATED_USER_IDS.append(user["id"])
        if custom_perms is not None:
            user_repo.set_custom_permissions(user["id"], list(custom_perms))
    return user_repo.get(user["id"])


def _mk_inbox(channel_id: str) -> int:
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


# ── Paridade for_users([u])[u] == for_user(u) ───────────────────────────────

def test_for_users_matches_for_user_across_configurations():
    from db.repositories import inbox_member_repo, team_member_repo, team_repo, user_repo
    from server.authz import ConversationAccessScope

    inbox_id = _mk_inbox("plan168_f3_parity_ch")

    admin = _mk_user("plan168_f3_admin@test.com", admin=True)
    custom_read_all = _mk_user(
        "plan168_f3_custom_readall@test.com",
        custom_perms=["conversation.read", "conversation.read_all"])
    custom_plain = _mk_user(
        "plan168_f3_custom_plain@test.com", custom_perms=["conversation.read"])
    inactive = _mk_user(
        "plan168_f3_inactive@test.com", custom_perms=["conversation.read"])
    user_repo.update_info(inactive["id"], is_active=0)

    member_private = _mk_user(
        "plan168_f3_member_private@test.com", custom_perms=["conversation.read"])
    non_member = _mk_user(
        "plan168_f3_non_member@test.com", custom_perms=["conversation.read"])
    read_any_team = _mk_user(
        "plan168_f3_read_any_team@test.com",
        custom_perms=["conversation.read", "conversation.team.read_any"])

    # Um time PRIVADO exige >=1 membro já na criação (team_repo.create).
    team_private = team_repo.create(
        "Plan168 F3 Privado", enforce_team_access=True,
        member_user_ids=[member_private["id"]])
    team_hidden = team_repo.create(
        "Plan168 F3 Oculto", restrict_visibility=True)
    team_assignee = team_repo.create(
        "Plan168 F3 Assignee", enforce_team_access=True, visible_to_assignee=True,
        member_user_ids=[member_private["id"]])
    _CREATED_TEAM_IDS.extend([team_private["id"], team_hidden["id"], team_assignee["id"]])

    inbox_member_repo.set_members(inbox_id, [
        admin["id"], custom_read_all["id"], custom_plain["id"], inactive["id"],
        member_private["id"], non_member["id"], read_any_team["id"],
    ])

    # ``inactive`` fica de FORA do laço de paridade de propósito: I9 é uma
    # divergência DELIBERADA de ``for_users`` (nega tudo a quem foi desativado)
    # que ``for_user`` nunca teve — ele não olha ``is_active``. Coberto à parte
    # logo abaixo.
    all_users = [
        admin, custom_read_all, custom_plain,
        member_private, non_member, read_any_team,
        None,  # legacy/open (no identity) — for_user(None) too
    ]
    user_ids = {(u["id"] if u else None) for u in all_users} | {inactive["id"]}
    batched = ConversationAccessScope.for_users(user_ids)

    for u in all_users:
        uid = u["id"] if u else None
        solo = ConversationAccessScope.for_user(uid)
        got = batched[uid]
        assert got.user_id == solo.user_id, uid
        assert got.inbox_ids == solo.inbox_ids, (uid, got.inbox_ids, solo.inbox_ids)
        assert got.team_ids == solo.team_ids, uid
        assert got.read_any_team == solo.read_any_team, uid
        assert set(got.teams_by_id) == set(solo.teams_by_id), uid

    # Deactivated user (I9): denies everything, not "inherits real membership".
    inactive_scope = batched[inactive["id"]]
    assert inactive_scope.inbox_ids == [], "usuário desativado deve ter inbox_ids=[] (nega tudo)"


def test_for_users_allows_decisions_match_for_user_on_real_conversations():
    """Não basta os campos baterem — o veredito de ``.allows()`` nas conversas
    reais dos três modos de time (154/155/166) tem de ser idêntico."""
    from db.repositories import (conversation_repo, inbox_member_repo,
                                 team_member_repo, team_repo)
    from server.authz import ConversationAccessScope

    inbox_id = _mk_inbox("plan168_f3_allows_ch")
    member = _mk_user("plan168_f3_allows_member@test.com", custom_perms=["conversation.read"])
    outsider = _mk_user("plan168_f3_allows_outsider@test.com", custom_perms=["conversation.read"])

    team_restricted = team_repo.create("Plan168 F3 Allows Restrito", restrict_visibility=True)
    team_private = team_repo.create(
        "Plan168 F3 Allows Privado", enforce_team_access=True,
        member_user_ids=[member["id"]])
    team_exempt = team_repo.create(
        "Plan168 F3 Allows Exceção", enforce_team_access=True, visible_to_assignee=True,
        member_user_ids=[member["id"]])
    _CREATED_TEAM_IDS.extend([team_restricted["id"], team_private["id"], team_exempt["id"]])

    inbox_member_repo.set_members(inbox_id, [member["id"], outsider["id"]])
    team_member_repo.set_members(team_restricted["id"], [member["id"]])

    conv_restricted, _ = _open_conv_in_inbox("5511970500001", inbox_id)
    conv_private, _ = _open_conv_in_inbox("5511970500002", inbox_id)
    conv_exempt, _ = _open_conv_in_inbox("5511970500003", inbox_id)
    conversation_repo.set_team(conv_restricted, team_restricted["id"])
    conversation_repo.set_team(conv_private, team_private["id"])
    conversation_repo.set_team(conv_exempt, team_exempt["id"])
    conversation_repo.set_assignee(conv_exempt, outsider["id"])

    convs = {
        "restricted": conversation_repo.get(conv_restricted),
        "private": conversation_repo.get(conv_private),
        "exempt": conversation_repo.get(conv_exempt),
    }

    batched = ConversationAccessScope.for_users({member["id"], outsider["id"]})
    for uid in (member["id"], outsider["id"]):
        solo = ConversationAccessScope.for_user(uid)
        got = batched[uid]
        for surface in ("list", "direct"):
            for label, conv in convs.items():
                assert got.allows(conv, surface) == solo.allows(conv, surface), (
                    uid, surface, label)


# ── Custo constante (I8) ────────────────────────────────────────────────────

class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def accept(self):
        return None

    async def send_text(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        return None


def test_broadcast_conversation_query_count_is_constant_in_connected_sockets():
    """Antes da F3: ~1 query de conversa + N usuários × (2-3 de permissão + 1 de
    inbox + 1 de time + 1 de team_repo.list_all) — crescia linear com sockets.
    Depois: um pequeno número FIXO de queries, batched, não importa quantos
    sockets estão conectados."""
    from db.repositories import inbox_member_repo
    from db.engine import get_engine
    from server.state import ConnectionManager

    inbox_id = _mk_inbox("plan168_f3_cost_ch")
    conv_id, _contact_id = _open_conv_in_inbox("5511970500010", inbox_id)

    users = [
        _mk_user(f"plan168_f3_cost_{i}@test.com", custom_perms=["conversation.read"])
        for i in range(10)
    ]
    inbox_member_repo.set_members(inbox_id, [u["id"] for u in users])

    async def scenario():
        manager = ConnectionManager()
        for u in users:
            await manager.connect(FakeSocket(), u["id"])

        queries = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", _count)
        try:
            await manager.broadcast(
                "new_message", {"conversation_id": conv_id, "content": "olá"})
        finally:
            event.remove(engine, "before_cursor_execute", _count)

        # Generous ceiling: conversation row + users/custom/roles/admin-perms +
        # inbox_members + team_members + teams.list_all — a HANDFUL, not
        # one group of round trips PER connected socket (10 sockets here; the
        # pre-F3 code paid ~14 per socket ≈ 140).
        assert len(queries) <= 10, (
            f"esperava um número de queries CONSTANTE (não por socket): "
            f"{len(queries)} queries para 10 sockets conectados"
        )

    asyncio.run(scenario())
