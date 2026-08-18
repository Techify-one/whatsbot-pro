"""Plano 129 — o ``ts`` do INBOUND passa a ser o timestamp REAL do provedor.

Hoje toda mensagem é carimbada com ``ts = time.time()`` no INSERT (relógio do
servidor), e o ``event.ts`` do provedor (Telegram ``date``, GOWA ``timestamp``,
Cloud ``timestamp``) — embora capturado — é descartado antes de persistir. Uma
mensagem entregue com ATRASO (entrega tardia / re-poll pós-restart) ganha um
``ts`` de "agora" e afunda para depois de mensagens que na verdade vieram DEPOIS
dela. Como a thread é ordenada por ``(ts, id)``, ao reabrir a conversa uma
resposta citada aparece ACIMA da mensagem original (incidente da conversa 15651).

Este arquivo é a rede da correção:

* ``test_message_repo_add_persists_explicit_ts`` (F0.1) — CONTRATO: ``message_repo.add``
  já aceita ``ts=`` e o persiste. Verde hoje e depois.
* ``test_delayed_inbound_orders_before_later_outbound`` (F0.2) — REPRODUÇÃO: um
  inbound cujo ``event.ts`` real é ANTERIOR ao de um outbound já salvo ordena
  ANTES dele. **Vermelho antes da F1** (o inbound recebe ``ts=now()`` e afunda),
  **verde depois**.
* ``test_delayed_reply_renders_after_quoted_message`` (F0.2b) — o caso do
  incidente: o outbound cita (``reply_to_msg_id``) o inbound atrasado; após
  releitura a original vem ANTES da resposta. Mesmo gatilho da F1.
* ``test_missing_provider_ts_falls_back_to_now`` (F0.3) — GUARD: inbound sem
  ``timestamp`` (``event.ts = 0.0``) cai em ``time.time()``, nunca grava epoch 0.
  Verde antes e depois (a correção não pode gravar 1970).

Driver: a rota GOWA real (``POST /api/webhook/gowa/default``) — o único caminho
de inbound. O ``payload.timestamp`` vira ``event.ts`` verbatim
([gowa/inbound.py:734](../../gowa/inbound.py#L734) ``data.get("timestamp") or 0.0``),
então o teste controla o relógio do provedor sem depender de plugin externo.

    WHATSBOT_TEST_DB_URL=... venv/bin/python -m pytest \
        tests/integration/test_inbound_provider_ts_ordering.py -q
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from db.repositories import contact_repo, message_repo


# Epoch fixo bem no passado (Set/2020): separa o "ts real do provedor" do
# ``time.time()`` do INSERT sem ambiguidade — qualquer ``ts`` perto de ``now()``
# é ordens de grandeza maior que estes valores.
BASE = 1_600_000_000.0


def _drain(built, channel_id: str, phone: str) -> None:
    """Espera o batch do orquestrador terminar, na loop do TestClient.

    ``ingest_event`` só enfileira; a gravação acontece na task de batch. Com
    ``message_batch_delay: 0`` o ciclo roda de imediato, mas pode gerar uma
    task de continuação — daí o laço (idioma de test_quoted_reply_ingest)."""
    async def _run():
        for _ in range(6):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_run)


def _new_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _post_gowa(built, phone: str, msg_id: str, body: str,
               ts: float | None) -> None:
    """POST um inbound GOWA de texto e drena o batch.

    ``ts=None`` OMITE o campo ``timestamp`` (simula provedor que não o mandou →
    ``event.ts = 0.0``)."""
    payload: dict = {
        "from": f"{phone}@s.whatsapp.net",
        "id": msg_id,
        "body": body,
        "from_name": "Cliente Teste",
    }
    if ts is not None:
        payload["timestamp"] = ts
    r = built.client.post("/api/webhook/gowa/default",
                          json={"event": "message", "payload": payload})
    assert r.status_code == 200, r.text
    _drain(built, "default", phone)


def _order(conversation_id: int) -> list[str]:
    """msg_ids da conversa em ordem cronológica (a mesma ``ORDER BY ts`` da thread)."""
    return [r.get("msg_id") for r in message_repo.get_by_conversation(conversation_id)]


def _seed_conversation(built, phone: str) -> tuple[int, int]:
    """Materializa contato + conversa com um 1º inbound e devolve (contact_id, conversation_id)."""
    seed_id = f"seed_{uuid.uuid4().hex[:12]}"
    _post_gowa(built, phone, seed_id, "primeira mensagem", ts=BASE)
    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "o inbound precisa ter materializado o contato"
    cid = contact["id"]
    seed_row = next((r for r in message_repo.get_all(cid)
                     if r.get("msg_id") == seed_id), None)
    assert seed_row is not None and seed_row.get("conversation_id"), \
        "o 1º inbound precisa ter criado a conversa"
    return cid, seed_row["conversation_id"]


# ── F0.1 — contrato: message_repo.add persiste o ts recebido ─────────────────

def test_message_repo_add_persists_explicit_ts(build_app):
    """``message_repo.add`` já aceita ``ts=`` e grava exatamente esse valor
    (sem ``ts`` cairia em ``time.time()``). É o pré-requisito da F1: basta o
    ``add_message`` encaminhar o ``ts`` até aqui."""
    build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    contact = contact_repo.get_or_create(phone)
    explicit = BASE + 12345.0
    msg_id = f"expl_{uuid.uuid4().hex[:8]}"
    saved = message_repo.add(contact["id"], "user", "linha com ts explícito",
                             msg_id=msg_id, ts=explicit)
    assert saved["ts"] == explicit
    fetched = next(r for r in message_repo.get_all(contact["id"])
                   if r.get("msg_id") == msg_id)
    assert fetched["ts"] == explicit


# ── F0.2 — reprodução: inbound atrasado ordena antes do outbound posterior ───

def test_delayed_inbound_orders_before_later_outbound(build_app):
    """VERMELHO antes da F1, VERDE depois.

    Um inbound com ``event.ts`` real BASE+50 é ENTREGUE tarde (salvo por último),
    depois de um outbound cujo ``ts`` real é BASE+100. A ordem cronológica CERTA
    é inbound→outbound. Hoje o inbound recebe ``ts=now()`` e afunda para o fim."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    cid, conv = _seed_conversation(built, phone)

    # Outbound do operador, salvo NA HORA (ts real BASE+100).
    out_id = f"out_{uuid.uuid4().hex[:12]}"
    message_repo.add(cid, "assistant", "resposta do operador", status="operator",
                     msg_id=out_id, conversation_id=conv, ts=BASE + 100)

    # Inbound do cliente ENTREGUE COM ATRASO: ts real BASE+50, mas salvo por último.
    late_id = f"late_{uuid.uuid4().hex[:12]}"
    _post_gowa(built, phone, late_id, "mensagem atrasada", ts=BASE + 50)

    order = _order(conv)
    assert late_id in order and out_id in order, order
    assert order.index(late_id) < order.index(out_id), (
        "o inbound atrasado (ts real do provedor BASE+50) deve ordenar ANTES do "
        f"outbound posterior (BASE+100); ordem obtida={order}")


