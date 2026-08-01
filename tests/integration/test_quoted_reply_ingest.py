"""Plano 75 F9 — a citação do cliente chega ao BANCO, não só ao ``InboundEvent``.

O teste de parsing do plugin WhatsApp Cloud prova o PARSE (``context.id`` →
``InboundEvent.reply_to_msg_id``). Isso não basta: entre o parse e a bolha
citada há o ingest (``message_ingest_service``) e o batch
(``messaging_service``), e é lá que um ``reply_to_msg_id`` some sem ninguém
notar — o parse continuaria verde. Este arquivo fecha o vão indo do POST no
webhook até a LINHA GRAVADA em ``messages``.

Cobre:

* **Cloud API** — envelope com ``context.id`` apontando para uma mensagem que
  existe ⇒ a linha nova nasce com ``reply_to_msg_id`` preenchido;
* **GOWA (regressão, F9 item 6)** — o GOWA já funcionava em produção e ficou
  FORA da correção; o plano pede o teste nominal com a fixture real da
  reprodução (``3EB0AD7A3F6A10BB1D2C5A`` citando ``3EB035666D2722BFDFA063``),
  para que mexer no plugin Cloud nunca derrube o caminho que já estava certo;
* **alvo inexistente** — a citação é gravada assim mesmo (o painel resolve
  depois, ou mostra "mensagem original indisponível"); nada pode quebrar o save;
* **prefixo ``WAID:``** — gravado VERBATIM, sem normalização (Risco R12): quatro
  formatos de id convivem no banco e cada um só precisa casar consigo mesmo.

Sobre de onde vem o provider Cloud: o app de teste (``build_app`` →
``tests/support.py``) resolve primeiro a fonte externa indicada por
``WHATSBOT_PLUGIN_SOURCE_ROOT`` e usa a bancada legada do core somente como
fallback de compatibilidade. A cópia eventualmente instalada em
``storages/plugins/`` não é a fonte preferencial destes testes.

    venv/bin/python -m pytest tests/integration/test_quoted_reply_ingest.py -q
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from db.repositories import (contact_inbox_repo, contact_repo, conversation_repo,
                             message_repo)

INBOX_ID = 1  # semeado pela migration 0013
PHONE_NUMBER_ID = "PNID_P75_REPLY"

# Fixture REAL da reprodução em produção (§3.5 do plano, conversa 14970): o
# inbound do GOWA grava o stanza id cru, e a citação vem no MESMO formato.
GOWA_MSG_ID = "3EB0AD7A3F6A10BB1D2C5A"
GOWA_QUOTED_ID = "3EB035666D2722BFDFA063"


# ── Infra comum ─────────────────────────────────────────────────────────────

def _drain_orchestrator(built, channel_id: str, phone: str) -> None:
    """Espera o batch orchestrator terminar, na loop do TestClient.

    ``ingest_event`` só enfileira; a gravação acontece na task de batch. Com
    ``message_batch_delay: 0`` o ciclo roda de imediato, mas ele pode gerar uma
    task de continuação — daí o laço.
    """
    async def _drain():
        for _ in range(6):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_drain)


def _new_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _saved_row(contact_id: int, msg_id: str) -> dict | None:
    for row in message_repo.get_all(contact_id):
        if row.get("msg_id") == msg_id:
            return row
    return None


# ── Cloud API ───────────────────────────────────────────────────────────────

def _make_cloud_channel(channel_id: str) -> None:
    from db.repositories import channel_credential_repo, channel_repo
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    for key, value in {"access_token": "TOKEN_SECRET",
                       "phone_number_id": PHONE_NUMBER_ID,
                       "waba_id": "WABA_P75_REPLY",
                       "verify_token": "VERIFY_SECRET"}.items():
        channel_credential_repo.set(channel_id, key, value)


@pytest.fixture
def cloud_thread(_engine_ready):
    """Canal Cloud + contato + conversa + a mensagem de saída que será CITADA."""
    channel_id = f"p75rep_{uuid.uuid4().hex[:8]}"
    _make_cloud_channel(channel_id)
    phone = _new_phone()
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    target_id = f"wamid.p75rep.{uuid.uuid4().hex[:16]}"
    message_repo.add(contact["id"], "assistant",
                     "Seu boleto vence dia 10, posso ajudar em algo mais?",
                     status="operator", msg_id=target_id,
                     conversation_id=conv["id"])
    return {"channel_id": channel_id, "phone": phone, "contact": contact,
            "conversation": conv, "target_msg_id": target_id}


def _cloud_text_payload(phone: str, msg_id: str, body: str,
                        context_id: str | None) -> dict:
    """Envelope da Cloud API (§12 do plano) para uma mensagem de texto."""
    message: dict = {"from": phone, "id": msg_id, "timestamp": "1751142999",
                     "type": "text", "text": {"body": body}}
    if context_id is not None:
        # ``context.from`` é o número do NEGÓCIO — só ``context.id`` vira citação.
        message["context"] = {"from": "15550001111", "id": context_id}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA_P75_REPLY", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111",
                             "phone_number_id": PHONE_NUMBER_ID},
                "contacts": [{"profile": {"name": "Cliente Teste"}, "wa_id": phone}],
                "messages": [message]}}]}]}


def _post_cloud(built, cloud_thread: dict, msg_id: str, body: str,
                context_id: str | None):
    r = built.client.post(
        f"/api/webhook/whatsapp_cloud/{cloud_thread['channel_id']}",
        json=_cloud_text_payload(cloud_thread["phone"], msg_id, body, context_id))
    assert r.status_code == 200, r.text
    _drain_orchestrator(built, cloud_thread["channel_id"], cloud_thread["phone"])
    return r


def test_cloud_reply_reaches_the_database(build_app, cloud_thread):
    """O caso do plano: o cliente responde a uma mensagem nossa e a linha nasce
    com ``reply_to_msg_id`` — antes da F9 o campo chegava NULL."""
    built = build_app(["whatsapp_cloud"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    inbound_id = f"wamid.p75rep.in.{uuid.uuid4().hex[:12]}"

    _post_cloud(built, cloud_thread, inbound_id, "quero remarcar esse vencimento",
                cloud_thread["target_msg_id"])

    row = _saved_row(cloud_thread["contact"]["id"], inbound_id)
    assert row is not None, "a mensagem recebida precisa ter sido gravada"
    assert row["role"] == "user"
    assert row.get("reply_to_msg_id") == cloud_thread["target_msg_id"]


def test_cloud_message_without_context_has_no_reply(build_app, cloud_thread):
    """Controle: sem ``context`` o campo não pode nascer preenchido."""
    built = build_app(["whatsapp_cloud"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    inbound_id = f"wamid.p75rep.plain.{uuid.uuid4().hex[:12]}"

    _post_cloud(built, cloud_thread, inbound_id, "bom dia", None)

    row = _saved_row(cloud_thread["contact"]["id"], inbound_id)
    assert row is not None
    assert not row.get("reply_to_msg_id")


def test_cloud_reply_to_unknown_target_still_saves(build_app, cloud_thread):
    """Citação de uma mensagem que não está no banco (histórico não importado,
    id divergente): grava o campo assim mesmo e NÃO derruba o save — quem
    resolve (ou mostra 'indisponível') é o painel."""
    built = build_app(["whatsapp_cloud"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    inbound_id = f"wamid.p75rep.orphan.{uuid.uuid4().hex[:12]}"
    ghost = f"wamid.p75rep.NAO-EXISTE.{uuid.uuid4().hex[:8]}"

    _post_cloud(built, cloud_thread, inbound_id, "sobre aquela mensagem antiga",
                ghost)

    row = _saved_row(cloud_thread["contact"]["id"], inbound_id)
    assert row is not None, "o save não pode depender do alvo existir"
    assert row.get("reply_to_msg_id") == ghost


def test_cloud_waid_prefix_is_stored_verbatim(build_app, cloud_thread):
    """R12: nada de normalizar. As 301k linhas importadas do Chatwoot usam
    ``WAID:wamid.…`` e só casam com a forma EXATA."""
    built = build_app(["whatsapp_cloud"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    waid = f"WAID:wamid.p75rep.{uuid.uuid4().hex[:16]}"
    message_repo.add(cloud_thread["contact"]["id"], "assistant",
                     "linha importada do Chatwoot", status="operator",
                     msg_id=waid, conversation_id=cloud_thread["conversation"]["id"])
    inbound_id = f"wamid.p75rep.waid.{uuid.uuid4().hex[:12]}"

    _post_cloud(built, cloud_thread, inbound_id, "isso mesmo", waid)

    row = _saved_row(cloud_thread["contact"]["id"], inbound_id)
    assert row is not None
    assert row.get("reply_to_msg_id") == waid, "o prefixo WAID: tem de sobreviver"
    assert row["reply_to_msg_id"].startswith("WAID:")


# ── GOWA (regressão — F9 item 6) ────────────────────────────────────────────

def _gowa_payload(phone: str, msg_id: str, body: str, quoted: dict) -> dict:
    payload = {"chat_id": f"{phone}@s.whatsapp.net",
               "from": f"{phone}@s.whatsapp.net",
               "id": msg_id, "body": body, "from_name": "Cliente Teste"}
    payload.update(quoted)
    return {"event": "message", "payload": payload}


def _post_gowa(built, phone: str, msg_id: str, body: str, quoted: dict):
    r = built.client.post("/api/webhook/gowa/default",
                          json=_gowa_payload(phone, msg_id, body, quoted))
    assert r.status_code == 200, r.text
    _drain_orchestrator(built, "default", phone)
    return r


@pytest.mark.parametrize("quoted,label", [
    ({"context_info": {"stanza_id": GOWA_QUOTED_ID}}, "context_info.stanza_id"),
    ({"reply_message_id": GOWA_QUOTED_ID}, "reply_message_id"),
])
def test_gowa_reply_still_reaches_the_database(build_app, quoted, label):
    """Regressão nominal do plano (F9 item 6): o GOWA continua extraindo a
    citação e ela continua chegando ao banco. Duas formas do payload porque o
    GOWA aninha o id de formas diferentes entre versões (``_extract_reply_to``
    sonda ambas) — nenhuma pode regredir."""
    phone = _new_phone()
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})

    _post_gowa(built, phone, GOWA_MSG_ID, "teste resposta", quoted)

    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, f"contato não materializado ({label})"
    row = _saved_row(contact["id"], GOWA_MSG_ID)
    assert row is not None, f"mensagem não gravada ({label})"
    assert row.get("reply_to_msg_id") == GOWA_QUOTED_ID, label


def test_gowa_message_without_quote_has_no_reply(build_app):
    """Controle do lado GOWA: mensagem solta não pode ganhar citação fantasma
    (o sonda-recursivo de ``_extract_reply_to`` não pode pegar o id próprio)."""
    phone = _new_phone()
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})

    _post_gowa(built, phone, GOWA_MSG_ID, "teste", {})

    contact = contact_repo.get_by_phone(phone)
    assert contact is not None
    row = _saved_row(contact["id"], GOWA_MSG_ID)
    assert row is not None
    assert not row.get("reply_to_msg_id")
