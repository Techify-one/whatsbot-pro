"""Plano 75 F5 — os eventos de bus da falha de entrega, pelo webhook de verdade.

O bug que a F5 conserta é POSICIONAL: o ``emit_with_filter("receipt.changed", …)``
morava **DENTRO** do ``if status in ("delivered", "read")`` em
[server/routes/channel_webhook.py], então nenhum plugin conseguia reagir a uma
falha de entrega (nem a um ``sent``). O conserto tirou o emit do ``if`` e criou o
evento novo ``message.failed``.

Um `if` é fácil de re-fechar num merge distraído, e até aqui **nenhum teste
reclamaria** — daí este arquivo. Ele trava, ponta a ponta (POST no webhook real
do canal Cloud, envelope literal do §12.8 do plano):

* ``message.failed`` sai no bus com ``error_code`` / ``error_title`` /
  ``error_details`` / ``conversation_id`` (o gancho de automação pedido);
* ``receipt.changed`` sai para ``sent`` **e** para ``failed`` — ou seja, SAIU de
  dentro do ``if``; e continua saindo para ``delivered`` (não houve regressão);
* o payload de ``receipt.changed`` carrega ``status`` e ``errors`` (risco R3 — o
  assinante precisa distinguir o que antes era implícito).

Captura do bus: um ``EventRecorder`` (filtro ``filter.event.before_emit``,
síncrono dentro do emit) para a ordem/payload determinísticos, MAIS um assinante
de plugin de verdade registrado com ``plugins.events.register`` — é ele que prova
o critério de pronto da fase ("um plugin de teste recebe ``message.failed``").
Os dois registros são removidos cirurgicamente no teardown (nada de ``reset()``,
que derrubaria os handlers do app).

    venv/bin/python -m pytest tests/test_plano75_bus_events.py -q
"""

from __future__ import annotations

import uuid

import pytest

from db.repositories import (contact_inbox_repo, contact_repo, conversation_repo,
                             message_repo)
from tests.characterization.golden import EventRecorder

INBOX_ID = 1  # semeado pela migration 0013
PHONE_NUMBER_ID = "PNID_P75_BUS"

# Códigos/textos LITERAIS do §12.8 do plano (doc oficial da Meta).
FAILED_CODE = 131049
FAILED_TITLE = ("This message was not delivered to maintain healthy ecosystem "
                "engagement.")
FAILED_DETAILS = ("In order to maintain a healthy ecosystem engagement, the "
                  "message failed to be delivered.")


# ── Fixtures de canal / conversa ────────────────────────────────────────────

def _make_cloud_channel(channel_id: str) -> None:
    from db.repositories import channel_credential_repo, channel_repo
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    for key, value in {"access_token": "TOKEN_SECRET",
                       "phone_number_id": PHONE_NUMBER_ID,
                       "waba_id": "WABA_P75_BUS",
                       "verify_token": "VERIFY_SECRET"}.items():
        channel_credential_repo.set(channel_id, key, value)


@pytest.fixture
def outgoing(_engine_ready):
    """Canal Cloud + contato + conversa + UMA mensagem de saída ``operator``.

    Telefone/canal novos por teste: o banco é global ao processo e o índice
    parcial ``uq_atend_open_contact_inbox`` não pode colidir entre testes.
    """
    channel_id = f"p75bus_{uuid.uuid4().hex[:8]}"
    _make_cloud_channel(channel_id)
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    msg_id = f"wamid.p75bus.{uuid.uuid4().hex[:16]}"
    message_repo.add(contact["id"], "assistant", "Segue o template",
                     status="operator", msg_id=msg_id, conversation_id=conv["id"])
    return {"channel_id": channel_id, "phone": phone, "contact": contact,
            "conversation": conv, "msg_id": msg_id}


# ── Envelopes literais do §12.8 ─────────────────────────────────────────────

def _meta_errors() -> list[dict]:
    return [{
        "code": FAILED_CODE,
        "title": FAILED_TITLE,
        "message": FAILED_TITLE,
        "error_data": {"details": FAILED_DETAILS},
        "href": "/documentation/business-messaging/whatsapp/support/error-codes",
    }]


def _status_payload(msg_id: str, recipient: str, status: str,
                    errors: list | None = None) -> dict:
    item: dict = {"id": msg_id, "status": status,
                  "timestamp": "1751142888", "recipient_id": recipient}
    if errors is not None:
        item["errors"] = errors
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA_P75_BUS", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111",
                             "phone_number_id": PHONE_NUMBER_ID},
                "statuses": [item]}}]}]}


def _post(built, outgoing: dict, status: str, errors: list | None = None):
    return built.client.post(
        f"/api/webhook/whatsapp_cloud/{outgoing['channel_id']}",
        json=_status_payload(outgoing["msg_id"], outgoing["phone"], status, errors))


# ── Coletor de bus (filtro + assinante de plugin real) ──────────────────────

