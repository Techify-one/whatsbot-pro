"""Plano 42 WS B — isolamento de leitura por membership de inbox (Defeito #2).

O Defeito #2 (ler conversa de outro canal) foi confirmado como CONSEQUÊNCIA do
Defeito #1 (source_id fantasma no inbox WhatsApp) — o backend JÁ aplica
``visible_inbox_ids`` nos 4 pontos de leitura. Este teste TRAVA esse isolamento
contra regressão de um handler futuro esquecido: um usuário membro só do inbox A,
com ``conversation.read`` mas SEM ``conversation.read_all`` / não-admin, precisa:
  * 404 no GET de uma conversa (e mensagens) que vive no inbox B,
  * NÃO ver a conversa de B na lista,
  * mas VER a sua própria (controle — prova que o 404 é scoping, não negação total).

    venv/bin/python -m pytest tests/integration/test_conversation_read_isolation.py -q
"""

from __future__ import annotations

import pytest

from db.repositories import (channel_repo, contact_repo, conversation_repo,
                             inbox_member_repo, inbox_repo, user_repo)
from server.auth import hash_password_argon2


_CREATED_USER_IDS: list[int] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_isolation_users(_engine_ready):
    """Remove module-owned identities from the process-global test database."""
    yield
    from db.repositories import session_repo

    for user_id in reversed(_CREATED_USER_IDS):
        session_repo.delete_for_user(user_id)
        assert user_repo.delete(user_id), (
            f"could not remove conversation-isolation user {user_id}"
        )
    _CREATED_USER_IDS.clear()


def _mk_inbox(channel_id: str) -> int:
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    inbox = inbox_repo.get_by_channel(channel_id) or inbox_repo.create(
        channel_id=channel_id, name=channel_id)
    return inbox["id"]


def _mk_conversation(phone: str, inbox_id: int) -> int:
    contact = contact_repo.get_or_create(phone)
    conv, _ = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net", inbox_id=inbox_id)
    return conv["id"]


def _scoped_user(email: str, inbox_id: int) -> int:
    """Usuário custom com APENAS conversation.read, membro só de ``inbox_id``.
    Idempotente (o DB de teste é compartilhado entre as funções deste módulo)."""
    existing = user_repo.get_by_email(email)
    if existing:
        user_id = existing["id"]
    else:
        user_id = user_repo.create(
            email=email, name=email.split("@")[0],
            password_hash=hash_password_argon2("supersecret"),
            permission_keys=["conversation.read"], custom=True)["id"]
        _CREATED_USER_IDS.append(user_id)
    inbox_member_repo.set_members(inbox_id, [user_id])
    return user_id


