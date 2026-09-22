"""Roteamento real do WebSocket (plano 168 — Fase 0 · caracterização).

Diferente do espião em ``plugins.context.broadcast`` (que intercepta ANTES do
roteamento e por isso nunca testou o roteador de verdade), este módulo chama
``ConnectionManager.broadcast`` diretamente, com sockets falsos plugados em
``manager.active``/``manager._user_ids`` — o mesmo padrão de
``tests/integration/test_teams.py::test_private_team_websocket_fanout_tracks_membership_changes``.

Os casos ``xfail`` reproduzem o bug do commit ``4245ac5`` (broadcast passou a
exigir um ``conversation_id`` provado para TODO evento de conversa e descarta em
silêncio o que não consegue provar — o ``conversation_upsert``, que roteia pela
chave ``id``, nunca mais chegou a navegador nenhum). A Fase 1 os torna verdes;
nesse ponto o marcador ``xfail`` é removido (não fica "documentando" um bug já
corrigido)."""

from __future__ import annotations

import asyncio
import json

import pytest

_CREATED_USER_IDS: list[int] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_ws_audience_users(_engine_ready):
    yield
    from db.repositories import session_repo, user_repo

    for user_id in reversed(_CREATED_USER_IDS):
        session_repo.delete_for_user(user_id)
        assert user_repo.delete(user_id), f"could not remove ws_audience test user {user_id}"
    _CREATED_USER_IDS.clear()


def _mk_user(email: str, *, custom_perms=None):
    from db.repositories import user_repo
    user = user_repo.get_by_email(email)
    if user is None:
        user = user_repo.create(
            email=email, name="WS Audience Test", password_hash="x", role_keys=[])
        _CREATED_USER_IDS.append(user["id"])
        if custom_perms is not None:
            user_repo.set_custom_permissions(user["id"], list(custom_perms))
    return user_repo.get(user["id"])


