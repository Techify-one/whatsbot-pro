"""Plano 82 — inbound de SISTEMA não-acionável, pelo webhook Cloud de verdade.

O bug: um webhook ``type: system`` da Cloud API (o cliente trocou de número —
``user_changed_number``) era classificado como ``kind="message"``, entrava no
funil agêntico e a IA respondia ao número ANTIGO (morto) → erro 131026, além de
abrir protocolo e criar contato-fantasma.

O conserto é estrutural (F1/F2/F3): o provider declara ``kind="system"`` e o
dispatch do core ([server/routes/channel_webhook.py]) grava um card PAINEL-ONLY
(``conversation_event``) na conversa EXISTENTE e NADA MAIS — sem materializar
contato, sem criar/reabrir conversa, sem IA e, crucialmente, **sem emitir
``message.saved``/``message.received``** (é isso que impede plugins de automação
como o ``protocolos`` de dispararem). O hook opt-in é o evento novo
``channel.system_event``.

Esta suíte trava, ponta a ponta (POST no webhook real do canal Cloud, envelope
literal do §11.1 do plano), os três casos da decisão P2 + a não-acionabilidade:

* conversa ABERTA → 1 card ``conversation_event``, conversa segue aberta;
* conversa FECHADA → card gravado, conversa **continua fechada** (não reabre);
* sem conversa / sem contato → **nada** gravado, contato/conversa não criados;
* em todos: **zero** ``message.saved``/``message.received``, e ``channel.system_event``
  emitido; reentrega da Meta não duplica o card nem o evento.

    venv/bin/python -m pytest tests/test_plano82_system_inbound.py -q
"""

from __future__ import annotations

import uuid

import pytest

from db.repositories import (channel_credential_repo, channel_repo,
                             contact_inbox_repo, contact_repo, conversation_repo,
                             message_repo)
from tests.characterization.golden import EventRecorder

INBOX_ID = 1  # semeado pela migration 0013
PHONE_NUMBER_ID = "PNID_P82_SYS"
NEW_WA_ID = "12195555358"
SYS_TYPE = "user_changed_number"


# ── Setup de canal / conversa ────────────────────────────────────────────────

def _make_cloud_channel() -> str:
    channel_id = f"p82sys_{uuid.uuid4().hex[:8]}"
    channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                        display_name=channel_id, enabled=1)
    for key, value in {"access_token": "TOKEN_SECRET",
                       "phone_number_id": PHONE_NUMBER_ID,
                       "waba_id": "WABA_P82_SYS",
                       "verify_token": "VERIFY_SECRET"}.items():
        channel_credential_repo.set(channel_id, key, value)
    return channel_id


def _new_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _seed_contact(phone: str) -> dict:
    return contact_repo.get_or_create(phone)


def _seed_conversation(contact_id: int, *, status: str = "open") -> dict:
    jid = f"{_new_phone()}@s.whatsapp.net"
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact_id, source_id=jid, source_jid=jid)
    return conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact_id, contact_inbox_id=ci["id"],
        status=status)


# ── Envelope literal (§11.1) ─────────────────────────────────────────────────

def _system_envelope(from_number: str, *, msg_id: str | None = None) -> dict:
    body = f"User A changed from {from_number} to {NEW_WA_ID}"
    msg = {
        "from": from_number,
        "id": msg_id or f"wamid.p82sys.{uuid.uuid4().hex[:16]}",
        "timestamp": "1750269342",
        "type": "system",
        "system": {"body": body, "wa_id": NEW_WA_ID, "type": SYS_TYPE},
    }
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA_P82_SYS", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111",
                             "phone_number_id": PHONE_NUMBER_ID},
                "messages": [msg]}}]}]}


def _post(built, channel_id: str, envelope: dict):
    return built.client.post(
        f"/api/webhook/whatsapp_cloud/{channel_id}", json=envelope)


def _for_phone(payloads: list[dict], phone: str) -> list[dict]:
    return [p for p in payloads if p.get("phone") == phone]


def _cards(conversation_id: int) -> list[dict]:
    return [m for m in message_repo.get_by_conversation(conversation_id)
            if m.get("role") == "conversation_event"]


# ── P2 (a): conversa ABERTA → card, sem IA, sem automação ────────────────────