def _login(client, email: str) -> dict:
    r = client.post("/api/auth/login",
                    json={"email": email, "password": "supersecret"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


@pytest.fixture
def isolation_env(build_app):
    built = build_app(["gowa"])
    inbox_a = _mk_inbox("iso_ch_a")
    inbox_b = _mk_inbox("iso_ch_b")
    conv_a = _mk_conversation("5511977770001", inbox_a)
    conv_b = _mk_conversation("5511977770002", inbox_b)
    _scoped_user("iso_member_a@test.com", inbox_a)
    headers = _login(built.client, "iso_member_a@test.com")
    return built.client, headers, conv_a, conv_b


def test_get_conversation_in_other_inbox_is_404(isolation_env):
    client, headers, _conv_a, conv_b = isolation_env
    r = client.get(f"/api/atendimentos/{conv_b}", headers=headers)
    assert r.status_code == 404, r.text
    assert r.json() == {"ok": False, "error": "Conversa não encontrada."}


def test_get_messages_in_other_inbox_is_404(isolation_env):
    client, headers, _conv_a, conv_b = isolation_env
    r = client.get(f"/api/atendimentos/{conv_b}/messages", headers=headers)
    assert r.status_code == 404, r.text
    assert r.json() == {"ok": False, "error": "Conversa não encontrada."}


def test_own_inbox_conversation_is_visible(isolation_env):
    """Controle: o usuário LÊ a conversa do seu próprio inbox (prova que o 404 é
    scoping por inbox, não uma negação geral)."""
    client, headers, conv_a, _conv_b = isolation_env
    assert client.get(f"/api/atendimentos/{conv_a}",
                      headers=headers).status_code == 200
    assert client.get(f"/api/atendimentos/{conv_a}/messages",
                      headers=headers).status_code == 200


def test_list_excludes_other_inbox(isolation_env):
    client, headers, conv_a, conv_b = isolation_env
    r = client.get("/api/atendimentos", headers=headers)
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json()["data"]["conversations"]]
    assert conv_a in ids
    assert conv_b not in ids


# ---------------------------------------------------------------------------
# Plano 89 · F5 — CONTRATO: dentro do inbox de que o usuário é membro, a leitura
# de uma conversa NÃO tem noção de DONO.
#
# O bloco acima trava o escopo por INBOX (o que é isolamento de verdade). Estes
# casos travam o oposto e são igualmente importantes: `get_with_channel` filtra
# SÓ por id — sem `assignee_user_id`, sem `status`, sem `is_archived` — e isso é
# DELIBERADO, não acidente de implementação.
#
# POR QUE é contrato: um link de conversa (`/conversations/<id>`) é um endereço
# PERMANENTE. Todo permalink do produto depende disso — os que o plugin de
# protocolos manda no fio da conversa, os de melhorias/agendamento, e o caso mais
# comum de todos: um atendente colando no chat interno o link de uma conversa que
# ele está atendendo para um colega olhar. Escopar esta leitura por
# `assignee_user_id` achando que "conversa alheia é privacidade" quebraria TODOS
# eles de uma vez, em silêncio (o painel simplesmente não abriria).
#
# Privacidade aqui é membership de CANAL (o bloco acima), nunca atribuição.
# ---------------------------------------------------------------------------


def _plain_user(email: str) -> int:
    """Usuário SEM membership nenhuma — só para ser o dono de uma conversa alheia."""
    existing = user_repo.get_by_email(email)
    if existing:
        return existing["id"]
    user_id = user_repo.create(
        email=email, name=email.split("@")[0],
        password_hash=hash_password_argon2("supersecret"),
        permission_keys=["conversation.read"], custom=True)["id"]
    _CREATED_USER_IDS.append(user_id)
    return user_id


@pytest.fixture
def ownership_env(build_app):
    """Quatro conversas no MESMO inbox do usuário, em estados que um leitor
    desavisado poderia achar que deveriam bloquear a leitura."""
    built = build_app(["gowa"])
    inbox_a = _mk_inbox("iso_ch_a")
    _scoped_user("iso_member_a@test.com", inbox_a)   # membro, sem read_all, não-admin
    other_id = _plain_user("iso_other_owner@test.com")

    convs = {
        "assigned_to_other": _mk_conversation("5511977770010", inbox_a),
        "unassigned": _mk_conversation("5511977770011", inbox_a),
        "archived": _mk_conversation("5511977770012", inbox_a),
        "closed": _mk_conversation("5511977770013", inbox_a),
    }
    conversation_repo.set_assignee(convs["assigned_to_other"], other_id)
    conversation_repo.set_assignee(convs["unassigned"], None)
    conversation_repo.set_archived(convs["archived"], 1)
    conversation_repo.set_status(convs["closed"], "closed")   # "Resolvida" na UI

    headers = _login(built.client, "iso_member_a@test.com")
    return built.client, headers, convs


@pytest.mark.parametrize("state", ["assigned_to_other", "unassigned",
                                   "archived", "closed"])
def test_read_is_not_scoped_by_ownership(ownership_env, state):
    """200 no GET da conversa E das mensagens — em todos os quatro estados."""
    client, headers, convs = ownership_env
    conv_id = convs[state]
    r = client.get(f"/api/atendimentos/{conv_id}", headers=headers)
    assert r.status_code == 200, f"{state}: {r.text}"
    assert r.json()["data"]["conversation"]["id"] == conv_id
    r = client.get(f"/api/atendimentos/{conv_id}/messages", headers=headers)
    assert r.status_code == 200, f"{state}: {r.text}"