def _mk_inbox(channel_id: str) -> int:
    """Inbox dedicado — mesmo padrão de ``tests/integration/test_teams.py:_mk_inbox``,
    para não mexer na membership do inbox default compartilhado com o resto da suíte."""
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


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def accept(self):
        return None

    async def send_text(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        return None


def _scenario(coro):
    """Run an async scenario synchronously (mirrors test_teams.py's pattern)."""
    return asyncio.run(coro)


@pytest.fixture
def _audience(_engine_ready):
    """Um inbox dedicado + um usuário MEMBRO e um NÃO-MEMBRO, cada um com uma
    conversa aberta própria (mesmo contato reaproveitado entre testes seria
    ambíguo — cada teste abre o seu telefone)."""
    inbox_id = _mk_inbox("plan168_ws_audience_ch")
    member = _mk_user(
        "plan168_ws_member@test.com", custom_perms=["conversation.read"])
    outsider = _mk_user(
        "plan168_ws_outsider@test.com", custom_perms=["conversation.read"])
    from db.repositories import inbox_member_repo
    inbox_member_repo.set_members(inbox_id, [member["id"]])
    return {"inbox_id": inbox_id, "member": member, "outsider": outsider}


# ── Caracterização: o que já funciona hoje ──────────────────────────────────

def test_new_message_with_conversation_id_routes_to_inbox_member_only(_audience):
    from server.state import ConnectionManager

    conv_id, _contact_id = _open_conv_in_inbox(
        "5511970400001", _audience["inbox_id"])

    async def scenario():
        manager = ConnectionManager()
        allowed = FakeSocket()
        denied = FakeSocket()
        await manager.connect(allowed, _audience["member"]["id"])
        await manager.connect(denied, _audience["outsider"]["id"])

        await manager.broadcast(
            "new_message", {"conversation_id": conv_id, "content": "olá"})
        assert [item["event"] for item in allowed.sent] == ["new_message"]
        assert denied.sent == []

    _scenario(scenario())


def test_global_event_reaches_every_connected_socket(_audience):
    """Evento fora de ``CONVERSATION_EVENTS`` (ex.: ``status``) continua indo a
    todo mundo — nunca passou pela audiência por conversa."""
    from server.state import ConnectionManager

    async def scenario():
        manager = ConnectionManager()
        one = FakeSocket()
        two = FakeSocket()
        await manager.connect(one, _audience["member"]["id"])
        await manager.connect(two, _audience["outsider"]["id"])

        await manager.broadcast("status", {"connected": True})
        assert [item["event"] for item in one.sent] == ["status"]
        assert [item["event"] for item in two.sent] == ["status"]

    _scenario(scenario())


# ── Reprodução do bug do commit 4245ac5 — verdes só a partir da Fase 1 ──────

def test_conversation_upsert_reaches_inbox_member(_audience):
    """E1 da tabela 3.1: o payload do ``conversation_upsert`` é a linha inteira
    da lista (``get_row_for_broadcast``) — tem ``id``, não ``conversation_id``."""
    from db.repositories import conversation_repo
    from server.state import ConnectionManager

    conv_id, _contact_id = _open_conv_in_inbox(
        "5511970400002", _audience["inbox_id"])
    row = conversation_repo.get_row_for_broadcast(conv_id)
    assert row is not None and row.get("id") == conv_id

    async def scenario():
        manager = ConnectionManager()
        allowed = FakeSocket()
        await manager.connect(allowed, _audience["member"]["id"])

        await manager.broadcast("conversation_upsert", row)
        assert [item["event"] for item in allowed.sent] == ["conversation_upsert"], (
            "conversation_upsert deve chegar ao membro do inbox — hoje é DROP "
            "sempre porque o roteador só reconhece a chave conversation_id")

    _scenario(scenario())


def test_non_conversation_event_with_uuid_id_is_not_dropped(_audience):
    """E10: evento de plugin FORA de ``CONVERSATION_EVENTS`` cujo payload usa
    ``conversation_id`` como uuid hex (``storages/plugins/melhorias``) não deve
    fazer o roteador levantar nem impedir a entrega — antes do 4245ac5 esse
    evento era fan-out global; ``int()` de um hex com letras a-f derruba o
    ``broadcast`` inteiro hoje."""
    from server.state import ConnectionManager

    async def scenario():
        manager = ConnectionManager()
        one = FakeSocket()
        await manager.connect(one, _audience["member"]["id"])

        await manager.broadcast(
            "plugin_melhorias_changed", {"conversation_id": "deadbeefcafefeed"})
        assert [item["event"] for item in one.sent] == ["plugin_melhorias_changed"]

    _scenario(scenario())


def test_broadcast_never_raises_when_resolver_blows_up(_audience, monkeypatch):
    """R1: uma exceção dentro do resolvedor (DB fora, dado sujo) não pode subir
    pelo ``await manager.broadcast(...)`` do webhook — hoje sobe, e o chamador
    (``message_ingest_service.py``) não tem try em volta, perdendo a mensagem do
    cliente já marcada como processada."""
    from db.repositories import message_repo
    from server.state import ConnectionManager

    def _boom(_msg_id):
        raise RuntimeError("db fora do ar")

    monkeypatch.setattr(message_repo, "get_by_msg_id", _boom)

    async def scenario():
        manager = ConnectionManager()
        one = FakeSocket()
        await manager.connect(one, _audience["member"]["id"])

        # message_status é evento de conversa; sem conversation_id nem db_id,
        # só msg_id ⇒ força o resolvedor, que aqui está quebrado.
        result = await manager.broadcast("message_status", {"msg_id": "abc123"})
        assert result is None

    _scenario(scenario())


# ── Não-vazamento: o upsert continua respeitando a audiência ───────────────

def test_conversation_upsert_does_not_leak_to_non_member(_audience):
    """Mesmo depois da Fase 1, quem não é membro do inbox não pode receber o
    upsert — a correção é o roteamento chegar até a checagem de audiência, não
    abrir mão dela (D2)."""
    from db.repositories import conversation_repo
    from server.state import ConnectionManager

    conv_id, _contact_id = _open_conv_in_inbox(
        "5511970400003", _audience["inbox_id"])
    row = conversation_repo.get_row_for_broadcast(conv_id)

    async def scenario():
        manager = ConnectionManager()
        denied = FakeSocket()
        await manager.connect(denied, _audience["outsider"]["id"])

        await manager.broadcast("conversation_upsert", row)
        assert denied.sent == []

    _scenario(scenario())


# ── Fase 2: produtores levam channel_id/conversation_id (DROP-MULTI) ───────

def test_channel_id_disambiguates_a_contact_with_two_open_conversations():
    """E3/E4/E5/E6 etc.: um contato com conversas abertas em DOIS canais só era
    roteável (pré-F2) quando tinha exatamente UMA — com duas, o resolvedor por
    ``phone`` sozinho desistia (DROP-MULTI). Os produtores agora mandam
    ``channel_id`` junto do ``phone``, o que o resolvedor já sabia usar
    (``get_latest_for_contact_inbox``) — este teste prova que a combinação
    entrega para o inbox CERTO e não para o outro."""
    from db.repositories import inbox_member_repo
    from server.state import ConnectionManager

    inbox_a = _mk_inbox("plan168_f2_multi_ch_a")
    inbox_b = _mk_inbox("plan168_f2_multi_ch_b")
    member_a = _mk_user(
        "plan168_f2_member_a@test.com", custom_perms=["conversation.read"])
    member_b = _mk_user(
        "plan168_f2_member_b@test.com", custom_perms=["conversation.read"])
    inbox_member_repo.set_members(inbox_a, [member_a["id"]])
    inbox_member_repo.set_members(inbox_b, [member_b["id"]])

    phone = "5511970400010"
    _conv_a, _ = _open_conv_in_inbox(phone, inbox_a)
    _conv_b, _ = _open_conv_in_inbox(phone, inbox_b)

    async def scenario():
        manager = ConnectionManager()
        socket_a = FakeSocket()
        socket_b = FakeSocket()
        await manager.connect(socket_a, member_a["id"])
        await manager.connect(socket_b, member_b["id"])

        await manager.broadcast(
            "messages_read", {"phone": phone, "channel_id": "plan168_f2_multi_ch_b",
                              "only_user": True})
        assert [item["event"] for item in socket_b.sent] == ["messages_read"], (
            "sem channel_id no payload, dois abertos = DROP; com ele, deve "
            "chegar ao membro do canal certo")
        assert socket_a.sent == [], "não pode vazar para o membro do OUTRO canal"

    _scenario(scenario())


def test_resolver_ignores_msg_id_collision_across_channels():
    """I6/R2: ``msg_id`` só é único DENTRO de um canal (provedores como Telegram
    emitem inteiros pequenos). Duas mensagens de CANAIS diferentes com o MESMO
    ``msg_id`` não podem fazer um ``message_status`` vazar para a audiência do
    canal errado quando o payload também informa ``channel_id``."""
    from db.repositories import inbox_member_repo, message_repo
    from server.state import ConnectionManager

    inbox_a = _mk_inbox("plan168_f2_collide_ch_a")
    inbox_b = _mk_inbox("plan168_f2_collide_ch_b")
    member_a = _mk_user(
        "plan168_f2_collide_member_a@test.com", custom_perms=["conversation.read"])
    member_b = _mk_user(
        "plan168_f2_collide_member_b@test.com", custom_perms=["conversation.read"])
    inbox_member_repo.set_members(inbox_a, [member_a["id"]])
    inbox_member_repo.set_members(inbox_b, [member_b["id"]])

    conv_a, contact_a = _open_conv_in_inbox("5511970400011", inbox_a)
    conv_b, contact_b = _open_conv_in_inbox("5511970400012", inbox_b)
    shared_msg_id = "42"
    message_repo.add(contact_a, "assistant", "oi de A", msg_id=shared_msg_id,
                     conversation_id=conv_a, status="sent")
    message_repo.add(contact_b, "assistant", "oi de B", msg_id=shared_msg_id,
                     conversation_id=conv_b, status="sent")

    async def scenario():
        manager = ConnectionManager()
        socket_a = FakeSocket()
        socket_b = FakeSocket()
        await manager.connect(socket_a, member_a["id"])
        await manager.connect(socket_b, member_b["id"])

        await manager.broadcast("message_status", {
            "msg_id": shared_msg_id, "channel_id": "plan168_f2_collide_ch_b",
            "phone": "5511970400012", "status": "read",
        })
        assert [item["event"] for item in socket_b.sent] == ["message_status"]
        assert socket_a.sent == [], (
            "o msg_id colidido pertence à conversa de A — sem o filtro por "
            "canal (I6) o resolvedor acharia a linha de A primeiro (get_by_msg_id "
            "sem escopo) e vazaria para o membro errado")

    _scenario(scenario())
