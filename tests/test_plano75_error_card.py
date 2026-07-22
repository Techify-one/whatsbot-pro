"""Plano 75 F6 — card no chat com o motivo da falha de envio (PT-BR).

Duas camadas:

* o catálogo puro :mod:`server.message_errors` — código conhecido vira frase
  acionável, código desconhecido cai no ``title``/``error_data.details`` que a
  própria Meta manda, e NUNCA sai string vazia (item 4);
* o webhook ponta a ponta — um ``statuses[].status == "failed"`` da Cloud API
  (payload do §12.8 do plano) marca a mensagem, grava UM card ``role="error"`` na
  conversa e o reentrega do MESMO status não gera um 2º card (item 5).

    venv/bin/python -m pytest tests/test_plano75_error_card.py -q
"""

from __future__ import annotations

import uuid

import pytest

from db.repositories import (contact_repo, contact_inbox_repo, conversation_repo,
                             message_repo)
from server.message_errors import ERROR_HINTS, UNKNOWN_REASON, describe_failure

INBOX_ID = 1  # seeded by migration 0013
PHONE_NUMBER_ID = "PNID_P75"


# ── Catálogo (puro) ──────────────────────────────────────────────────────────

def test_known_code_becomes_actionable_ptbr():
    text = describe_failure(code=131047)
    assert "24h" in text
    assert "template" in text.lower()
    assert "(código 131047)" in text


def test_numeric_string_code_resolves_the_same():
    """A Meta manda int, mas provider/serialização podem entregar string."""
    assert describe_failure(code="131049") == describe_failure(code=131049)


def test_unknown_code_falls_back_to_meta_text():
    text = describe_failure(
        code=999999, title="Some brand new failure",
        details="The recipient could not be reached for a novel reason.")
    assert "Some brand new failure" in text
    assert "novel reason" in text
    assert "(código 999999)" in text


def test_unknown_code_repeated_title_and_details_is_not_duplicated():
    text = describe_failure(code=999999, title="Same text", details="Same text")
    assert text.count("Same text") == 1


def test_unknown_code_without_any_text_uses_the_code():
    text = describe_failure(code=999999)
    assert text.strip()
    assert "999999" in text


def test_no_code_and_no_text_still_has_a_reason():
    assert describe_failure() == UNKNOWN_REASON
    assert describe_failure(code=None, title="", details="") == UNKNOWN_REASON


def test_non_numeric_code_is_preserved_as_label():
    text = describe_failure(code="E_WEIRD", title="Provider said no")
    assert "E_WEIRD" in text
    assert "Provider said no" in text


def test_catalog_has_no_phantom_codes():
    """F.P.#6 do plano: 470 (On-Premises legado) e 63016 (Twilio) NÃO existem
    na Cloud API — incluí-los mostraria um motivo errado ao operador."""
    assert 470 not in ERROR_HINTS
    assert 63016 not in ERROR_HINTS


def test_every_hint_is_non_empty_ptbr():
    for code, hint in ERROR_HINTS.items():
        assert isinstance(code, int)
        assert hint.strip(), code


# ── Ponta a ponta pelo webhook da Cloud API ──────────────────────────────────

def _make_cloud_channel(channel_id: str) -> None:
    from db.repositories import channel_repo, channel_credential_repo
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    for key, value in {"access_token": "TOKEN_SECRET",
                       "phone_number_id": PHONE_NUMBER_ID,
                       "waba_id": "WABA_P75",
                       "verify_token": "VERIFY_SECRET"}.items():
        channel_credential_repo.set(channel_id, key, value)


@pytest.fixture
def outgoing(_engine_ready):
    """Canal Cloud + contato + conversa + UMA mensagem de saída ``operator``.

    ``operator`` é o status real de um template/envio do painel — o mesmo que
    faria ``update_status_by_msg_id`` virar no-op silencioso (F.P.#4). Telefone
    novo por teste: o banco é global ao processo e o índice parcial
    ``uq_atend_open_contact_inbox`` não pode colidir entre testes.
    """
    channel_id = f"p75f6_{uuid.uuid4().hex[:8]}"
    _make_cloud_channel(channel_id)
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    msg_id = f"wamid.p75f6.{uuid.uuid4().hex[:16]}"
    message_repo.add(contact["id"], "assistant", "Segue o template",
                     status="operator", msg_id=msg_id, conversation_id=conv["id"])
    return {"channel_id": channel_id, "phone": phone, "contact": contact,
            "conversation": conv, "msg_id": msg_id}


def _failed_payload(msg_id: str, recipient: str, errors=None) -> dict:
    """Envelope real do §12.8 (``failed`` não traz ``conversation``/``pricing``)."""
    status: dict = {"id": msg_id, "status": "failed",
                    "timestamp": "1751142888", "recipient_id": recipient}
    if errors is not None:
        status["errors"] = errors
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA_P75", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111",
                             "phone_number_id": PHONE_NUMBER_ID},
                "statuses": [status]}}]}]}