# ── F0.2b — o caso do incidente: resposta citada não pode subir acima da original ──

def test_delayed_reply_renders_after_quoted_message(build_app):
    """VERMELHO antes da F1, VERDE depois (conversa 15651).

    O outbound CITA (``reply_to_msg_id``) a mensagem original do cliente, que
    chegou atrasada. Após releitura, a original tem de vir ANTES da resposta que
    a cita — senão o painel desenha a resposta acima da mensagem citada."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    cid, conv = _seed_conversation(built, phone)

    original_id = f"orig_{uuid.uuid4().hex[:12]}"   # do cliente, real ts BASE+50
    reply_id = f"reply_{uuid.uuid4().hex[:12]}"      # do operador, cita a original

    # A resposta do operador é salva ANTES (real ts BASE+100), citando a original.
    message_repo.add(cid, "assistant", "claro, já ajusto isso", status="operator",
                     msg_id=reply_id, reply_to_msg_id=original_id,
                     conversation_id=conv, ts=BASE + 100)

    # A original do cliente chega atrasada (real ts BASE+50, salva por último).
    _post_gowa(built, phone, original_id, "sobre aquele script...", ts=BASE + 50)

    order = _order(conv)
    assert order.index(original_id) < order.index(reply_id), (
        "a mensagem original citada deve renderizar ANTES da resposta que a cita; "
        f"ordem obtida={order}")


# ── F0.3 — guard: ausência de timestamp cai em time.time(), nunca 0.0 ────────

def test_missing_provider_ts_falls_back_to_now(build_app):
    """Inbound sem ``timestamp`` no payload (``event.ts = 0.0``) tem de cair em
    ``time.time()`` — jamais gravar epoch 0 (1970), que destruiria a ordenação.
    Verde antes e depois da F1 (a correção não pode regredir o guard)."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    cid, conv = _seed_conversation(built, phone)

    no_ts_id = f"nots_{uuid.uuid4().hex[:12]}"
    _post_gowa(built, phone, no_ts_id, "sem carimbo do provedor", ts=None)

    row = next((r for r in message_repo.get_by_conversation(conv)
                if r.get("msg_id") == no_ts_id), None)
    assert row is not None, "a mensagem sem timestamp ainda tem de ser salva"
    # Perto de "agora" (2020+), nunca 0.0/epoch 1970. BASE+1000 << now().
    assert row["ts"] > BASE + 1000, (
        f"event.ts=0.0 deveria cair em time.time(); ts gravado={row['ts']}")