def test_open_conversation_gets_card_no_ai_no_automation(build_app):
    channel_id = _make_cloud_channel()
    phone = _new_phone()
    contact = _seed_contact(phone)
    conv = _seed_conversation(contact["id"], status="open")
    built = build_app(["whatsapp_cloud"])

    with EventRecorder() as rec:
        r = _post(built, channel_id, _system_envelope(phone))
        assert r.status_code == 200, r.text
        rec.drain()

    # 1 card painel-only, com o texto pronto da Meta (prefixo ℹ️).
    cards = _cards(conv["id"])
    assert len(cards) == 1, cards
    assert "changed from" in cards[0]["content"]
    assert cards[0]["content"].startswith("ℹ️")
    assert cards[0]["msg_id"] is None  # nunca conta como não-lida

    # Não-acionabilidade: NENHUM message.saved/received para este número ⇒
    # protocolos & cia. jamais disparam. E a IA nunca foi chamada (o ramo não
    # passa pelo ingest), então não há mensagem de saída (assistant).
    assert _for_phone(rec.by_name("message.saved"), phone) == []
    assert _for_phone(rec.by_name("message.received"), phone) == []
    outgoing = [m for m in message_repo.get_by_conversation(conv["id"])
                if m.get("role") == "assistant"]
    assert outgoing == [], "a IA não pode responder a um evento de sistema"

    # O hook opt-in saiu, com o subtipo estruturado e a NOVA identidade.
    sysev = _for_phone(rec.by_name("channel.system_event"), phone)
    assert len(sysev) == 1, sysev
    assert sysev[0]["system_type"] == SYS_TYPE
    assert sysev[0]["wa_id"] == NEW_WA_ID
    assert sysev[0]["conversation_id"] == conv["id"]
    assert sysev[0]["channel_id"] == channel_id

    # Conversa segue ABERTA e nenhuma nova conversa nasceu.
    assert conversation_repo.get(conv["id"])["status"] == "open"
    assert conversation_repo.get_latest_for_contact(contact["id"])["id"] == conv["id"]


# ── P2 (b): conversa FECHADA → card, mas NÃO reabre ──────────────────────────

def test_closed_conversation_gets_card_but_stays_closed(build_app):
    channel_id = _make_cloud_channel()
    phone = _new_phone()
    contact = _seed_contact(phone)
    conv = _seed_conversation(contact["id"], status="closed")
    built = build_app(["whatsapp_cloud"])

    with EventRecorder() as rec:
        assert _post(built, channel_id, _system_envelope(phone)).status_code == 200
        rec.drain()

    assert len(_cards(conv["id"])) == 1
    # A garantia de P2: a conversa fechada CONTINUA fechada (sem set_status).
    assert conversation_repo.get(conv["id"])["status"] == "closed"
    assert _for_phone(rec.by_name("message.saved"), phone) == []


# ── P2 (c1): contato SEM conversa → nada gravado, conversa não criada ────────

def test_contact_without_conversation_writes_nothing(build_app):
    channel_id = _make_cloud_channel()
    phone = _new_phone()
    contact = _seed_contact(phone)  # contato existe, mas SEM conversa
    built = build_app(["whatsapp_cloud"])

    with EventRecorder() as rec:
        assert _post(built, channel_id, _system_envelope(phone)).status_code == 200
        rec.drain()

    # Nenhuma conversa foi criada e nenhum card foi gravado.
    assert conversation_repo.get_latest_for_contact(contact["id"]) is None
    assert message_repo.get_all(contact["id"]) == []
    # Mas o hook opt-in ainda sai (conversation_id=None), p/ automações que
    # queiram reagir à troca de número mesmo sem thread.
    sysev = _for_phone(rec.by_name("channel.system_event"), phone)
    assert len(sysev) == 1
    assert sysev[0]["conversation_id"] is None
    assert _for_phone(rec.by_name("message.saved"), phone) == []


# ── P2 (c2): SEM contato → não materializa contato nem conversa ──────────────

def test_unknown_contact_is_not_materialized(build_app):
    channel_id = _make_cloud_channel()
    phone = _new_phone()  # nunca virou contato
    built = build_app(["whatsapp_cloud"])

    with EventRecorder() as rec:
        assert _post(built, channel_id, _system_envelope(phone)).status_code == 200
        rec.drain()

    assert contact_repo.get_by_phone(phone) is None, \
        "um evento de sistema NÃO pode materializar um contato-fantasma"
    sysev = _for_phone(rec.by_name("channel.system_event"), phone)
    assert len(sysev) == 1
    assert sysev[0]["conversation_id"] is None


# ── R8/P3: reentrega da Meta não duplica card nem re-emite o evento ──────────

def test_meta_redelivery_does_not_duplicate(build_app):
    channel_id = _make_cloud_channel()
    phone = _new_phone()
    contact = _seed_contact(phone)
    conv = _seed_conversation(contact["id"], status="open")
    built = build_app(["whatsapp_cloud"])
    msg_id = f"wamid.p82dedup.{uuid.uuid4().hex[:16]}"

    with EventRecorder() as rec:
        # Mesmo external_msg_id entregue duas vezes (a Meta reentrega de rotina).
        assert _post(built, channel_id,
                     _system_envelope(phone, msg_id=msg_id)).status_code == 200
        assert _post(built, channel_id,
                     _system_envelope(phone, msg_id=msg_id)).status_code == 200
        rec.drain()

    assert len(_cards(conv["id"])) == 1, "reentrega não pode duplicar o card"
    assert len(_for_phone(rec.by_name("channel.system_event"), phone)) == 1, \
        "reentrega não pode re-emitir o evento de bus"