class _BusCapture:
    """``EventRecorder`` (filtro) + um assinante de PLUGIN de verdade.

    O filtro dá captura determinística (roda síncrono dentro do
    ``emit_with_filter``); o assinante prova a entrega real ao fan-out do bus —
    é o "plugin de teste recebe ``message.failed``" do critério de pronto.
    Ambos são desregistrados no ``__exit__``, sem tocar no resto do bus.
    """

    _counter = 0

    def __init__(self, event_names: tuple[str, ...]) -> None:
        _BusCapture._counter += 1
        self._plugin_id = f"_p75_bus_subscriber_{_BusCapture._counter}"
        self._event_names = event_names
        self.delivered: list[tuple[str, dict]] = []
        self._recorder = EventRecorder()

    def __enter__(self) -> "_BusCapture":
        from plugins import events as bus

        # O recorder também é quem garante um loop de bus vivo (a lifespan do
        # app de teste é no-op, então ``bus._loop`` viria None e o ``emit()``
        # descartaria o fan-out em silêncio).
        self._recorder.__enter__()

        def _subscriber(ctx, payload: dict) -> None:
            self.delivered.append((ctx.event_name, payload))

        for name in self._event_names:
            bus.register(self._plugin_id, name, _subscriber)
        return self

    def __exit__(self, *exc) -> None:
        from plugins import events as bus

        self.drain()
        for name in self._event_names:
            bucket = bus._handlers.get(name)
            if bucket:
                bus._handlers[name] = [
                    t for t in bucket if t[0] != self._plugin_id]
        self._recorder.__exit__(*exc)

    # ── views ──────────────────────────────────────────────────────────────

    def drain(self, seconds: float = 0.15) -> None:
        """Deixa o fan-out (``asyncio.create_task``) do bus terminar."""
        self._recorder.drain(seconds)

    def emitted(self, event_name: str) -> list[dict]:
        """Payloads que passaram pelo emit (captura pelo filtro)."""
        return self._recorder.by_name(event_name)

    def received(self, event_name: str) -> list[dict]:
        """Payloads que chegaram ao assinante de plugin (fan-out de verdade)."""
        return [p for n, p in self.delivered if n == event_name]


@pytest.fixture
def bus_capture():
    with _BusCapture(("message.failed", "receipt.changed")) as capture:
        yield capture


def _for_msg(payloads: list[dict], msg_id: str) -> list[dict]:
    """Só os payloads da NOSSA mensagem (o banco é compartilhado no processo)."""
    out = []
    for p in payloads:
        ids = p.get("msg_ids")
        if p.get("msg_id") == msg_id or (isinstance(ids, list) and msg_id in ids):
            out.append(p)
    return out


# ── message.failed ──────────────────────────────────────────────────────────

def test_failed_status_emits_message_failed_on_the_bus(build_app, outgoing, bus_capture):
    """O gancho de automação: ``message.failed`` chega a um plugin assinante com
    código, título, detalhe e a conversa onde a falha aconteceu."""
    built = build_app(["whatsapp_cloud"])

    r = _post(built, outgoing, "failed", _meta_errors())
    assert r.status_code == 200, r.text
    bus_capture.drain()

    received = _for_msg(bus_capture.received("message.failed"), outgoing["msg_id"])
    assert len(received) == 1, bus_capture.delivered
    payload = received[0]
    assert payload["error_code"] == FAILED_CODE
    assert payload["error_title"] == FAILED_TITLE
    assert payload["error_details"] == FAILED_DETAILS
    assert payload["conversation_id"] == outgoing["conversation"]["id"]
    assert payload["phone"] == outgoing["phone"]
    assert payload["channel_id"] == outgoing["channel_id"]
    assert payload["is_new"] is True


def test_message_failed_is_not_emitted_for_healthy_statuses(build_app, outgoing,
                                                            bus_capture):
    """``sent``/``delivered`` não podem acionar o gancho de falha."""
    built = build_app(["whatsapp_cloud"])

    assert _post(built, outgoing, "sent").status_code == 200
    assert _post(built, outgoing, "delivered").status_code == 200
    bus_capture.drain()

    assert _for_msg(bus_capture.emitted("message.failed"), outgoing["msg_id"]) == []


def test_redelivery_keeps_the_event_but_flags_is_new_false(build_app, outgoing,
                                                           bus_capture):
    """A Meta reentrega o mesmo status: o evento continua saindo (uma falha é uma
    falha), mas ``is_new`` vira False — é esse o guard de dedup do assinante."""
    built = build_app(["whatsapp_cloud"])

    assert _post(built, outgoing, "failed", _meta_errors()).status_code == 200
    assert _post(built, outgoing, "failed", _meta_errors()).status_code == 200
    bus_capture.drain()

    emitted = _for_msg(bus_capture.emitted("message.failed"), outgoing["msg_id"])
    assert len(emitted) == 2, emitted
    assert [p["is_new"] for p in emitted] == [True, False]
    # A 2ª passada não acha a row (já 'failed') ⇒ sem conversa no payload.
    assert emitted[0]["conversation_id"] == outgoing["conversation"]["id"]
    assert emitted[1]["conversation_id"] is None


