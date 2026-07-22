"""Plano 75 F5 — a falha de entrega não pode se perder na corrida com o writer.

O defeito: a resposta da IA é enviada parte a parte
(``app/services/messaging_service.py``, ``await asyncio.sleep(split_message_delay)``
entre as partes) e só é GRAVADA no banco depois do loop inteiro. A parte 1 fica
~2 s enviada-mas-não-persistida; um webhook ``status=failed`` que caia nessa
janela não acha linha nenhuma, ``mark_failed_by_msg_id`` devolve ``None`` e a
falha era descartada em silêncio — sem bolha vermelha e sem card.

O conserto vive no lado do webhook (``server/routes/channel_webhook.py``):
``exists_by_msg_id`` separa "dedup legítimo" de "linha ainda não existe" e, no
segundo caso, uma task de fundo limitada tenta de novo. O guard de unicidade do
card continua sendo o UPDATE condicional do repo — só uma chamada ganha a
transição.

    venv/bin/python -m pytest tests/test_plano75_failed_race.py -q
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid

import pytest

import server.routes.channel_webhook as channel_webhook
from db.repositories import (contact_repo, contact_inbox_repo, conversation_repo,
                             message_repo)

INBOX_ID = 1  # seeded by migration 0013
PHONE_NUMBER_ID = "PNID_P75RACE"


# ── Cenário ──────────────────────────────────────────────────────────────────

def _make_cloud_channel(channel_id: str) -> None:
    from db.repositories import channel_repo, channel_credential_repo
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    for key, value in {"access_token": "TOKEN_SECRET",
                       "phone_number_id": PHONE_NUMBER_ID,
                       "waba_id": "WABA_P75RACE",
                       "verify_token": "VERIFY_SECRET"}.items():
        channel_credential_repo.set(channel_id, key, value)


@pytest.fixture
def pending(_engine_ready):
    """Canal Cloud + contato + conversa, mas SEM a linha da mensagem.

    É exatamente o estado da corrida: o provedor já aceitou (e já reprovou) o
    envio, o WhatsBot ainda não gravou a linha. Telefone novo por teste — o
    banco é global ao processo.
    """
    channel_id = f"p75race_{uuid.uuid4().hex[:8]}"
    _make_cloud_channel(channel_id)
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    return {"channel_id": channel_id, "phone": phone, "contact": contact,
            "conversation": conv,
            "msg_id": f"wamid.p75race.{uuid.uuid4().hex[:16]}"}


@pytest.fixture
def fast_retry(monkeypatch):
    """Encurta a retentativa para o teste não gastar os ~6 s de produção."""
    def _apply(interval: float = 0.3, attempts: int = 3) -> None:
        monkeypatch.setattr(channel_webhook, "_FAILED_RETRY_INTERVAL", interval)
        monkeypatch.setattr(channel_webhook, "_FAILED_RETRY_ATTEMPTS", attempts)
    return _apply


def _failed_payload(msg_id: str, recipient: str, errors=None) -> dict:
    """Envelope real do §12.8 (``failed`` não traz ``conversation``/``pricing``)."""
    status: dict = {"id": msg_id, "status": "failed",
                    "timestamp": "1751142888", "recipient_id": recipient}
    if errors is not None:
        status["errors"] = errors
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA_P75RACE", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111",
                             "phone_number_id": PHONE_NUMBER_ID},
                "statuses": [status]}}]}]}


def _meta_errors(code, title: str, details: str) -> list[dict]:
    return [{"code": code, "title": title, "message": title,
             "error_data": {"details": details},
             "href": "/documentation/business-messaging/whatsapp/support/error-codes"}]


def _post(built, pending, errors=None):
    return built.client.post(
        f"/api/webhook/whatsapp_cloud/{pending['channel_id']}",
        json=_failed_payload(pending["msg_id"], pending["phone"], errors))


def _write_outgoing_row(pending) -> None:
    """O que o writer do split faz DEPOIS de mandar todas as partes."""
    message_repo.add(pending["contact"]["id"], "assistant", "Parte 1 da resposta",
                     status="sent", msg_id=pending["msg_id"],
                     conversation_id=pending["conversation"]["id"])


def _error_cards(pending) -> list[dict]:
    return [m for m in message_repo.get_all(pending["contact"]["id"])
            if m.get("role") == "error"]


def _status_of(pending) -> str | None:
    for m in message_repo.get_all(pending["contact"]["id"]):
        if m.get("msg_id") == pending["msg_id"]:
            return m.get("status")
    return None


@contextlib.contextmanager
def capture_warnings():
    """Coleta WARNINGs do logger do webhook durante o bloco.

    Não dá pra usar ``caplog`` aqui: o harness da suíte marca os loggers de
    módulo já importados como ``disabled=True`` (mesmo artefato documentado em
    ``tests/test_persistence_check.py``; em produção o ``logging.basicConfig`` do
    ``main.py`` nunca desabilita ninguém). Então o handler vai direto no logger,
    com ``disabled`` forçado a False e restaurado no fim.
    """
    records: list[logging.LogRecord] = []
    logger = channel_webhook.logger

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _H()
    handler.setLevel(logging.WARNING)
    prev_level, prev_disabled = logger.level, logger.disabled
    logger.setLevel(logging.DEBUG)
    logger.disabled = False
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        logger.disabled = prev_disabled


def _wait_for_cards(pending, expected: int = 1, timeout: float = 5.0) -> list[dict]:
    deadline = time.time() + timeout
    cards = _error_cards(pending)
    while len(cards) < expected and time.time() < deadline:
        time.sleep(0.05)
        cards = _error_cards(pending)
    return cards


# ── Helper do repo ───────────────────────────────────────────────────────────

def test_exists_by_msg_id_separates_unknown_from_not_transitioned(pending):
    """O ``None`` de ``mark_failed_by_msg_id`` funde os dois casos; este helper
    é o que o webhook usa para decidir entre "cala a boca" e "tenta de novo"."""
    assert message_repo.exists_by_msg_id(pending["msg_id"]) is False
    assert message_repo.exists_by_msg_id("") is False
    _write_outgoing_row(pending)
    assert message_repo.exists_by_msg_id(pending["msg_id"]) is True


# ── A corrida ────────────────────────────────────────────────────────────────

def test_failure_arriving_before_the_row_is_recovered(build_app, pending, fast_retry):
    """O caso do defeito: webhook ``failed`` chega ANTES de a linha existir.

    A linha aparece logo depois (como faz o writer do split) e a retentativa
    fecha o ciclo: status ``failed`` + EXATAMENTE um card ``role="error"``.
    """
    fast_retry(interval=0.3)
    built = build_app(["whatsapp_cloud"])

    r = _post(built, pending, _meta_errors(
        131049, "This message was not delivered to maintain healthy ecosystem "
                "engagement.", "Healthy ecosystem engagement."))
    assert r.status_code == 200, r.text
    # Nada aconteceu ainda — a linha nem existe.
    assert _error_cards(pending) == []

    _write_outgoing_row(pending)

    cards = _wait_for_cards(pending, 1)
    assert len(cards) == 1, cards
    assert _status_of(pending) == "failed"
    assert cards[0]["conversation_id"] == pending["conversation"]["id"]
    assert "marketing" in cards[0]["content"]

    # E nenhum card extra depois que a janela de retentativa se esgota.
    time.sleep(1.2)
    assert len(_error_cards(pending)) == 1


def test_row_that_never_appears_is_dropped_quietly(build_app, pending, fast_retry):
    """msg_id de outra instalação / mensagem apagada: o webhook responde 200, a
    retentativa se esgota, sobra um WARNING — nenhum card, nenhuma exceção."""
    fast_retry(interval=0.05)
    built = build_app(["whatsapp_cloud"])

    with capture_warnings() as records:
        r = _post(built, pending, _meta_errors(131047, "24h window", "closed"))
        assert r.status_code == 200, r.text
        deadline = time.time() + 5.0
        while time.time() < deadline and not records:
            time.sleep(0.05)
        text = "\n".join(rec.getMessage() for rec in records)

    assert "DESCARTADO" in text, text
    assert pending["msg_id"] in text
    assert "131047" in text
    assert _error_cards(pending) == []
    assert _status_of(pending) is None  # a linha nunca existiu


def test_redelivery_during_the_retry_window_still_writes_one_card(
        build_app, pending, fast_retry):
    """A Meta reentrega o mesmo status enquanto a 1ª retentativa está no ar:
    duas tasks disputam a mesma linha e só uma ganha o UPDATE condicional."""
    fast_retry(interval=0.3)
    built = build_app(["whatsapp_cloud"])

    errors = _meta_errors(131047, "24h window", "closed")
    assert _post(built, pending, errors).status_code == 200
    assert _post(built, pending, errors).status_code == 200

    _write_outgoing_row(pending)

    cards = _wait_for_cards(pending, 1)
    time.sleep(1.2)  # deixa TODAS as tentativas das duas tasks rodarem
    cards = _error_cards(pending)
    assert len(cards) == 1, cards
    assert _status_of(pending) == "failed"


def test_existing_row_path_is_unchanged(build_app, pending, fast_retry):
    """Regressão: com a linha já no banco, o card sai DENTRO do request (sem
    depender de retentativa nenhuma) — o caminho normal continua idêntico."""
    fast_retry(interval=0.3)
    built = build_app(["whatsapp_cloud"])
    _write_outgoing_row(pending)

    r = _post(built, pending, _meta_errors(131047, "24h window", "closed"))
    assert r.status_code == 200, r.text

    cards = _error_cards(pending)  # sem espera: já tem que estar lá
    assert len(cards) == 1, cards
    assert _status_of(pending) == "failed"

    time.sleep(1.2)
    assert len(_error_cards(pending)) == 1