def _meta_errors(code, title: str, details: str) -> list[dict]:
    return [{"code": code, "title": title, "message": title,
             "error_data": {"details": details},
             "href": "/documentation/business-messaging/whatsapp/support/error-codes"}]


def _install_ws_capture(built) -> list[tuple]:
    deps = built.app.state.deps
    events: list[tuple] = []
    orig = deps.ws_manager.broadcast

    async def _cap(*a, **k):
        name = a[0] if a else k.get("event")
        payload = a[1] if len(a) > 1 else (k.get("data") if "data" in k else k.get("payload"))
        events.append((name, payload))
        return await orig(*a, **k)

    deps.ws_manager.broadcast = _cap
    return events


def _post(built, outgoing, errors=None):
    return built.client.post(
        f"/api/webhook/whatsapp_cloud/{outgoing['channel_id']}",
        json=_failed_payload(outgoing["msg_id"], outgoing["phone"], errors))


def _error_cards(outgoing) -> list[dict]:
    return [m for m in message_repo.get_all(outgoing["contact"]["id"])
            if m.get("role") == "error"]


def _status_of(outgoing) -> str | None:
    for m in message_repo.get_all(outgoing["contact"]["id"]):
        if m.get("msg_id") == outgoing["msg_id"]:
            return m.get("status")
    return None


def test_known_code_produces_the_ptbr_card(build_app, outgoing):
    """O critério de pronto da fase: 131047 ⇒ bolha vermelha + card das 24h."""
    built = build_app(["whatsapp_cloud"])
    ws_events = _install_ws_capture(built)

    r = _post(built, outgoing, _meta_errors(
        131047,
        "Message failed to send because more than 24 hours have passed since "
        "the customer last replied to this number.",
        "Message failed to send because more than 24 hours have passed."))
    assert r.status_code == 200, r.text

    assert _status_of(outgoing) == "failed"
    cards = _error_cards(outgoing)
    assert len(cards) == 1, cards
    card = cards[0]
    assert "24h" in card["content"]
    assert "(código 131047)" in card["content"]
    assert card["conversation_id"] == outgoing["conversation"]["id"]

    # Broadcast ao vivo, mesmo formato do system_notice.
    pushed = [p for n, p in ws_events
              if n == "new_message" and (p or {}).get("phone") == outgoing["phone"]]
    assert pushed, ws_events
    assert pushed[-1]["message"]["role"] == "error"
    assert pushed[-1]["message"]["content"] == card["content"]

    # Painel-only: o card não pode entrar no contexto do LLM (role 'error' já é
    # excluído por message_repo.get_context) — se entrasse, o turno crasharia.
    ctx = message_repo.get_context(outgoing["contact"]["id"], 50)
    assert all(m["role"] != "error" for m in ctx), ctx


def test_unknown_code_uses_the_meta_title(build_app, outgoing):
    """Código fora do catálogo nunca vira card vazio (item 4)."""
    built = build_app(["whatsapp_cloud"])

    r = _post(built, outgoing, _meta_errors(
        999123, "Brand new Meta failure", "Something the catalog never saw."))
    assert r.status_code == 200, r.text

    cards = _error_cards(outgoing)
    assert len(cards) == 1, cards
    assert "Brand new Meta failure" in cards[0]["content"]
    assert "Something the catalog never saw." in cards[0]["content"]


def test_missing_errors_array_does_not_break(build_app, outgoing):
    """``failed`` sem ``errors`` (ou com array vazio) ainda marca e explica."""
    built = build_app(["whatsapp_cloud"])

    r = _post(built, outgoing, None)
    assert r.status_code == 200, r.text

    assert _status_of(outgoing) == "failed"
    cards = _error_cards(outgoing)
    assert len(cards) == 1, cards
    assert cards[0]["content"] == UNKNOWN_REASON


def test_redelivery_does_not_create_a_second_card(build_app, outgoing):
    """R7 — a Meta reentrega o webhook; o guard é o retorno de
    ``mark_failed_by_msg_id`` (None na 2ª passada)."""
    built = build_app(["whatsapp_cloud"])

    errors = _meta_errors(
        131049, "This message was not delivered to maintain healthy ecosystem "
                "engagement.", "In order to maintain a healthy ecosystem "
                               "engagement, the message failed to be delivered.")
    assert _post(built, outgoing, errors).status_code == 200
    assert _post(built, outgoing, errors).status_code == 200

    cards = _error_cards(outgoing)
    assert len(cards) == 1, cards
    assert "marketing" in cards[0]["content"]


def test_unknown_msg_id_writes_no_card(build_app, outgoing):
    """Falha de uma mensagem que não está no banco (importada, id divergente):
    o webhook responde 200, loga, e NÃO inventa card em conversa nenhuma."""
    built = build_app(["whatsapp_cloud"])
    ghost = dict(outgoing, msg_id=f"wamid.p75f6.ghost.{uuid.uuid4().hex[:8]}")

    r = _post(built, ghost, _meta_errors(131047, "24h window", "closed"))
    assert r.status_code == 200, r.text

    assert _error_cards(outgoing) == []
    assert _status_of(outgoing) == "operator"
