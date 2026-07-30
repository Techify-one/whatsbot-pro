"""Plano 95 F5 — prova ponta a ponta de que a mensagem vazia da Meta não cria NADA.

O payload é o literal capturado em produção (§2.1 do plano): a Cloud API
entregou ``type: "unsupported"`` + ``errors[0].code = 131051`` e nenhum corpo —
um código 2FA do Facebook para o número que está na API oficial. Antes do plano
isso materializava contato, contava não-lida, reabria atendimento, abria
protocolo e acordava a IA.

Por que POST no webhook de verdade em vez de chamar ``parse_inbound``: o buraco
que o plano fecha (§2.3) está justamente ANTES do
``filter.message.before_save`` — contato (``message_ingest_service.py:410``) e
``increment_unread`` (``:429``) já rodaram quando aquele filtro é consultado. Só
o caminho completo prova que nenhum dos dois acontece.

    WHATSBOT_TEST_DB_URL=.../whatsbot_test_95 \\
        venv/bin/python -m pytest tests/test_plano95_ignore_empty_e2e.py -q
"""

from __future__ import annotations

import asyncio
import sys
import uuid

import pytest

from db.repositories import (channel_credential_repo, channel_repo, config_repo,
                             contact_inbox_repo, contact_repo, conversation_repo,
                             message_repo)

INBOX_ID = 1  # semeado pela migration 0013
PHONE_NUMBER_ID = "PNID_P95_E2E"
TOGGLE_KEY = "plugin.whatsapp_cloud.ignore_empty_meta_messages"
CODES_KEY = "plugin.whatsapp_cloud.ignore_error_codes"


# -- Setup -------------------------------------------------------------------

def _make_cloud_channel() -> str:
    channel_id = f"p95_{uuid.uuid4().hex[:8]}"
    channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                        display_name=channel_id, enabled=1)
    for key, value in {"access_token": "TOKEN_SECRET",
                       "phone_number_id": PHONE_NUMBER_ID,
                       "waba_id": "WABA_P95_E2E",
                       "verify_token": "VERIFY_SECRET"}.items():
        channel_credential_repo.set(channel_id, key, value)
    return channel_id


def _new_phone() -> str:
    return f"44797{uuid.uuid4().int % 10**8:08d}"


def _reset_ignore_cache() -> None:
    """Zera o TTL do toggle no modulo do plugin JA CARREGADO pelo app.

    O cache e de 30 s (F3) - sem isto, mudar a config no meio do teste nao teria
    efeito nenhum.
    """
    mod = sys.modules.get("whatsbot_plugins.whatsapp_cloud.channels")
    if mod is not None:
        mod.reset_ignore_settings_cache()


@pytest.fixture
def clean_config(_engine_ready):
    """Config do descarte no default do plano, restaurada ao fim.

    A config vive no banco COMPARTILHADO da sessao - deixar sujo contaminaria
    outros testes.
    """
    before = {k: config_repo.get(k) for k in (TOGGLE_KEY, CODES_KEY)}
    config_repo.set(TOGGLE_KEY, True)
    config_repo.set(CODES_KEY, "")
    _reset_ignore_cache()
    yield
    for key, val in before.items():
        if val is None:
            config_repo.delete_prefix(key)
        else:
            config_repo.set(key, val)
    _reset_ignore_cache()


@pytest.fixture
def channel_id(_engine_ready):
    """O canal precisa existir ANTES do build: o app registra as instancias
    vivas no startup e o webhook so parseia o que esta no registry."""
    return _make_cloud_channel()


@pytest.fixture
def built(build_app, clean_config, channel_id):
    app = build_app(["whatsapp_cloud"], settings_overrides={
        "auto_reply": False,            # a IA nunca deve ser exercida aqui
        "message_batch_delay": 0,       # o batch fecha na hora
        "image_transcription_enabled": False,
        "document_transcription_enabled": False,
    })
    _reset_ignore_cache()               # o modulo so existe depois do build
    return app


# -- Envelopes ---------------------------------------------------------------

def _empty_msg(phone: str, *, code: int = 131051, msg_id: str | None = None) -> dict:
    """O item literal de 2.1 (so o telefone/id variam por teste)."""
    return {
        "from": phone,
        "from_user_id": "GB.2076874439870767",
        "id": msg_id or f"wamid.p95.{uuid.uuid4().hex[:16]}",
        "timestamp": "1785420528",
        "errors": [{"code": code, "title": "Message type unknown",
                    "message": "Message type unknown",
                    "error_data": {"details": "Message type is currently not supported."}}],
        "type": "unsupported",
        "unsupported": {"type": "unknown"},
    }


def _text_msg(phone: str, body: str = "oi, preciso de ajuda") -> dict:
    return {"from": phone, "id": f"wamid.p95txt.{uuid.uuid4().hex[:16]}",
            "timestamp": "1785420600", "type": "text", "text": {"body": body}}


def _envelope(*messages: dict) -> dict:
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA_P95_E2E", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "556299071262",
                             "phone_number_id": PHONE_NUMBER_ID},
                "messages": list(messages)}}]}]}


