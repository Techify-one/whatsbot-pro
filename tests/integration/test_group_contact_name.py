"""Nome de grupo: pré-preenchido pelo WhatsApp e não editável.

O nome de um grupo é o assunto que o provider reportou (``contacts.group_name``);
``contacts.name`` fica vazio. Dois efeitos travados aqui:

* a sidebar conversa-cêntrica (``contact_name`` do row enriquecido) mostra o nome do
  grupo — antes caía no JID, porque a query só trazia ``contacts.name``;
* o ``PUT /api/contacts/{phone}/info`` (mesmo serviço da ``/api/v1``) IGNORA ``name``
  para grupo, mas segue gravando o resto (observações etc.).

    venv/bin/python -m pytest tests/integration/test_group_contact_name.py -q
"""

from __future__ import annotations

import uuid

import pytest

from db.repositories import (contact_repo, conversation_repo, session_repo,
                             user_repo)
from server.auth import generate_session_token


@pytest.fixture
def admin_client(client):
    user = user_repo.create(
        email=f"grp-{uuid.uuid4().hex}@test.local", name="Grp",
        password_hash="test-only", role_keys=["admin"])
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
    client.headers.pop("Authorization", None)
    session_repo.delete(token)
    user_repo.delete(user["id"])


def _make_contact(phone: str, *, is_group: bool, name: str = "", group_name: str = ""):
    contact = contact_repo.get_or_create(phone)
    fields = {"is_group": 1 if is_group else 0}
    if name:
        fields["name"] = name
    if group_name:
        fields["group_name"] = group_name
    contact_repo.update(contact["id"], **fields)
    return contact["id"]


def _open_conv(contact_id: int, jid: str) -> int:
    conv, _ = conversation_repo.resolve_for_contact_ex(contact_id, jid)
    return conv["id"]


@pytest.fixture
def cleanup():
    ids: list[int] = []
    yield ids
    for cid in ids:
        contact_repo.delete(cid)


def _group_phone() -> str:
    return f"1203{uuid.uuid4().int % 10**14:014d}@g.us"


def test_conversation_row_carries_the_group_name(_engine_ready, cleanup):
    phone = _group_phone()
    cid = _make_contact(phone, is_group=True, group_name="Equipe Comercial")
    cleanup.append(cid)
    conv_id = _open_conv(cid, phone)

    assert conversation_repo.get_with_channel(conv_id)["contact_name"] == "Equipe Comercial"
    # o mesmo row alimenta o conversation_upsert do WS
    assert conversation_repo.get_row_for_broadcast(conv_id)["contact_name"] == "Equipe Comercial"


def test_group_without_synced_name_falls_back_to_contact_name(_engine_ready, cleanup):
    phone = _group_phone()
    cid = _make_contact(phone, is_group=True)
    cleanup.append(cid)
    conv_id = _open_conv(cid, phone)

    assert conversation_repo.get_with_channel(conv_id)["contact_name"] == ""


def test_person_keeps_its_own_name_even_with_a_stray_group_name(_engine_ready, cleanup):
    phone = f"5511{uuid.uuid4().int % 10**9:09d}"
    cid = _make_contact(phone, is_group=False, name="Bia", group_name="lixo")
    cleanup.append(cid)
    conv_id = _open_conv(cid, f"{phone}@s.whatsapp.net")

    assert conversation_repo.get_with_channel(conv_id)["contact_name"] == "Bia"


def test_panel_save_ignores_name_for_a_group_but_keeps_the_rest(admin_client, cleanup):
    phone = _group_phone()
    cid = _make_contact(phone, is_group=True, group_name="Equipe Comercial")
    cleanup.append(cid)

    r = admin_client.put(f"/api/contacts/{phone}/info",
                         json={"name": "Renomeado", "observations": ["anotação"]})
    assert r.status_code == 200, r.text

    row = contact_repo.get_by_phone(phone)
    assert row["name"] == ""                       # nome do grupo não é editável
    assert row["group_name"] == "Equipe Comercial"
    full = contact_repo.get_full_contact(phone)
    assert full["info"]["observations"] == ["anotação"]   # o resto continua gravando


def test_panel_save_still_renames_a_person(admin_client, cleanup):
    phone = f"5511{uuid.uuid4().int % 10**9:09d}"
    cid = _make_contact(phone, is_group=False, name="Bia")
    cleanup.append(cid)

    r = admin_client.put(f"/api/contacts/{phone}/info", json={"name": "Beatriz"})
    assert r.status_code == 200, r.text
    assert contact_repo.get_by_phone(phone)["name"] == "Beatriz"
