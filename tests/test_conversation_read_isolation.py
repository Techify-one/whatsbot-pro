"""Plano 42 WS B — isolamento de leitura por membership de inbox (Defeito #2).

O Defeito #2 (ler conversa de outro canal) foi confirmado como CONSEQUÊNCIA do
Defeito #1 (source_id fantasma no inbox WhatsApp) — o backend JÁ aplica
``visible_inbox_ids`` nos 4 pontos de leitura. Este teste TRAVA esse isolamento
contra regressão de um handler futuro esquecido: um usuário membro só do inbox A,
com ``conversation.read`` mas SEM ``conversation.read_all`` / não-admin, precisa:
  * 404 no GET de uma conversa (e mensagens) que vive no inbox B,
  * NÃO ver a conversa de B na lista,
  * mas VER a sua própria (controle — prova que o 404 é scoping, não negação total).

    venv/bin/python -m pytest tests/test_conversation_read_isolation.py -q
"""

from __future__ import annotations

import pytest

from db.repositories import (channel_repo, contact_repo, conversation_repo,
                             inbox_member_repo, inbox_repo, user_repo)
from server.auth import hash_password_argon2


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
