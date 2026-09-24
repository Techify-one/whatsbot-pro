"""Filtro "Tipo de conversa" (grupo x individual) do painel de conversas.

A dimensão ``chat_type`` do motor de filtros (``db/filters``) responde "apenas grupos" /
"exceto grupos" sobre ``contacts.is_group``. Travado aqui, pelas rotas HTTP reais:

* ``chat_type=group`` devolve só grupos; ``chat_type=individual`` e
  ``chat_type=group&chat_type__op=not_equal_to`` devolvem só o resto (as duas formas de
  "exceto grupos" concordam);
* a LISTA e a CONTAGEM leem o mesmo WHERE (o invariante do plano 69) — o cliente manda
  os mesmos params para as duas;
* valor fora do vocabulário é 400, nunca um SQL estranho;
* a dimensão aparece no ``filter-schema`` (o motor se autodescreve).

    venv/bin/python -m pytest tests/integration/test_chat_type_filter.py -q
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
        email=f"chat-type-{uuid.uuid4().hex}@test.local", name="ChatType",
        password_hash="test-only", role_keys=["admin"])
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
    client.headers.pop("Authorization", None)
    session_repo.delete(token)
    user_repo.delete(user["id"])


@pytest.fixture
def seeded(_engine_ready):
    """Um grupo e uma pessoa, cada um com uma conversa aberta."""
    created: list[int] = []

    group_phone = f"1203{uuid.uuid4().int % 10**14:014d}@g.us"
    group = contact_repo.get_or_create(group_phone)
    contact_repo.update(group["id"], is_group=1, group_name="Equipe Comercial")
    created.append(group["id"])
    group_conv, _ = conversation_repo.resolve_for_contact_ex(group["id"], group_phone)

    person_phone = f"5511{uuid.uuid4().int % 10**9:09d}"
    person = contact_repo.get_or_create(person_phone)
    contact_repo.update(person["id"], is_group=0, name="Bia")
    created.append(person["id"])
    person_conv, _ = conversation_repo.resolve_for_contact_ex(
        person["id"], f"{person_phone}@s.whatsapp.net")

    yield {"group": group_conv["id"], "person": person_conv["id"]}
    for cid in created:
        contact_repo.delete(cid)


def _list(client, query: str) -> list[dict]:
    r = client.get(f"/api/atendimentos/filter?{query}&status=open&limit=200")
    assert r.status_code == 200, r.text
    return r.json()["data"]["conversations"]


def test_only_groups(admin_client, seeded):
    rows = _list(admin_client, "chat_type=group")
    ids = {c["id"] for c in rows}
    assert seeded["group"] in ids
    assert seeded["person"] not in ids
    assert all(c["contact_is_group"] for c in rows)


def test_except_groups_both_spellings_agree(admin_client, seeded):
    individual = _list(admin_client, "chat_type=individual")
    negated = _list(admin_client, "chat_type=group&chat_type__op=not_equal_to")
    for rows in (individual, negated):
        ids = {c["id"] for c in rows}
        assert seeded["person"] in ids
        assert seeded["group"] not in ids
        assert not any(c["contact_is_group"] for c in rows)
    assert {c["id"] for c in individual} == {c["id"] for c in negated}


def test_group_list_and_count_share_the_same_where(admin_client, seeded):
    for query in ("chat_type=group", "chat_type=group&chat_type__op=not_equal_to"):
        rows = _list(admin_client, query)
        r = admin_client.get(f"/api/atendimentos/count?{query}&status=open")
        assert r.status_code == 200, r.text
        counts = r.json()["data"]
        if len(rows) < 200:   # página única: lista == contagem
            assert counts["all"] == len(rows), (query, counts, len(rows))


def test_invalid_value_is_a_400(admin_client, _engine_ready):
    r = admin_client.get("/api/atendimentos/filter?chat_type=channel")
    assert r.status_code == 400, r.text


def test_dimension_is_published_in_the_filter_schema(admin_client, _engine_ready):
    r = admin_client.get("/api/atendimentos/filter-schema")
    assert r.status_code == 200, r.text
    dims = {d["key"]: d for d in r.json()["data"]["dimensions"]}
    assert dims["chat_type"]["label"] == "Tipo de conversa"
    assert dims["chat_type"]["enum"] == ["group", "individual"]
    assert set(dims["chat_type"]["ops"]) == {"equal_to", "not_equal_to"}
