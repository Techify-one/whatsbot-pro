"""Busca da barra lateral — ``GET /api/atendimentos?contact_ids=``.

Contexto (bug "contato novo não aparece na busca"): a busca da sidebar cruza
``/api/contacts?q=`` × ``/api/atendimentos`` no cliente. Sem restrição, a 2ª chamada
pedia as 200 conversas mais recentes e o ``buildRows`` descartava em silêncio o
contato cujo atendimento fosse mais antigo que essa janela. Com ``contact_ids`` a
busca pede exatamente os atendimentos dos contatos que casaram o termo — a idade do
atendimento deixa de importar.

Estes testes cobrem o contrato do parâmetro (filtro, parse defensivo, teto de limit)
e o não-regresso do comportamento sem ele.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def two_contacts_with_conversations(seed):
    """Dois contatos, um atendimento cada. Devolve ``(id_a, id_b)``."""
    from db.repositories import contact_repo, conversation_repo

    ids = []
    for phone in ("5511900000101", "5511900000102"):
        contact = contact_repo.get_or_create(phone)
        conversation_repo.resolve_for_contact(
            contact["id"], f"{phone}@s.whatsapp.net")
        ids.append(contact["id"])
    return tuple(ids)


def _conv_rows(client, qs: str) -> list[dict]:
    r = client.get(f"/api/atendimentos{qs}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    return body["data"]["conversations"]


def test_contact_ids_restricts_to_that_contact(client, two_contacts_with_conversations):
    id_a, id_b = two_contacts_with_conversations
    rows = _conv_rows(client, f"?contact_ids={id_a}")
    assert rows, "o contato pedido tem atendimento e deve vir"
    assert {r["contact_id"] for r in rows} == {id_a}, \
        f"só os atendimentos de {id_a}; veio {sorted({r['contact_id'] for r in rows})}"

    # CSV com os dois → os dois voltam.
    both = _conv_rows(client, f"?contact_ids={id_a},{id_b}")
    assert {id_a, id_b} <= {r["contact_id"] for r in both}


def test_without_the_param_nothing_changes(client, two_contacts_with_conversations):
    """Não-regresso: sem ``contact_ids`` a lista continua irrestrita."""
    id_a, id_b = two_contacts_with_conversations
    all_ids = {r["contact_id"] for r in _conv_rows(client, "")}
    assert {id_a, id_b} <= all_ids


def test_unknown_ids_yield_no_rows(client, two_contacts_with_conversations):
    assert _conv_rows(client, "?contact_ids=99999999") == []


def test_empty_and_malformed_csv_are_defensive(client, two_contacts_with_conversations):
    """Entrada degenerada vira "nenhum resultado", nunca 500.

    Distinção que importa: ``contact_ids`` AUSENTE = sem restrição; ``contact_ids``
    presente mas vazio (ou só lixo) = conjunto vazio = zero linhas."""
    for qs in ("?contact_ids=", "?contact_ids=,,", "?contact_ids=abc", "?contact_ids=abc,,x"):
        assert _conv_rows(client, qs) == [], f"esperava lista vazia para {qs!r}"

    # Tokens válidos convivem com lixo (o lixo é descartado, o válido filtra).
    id_a, _id_b = two_contacts_with_conversations
    rows = _conv_rows(client, f"?contact_ids=abc,{id_a},")
    assert {r["contact_id"] for r in rows} == {id_a}


def test_limit_cap_rises_only_with_contact_ids(client, two_contacts_with_conversations):
    """O teto de ``limit`` é 200 sem o parâmetro e 500 com ele — o ``IN`` já delimita
    o conjunto, então o cap maior não abre uma varredura."""
    from server.routes import conversations as conv_routes

    id_a, _id_b = two_contacts_with_conversations
    captured: dict = {}
    orig = conv_routes.conversation_repo.list_conversations

    def _spy(**kwargs):
        captured.update(kwargs)
        return orig(**kwargs)

    conv_routes.conversation_repo.list_conversations = _spy
    try:
        _conv_rows(client, "?limit=99999")
        assert captured["limit"] == 200
        assert captured["contact_ids"] is None

        _conv_rows(client, f"?limit=99999&contact_ids={id_a}")
        assert captured["limit"] == 500
        assert captured["contact_ids"] == [id_a]
    finally:
        conv_routes.conversation_repo.list_conversations = orig


def test_parse_contact_ids_caps_the_list():
    """Teto de ids: um termo curto pode casar muita gente e o ``IN`` não deve crescer
    sem limite."""
    from server.routes.conversations import MAX_CONTACT_IDS, _parse_contact_ids

    assert _parse_contact_ids(None) is None, "ausente = sem restrição (≠ lista vazia)"
    assert _parse_contact_ids("") == []
    assert _parse_contact_ids(" 7 , 8 ") == [7, 8], "espaços em volta são tolerados"

    raw = ",".join(str(i) for i in range(MAX_CONTACT_IDS + 50))
    assert len(_parse_contact_ids(raw)) == MAX_CONTACT_IDS