def _post(built, channel_id: str, envelope: dict):
    r = built.client.post(f"/api/webhook/whatsapp_cloud/{channel_id}", json=envelope)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _drain(built, channel_id: str, phone: str) -> None:
    """Fecha o ciclo de batch na loop do TestClient (molde do plano 75)."""
    async def _run():
        idle = 0
        for _ in range(40):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                idle += 1
                if idle >= 2:
                    return
                await asyncio.sleep(0.05)
                continue
            idle = 0
            try:
                await asyncio.wait_for(task, timeout=8.0)
            except Exception:
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_run)


def _seed_thread(phone: str, *, status: str = "closed") -> dict:
    contact = contact_repo.get_or_create(phone)
    jid = f"{phone}@s.whatsapp.net"
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"],
        status=status)
    return {"contact": contact, "conversation": conv}


# -- O caso do plano ---------------------------------------------------------

def test_empty_message_produces_no_event(built, channel_id):
    data = _post(built, channel_id, _envelope(_empty_msg(_new_phone())))
    assert data["events"] == 0, "a mensagem sem conteudo nao pode virar evento"


def test_unknown_sender_is_not_materialized(built, channel_id):
    """O buraco do 2.3: remetente inedito nao pode virar contato-fantasma."""
    phone = _new_phone()

    _post(built, channel_id, _envelope(_empty_msg(phone)))
    _drain(built, channel_id, phone)

    assert contact_repo.get_by_phone(phone) is None


def test_existing_thread_is_untouched(built, channel_id):
    """Conversa fechada nao reabre, nada e gravado e o badge nao mexe."""
    phone = _new_phone()
    seeded = _seed_thread(phone, status="closed")
    contact_id = seeded["contact"]["id"]
    unread_before = contact_repo.get_by_phone(phone)["unread_count"]
    msgs_before = len(message_repo.get_all(contact_id))

    _post(built, channel_id, _envelope(_empty_msg(phone)))
    _drain(built, channel_id, phone)

    assert len(message_repo.get_all(contact_id)) == msgs_before, "nada pode ser salvo"
    assert contact_repo.get_by_phone(phone)["unread_count"] == unread_before, \
        "increment_unread roda ANTES do filter.message.before_save - dai o corte no parse"
    conv = conversation_repo.get(seeded["conversation"]["id"])
    assert conv["status"] == "closed", "a conversa fechada nao pode reabrir"
    assert conversation_repo.get_latest_for_contact(contact_id)["id"] == \
        seeded["conversation"]["id"], "nenhuma conversa nova pode nascer"


def test_mixed_batch_keeps_the_real_message(built, channel_id):
    """Um lote com vazia + texto perde SO a vazia."""
    phone = _new_phone()
    text = _text_msg(phone)

    data = _post(built, channel_id, _envelope(_empty_msg(phone), text))
    assert data["events"] == 1
    _drain(built, channel_id, phone)

    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "a mensagem de texto tinha que ser ingerida normalmente"
    saved = [m for m in message_repo.get_all(contact["id"]) if m.get("role") == "user"]
    assert [m["content"] for m in saved] == ["oi, preciso de ajuda"]
    assert contact["unread_count"] == 1, "so a de texto conta como nao lida"


def test_receipts_still_arrive(built, channel_id):
    """``statuses[]`` e outro laco - o descarte nao pode calar recibo."""
    phone = _new_phone()
    envelope = _envelope(_empty_msg(phone))
    envelope["entry"][0]["changes"][0]["value"]["statuses"] = [
        {"id": f"wamid.out.{uuid.uuid4().hex[:12]}", "recipient_id": phone,
         "status": "delivered", "timestamp": "1785420700"}]

    data = _post(built, channel_id, envelope)
    assert data["events"] == 1, "o recibo tem que continuar chegando"


# -- Os dois escapes do operador (sem redeploy) ------------------------------

def test_toggle_off_restores_old_behaviour(built, channel_id):
    phone = _new_phone()
    config_repo.set(TOGGLE_KEY, False)
    _reset_ignore_cache()

    data = _post(built, channel_id, _envelope(_empty_msg(phone)))
    assert data["events"] == 1, "desligado, a bolha de aviso volta a ser ingerida"
    _drain(built, channel_id, phone)

    contact = contact_repo.get_by_phone(phone)
    assert contact is not None
    # ``role="user"`` filtra o card ``conversation_event`` de "Conversa iniciada",
    # que o core grava sozinho quando a conversa nasce.
    saved = [m for m in message_repo.get_all(contact["id"]) if m.get("role") == "user"]
    assert len(saved) == 1
    assert saved[0]["media_type"] == "unsupported"
    assert "não suportada" in saved[0]["content"]


def test_narrowed_code_list_lets_other_codes_through(built, channel_id):
    """``ignore_error_codes = "131051"`` estreita a regra ao caso da Meta."""
    phone = _new_phone()
    config_repo.set(CODES_KEY, "131051")
    _reset_ignore_cache()

    # O codigo listado continua descartado...
    assert _post(built, channel_id, _envelope(_empty_msg(phone)))["events"] == 0
    # ...e um `unsupported` de OUTRO codigo volta a aparecer.
    other = _new_phone()
    assert _post(built, channel_id,
                 _envelope(_empty_msg(other, code=999999)))["events"] == 1