def test_message_failed_survives_an_errors_array_that_meta_omitted(build_app,
                                                                   outgoing,
                                                                   bus_capture):
    """``failed`` sem ``errors``: o evento sai mesmo assim, com os campos vazios —
    o assinante nunca precisa de ``.get`` defensivo para as 3 chaves."""
    built = build_app(["whatsapp_cloud"])

    assert _post(built, outgoing, "failed").status_code == 200
    bus_capture.drain()

    received = _for_msg(bus_capture.received("message.failed"), outgoing["msg_id"])
    assert len(received) == 1, bus_capture.delivered
    payload = received[0]
    assert set(payload) >= {"error_code", "error_title", "error_details",
                            "conversation_id", "is_new", "msg_id", "phone"}
    assert payload["error_code"] is None
    assert payload["error_title"] == ""
    assert payload["error_details"] == ""


# ── receipt.changed SAIU do `if delivered/read` ─────────────────────────────

def test_receipt_changed_fires_for_failed(build_app, outgoing, bus_capture):
    """ESTE é o ponto do arquivo: antes da F5 o emit vivia dentro do
    ``if status in ("delivered","read")`` e um ``failed`` não produzia evento
    nenhum. Se um merge devolver o emit para lá, este teste cai."""
    built = build_app(["whatsapp_cloud"])

    assert _post(built, outgoing, "failed", _meta_errors()).status_code == 200
    bus_capture.drain()

    received = _for_msg(bus_capture.received("receipt.changed"), outgoing["msg_id"])
    assert len(received) == 1, bus_capture.delivered
    payload = received[0]
    assert payload["status"] == "failed"
    assert payload["msg_ids"] == [outgoing["msg_id"]]
    assert payload["channel_id"] == outgoing["channel_id"]
    # R3: o assinante precisa do motivo junto do status.
    assert payload["errors"] and payload["errors"][0]["code"] == FAILED_CODE


def test_receipt_changed_fires_for_sent(build_app, outgoing, bus_capture):
    """``sent`` não escreve nada no banco (a msg já nasce enviada), mas o evento
    tem de sair — é a outra metade da prova de que o emit está FORA do ``if``."""
    built = build_app(["whatsapp_cloud"])

    assert _post(built, outgoing, "sent").status_code == 200
    bus_capture.drain()

    received = _for_msg(bus_capture.received("receipt.changed"), outgoing["msg_id"])
    assert len(received) == 1, bus_capture.delivered
    payload = received[0]
    assert payload["status"] == "sent"
    assert payload["errors"] is None
    assert payload["phone"] == outgoing["phone"]


def test_receipt_changed_still_fires_for_delivered(build_app, outgoing, bus_capture):
    """Guarda de regressão do caminho que SEMPRE funcionou: tirar o emit do
    ``if`` não pode ter custado o ``delivered``."""
    built = build_app(["whatsapp_cloud"])

    assert _post(built, outgoing, "delivered").status_code == 200
    bus_capture.drain()

    received = _for_msg(bus_capture.received("receipt.changed"), outgoing["msg_id"])
    assert len(received) == 1, bus_capture.delivered
    assert received[0]["status"] == "delivered"


def test_receipt_changed_payload_always_carries_status_and_errors(build_app,
                                                                  outgoing,
                                                                  bus_capture):
    """Contrato do payload (R3): as duas chaves existem em TODO status, para o
    assinante poder ramificar sem adivinhar."""
    built = build_app(["whatsapp_cloud"])

    for status in ("sent", "delivered", "read"):
        assert _post(built, outgoing, status).status_code == 200
    assert _post(built, outgoing, "failed", _meta_errors()).status_code == 200
    bus_capture.drain()

    emitted = _for_msg(bus_capture.emitted("receipt.changed"), outgoing["msg_id"])
    assert [p["status"] for p in emitted] == ["sent", "delivered", "read", "failed"]
    for payload in emitted:
        assert "status" in payload and "errors" in payload


# ── Higiene do próprio coletor ──────────────────────────────────────────────

def test_capture_leaves_no_handler_behind():
    """Sem a fixture de propósito: prova que os testes acima já devolveram o bus
    ao estado anterior. Um assinante esquecido receberia eventos de OUTROS
    arquivos de teste (e os pagaria com falhas fantasma difíceis de rastrear)."""
    from plugins import events as bus

    leaked = [(name, pid) for name, bucket in bus._handlers.items()
              for pid, _fn in bucket if pid.startswith("_p75_bus_subscriber_")]
    assert leaked == [], leaked
