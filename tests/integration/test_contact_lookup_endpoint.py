"""``GET /api/contacts/lookup`` — checagem de existência SEM efeito colateral.

Suporta o modal "Novo contato" (web/static/js/components/ContactsListScreen.js):
antes de o operador confirmar a criação, o modal precisa saber se o número já é
um contato — sem criar (``check-phone``) nem materializar (``GET /{phone}``, que
também cria quando ausente). Este é o único endpoint de leitura de contato sem
efeito colateral nenhum.

    WHATSBOT_TEST_DB_URL=... venv/bin/python -m pytest \
        tests/integration/test_contact_lookup_endpoint.py -q
"""

from __future__ import annotations

import uuid

from db.repositories import contact_repo


def _new_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _build(build_app):
    return build_app(["gowa"])


def test_lookup_reports_not_found_for_unknown_phone(build_app):
    built = _build(build_app)
    phone = _new_phone()
    r = built.client.get(f"/api/contacts/lookup?phone={phone}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"] == {"exists": False, "contact": None}


def test_lookup_finds_an_existing_contact_without_creating_a_new_one(build_app):
    built = _build(build_app)
    phone = _new_phone()
    created = contact_repo.get_or_create(phone)

    r = built.client.get(f"/api/contacts/lookup?phone={phone}")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["exists"] is True
    assert body["data"]["contact"]["id"] == created["id"]
    assert body["data"]["contact"]["phone"] == phone


def test_lookup_normalizes_like_check_phone_no_ddi_prefix(build_app):
    """Sem o DDI ``55``, o lookup tem de casar o MESMO contato — mesma
    normalização usada por ``check-phone``/importação (``_normalize_import_phone``:
    strip não-dígitos, prefixa ``55`` quando ausente)."""
    built = _build(build_app)
    phone = _new_phone()  # já com "55" na frente
    contact_repo.get_or_create(phone)
    without_ddi = phone[2:]  # DDD + número, sem o "55"

    r = built.client.get(f"/api/contacts/lookup?phone={without_ddi}")
    assert r.status_code == 200
    assert r.json()["data"]["exists"] is True


def test_lookup_rejects_a_too_short_number(build_app):
    built = _build(build_app)
    r = built.client.get("/api/contacts/lookup?phone=123")
    assert r.status_code == 400  # mesmo contrato de erro de _err()
    assert r.json()["ok"] is False


def test_lookup_route_is_not_swallowed_by_the_phone_path_param(build_app):
    """Guard de ordenação de rota: ``/api/contacts/lookup`` precisa estar
    registrada ANTES de ``/api/contacts/{phone}`` — senão isto vira uma busca
    pelo contato literal ``"lookup"`` e a resposta não teria a forma
    ``{exists, contact}`` (viria o shape de ``GET /{phone}``, com ``msg_count``
    etc., ou um erro de parâmetro)."""
    built = _build(build_app)
    r = built.client.get("/api/contacts/lookup?phone=5511999998888")
    assert r.status_code == 200
    body = r.json()
    assert set(body["data"]) == {"exists", "contact"}
