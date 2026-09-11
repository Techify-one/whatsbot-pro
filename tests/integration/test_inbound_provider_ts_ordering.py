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


# ── Plano 141 — o formato REAL do GOWA (RFC 3339) não pode destruir a mensagem ──
#
# A fixture acima injeta ``timestamp`` como float, mas o GOWA de produção manda
# uma STRING RFC 3339 em 100% dos webhooks. Esse buraco na fixture é o que deixou
# o plano 129 passar verde enquanto, em produção, TODO inbound 1:1 dos canais
# GOWA morria no INSERT (``InvalidTextRepresentation``) e a mensagem do cliente
# era destruída em silêncio — 6 dias, ~zero mensagens ``role='user'``.

def _rfc3339(epoch: float) -> str:
    """Epoch → a MESMA forma que o GOWA manda: ``2026-08-24T17:43:58Z``."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_gowa_rfc3339_timestamp_is_persisted_not_dropped(build_app):
    """REGRESSÃO do incidente: payload GOWA com ``timestamp`` RFC 3339 tem de
    persistir a mensagem, com o epoch CORRETO. Vermelho antes do plano 141."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    cid, conv = _seed_conversation(built, phone)

    iso_id = f"iso_{uuid.uuid4().hex[:12]}"
    _post_gowa(built, phone, iso_id, "mensagem com carimbo ISO", ts=_rfc3339(BASE + 500))

    row = next((r for r in message_repo.get_by_conversation(conv)
                if r.get("msg_id") == iso_id), None)
    assert row is not None, (
        "a mensagem do cliente com timestamp RFC 3339 foi DESTRUÍDA — é o "
        "incidente do plano 141 (InvalidTextRepresentation engolido no batch)")
    assert row["ts"] == pytest.approx(BASE + 500), (
        f"o carimbo real do provedor tem de ser preservado; ts gravado={row['ts']}")


def test_gowa_garbage_timestamp_never_costs_the_message(build_app):
    """Carimbo ININTERPRETÁVEL vira ``time.time()`` — nunca perde a mensagem e
    nunca grava 1970. Falha de carimbo ≠ perda de mensagem (D3)."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    cid, conv = _seed_conversation(built, phone)

    bad_id = f"bad_{uuid.uuid4().hex[:12]}"
    _post_gowa(built, phone, bad_id, "carimbo torto", ts="não é uma data")

    row = next((r for r in message_repo.get_by_conversation(conv)
                if r.get("msg_id") == bad_id), None)
    assert row is not None, "carimbo ruim NUNCA pode custar a mensagem do cliente"
    assert row["ts"] > BASE + 1000, f"deveria cair em time.time(); ts={row['ts']}"


def test_inbound_event_coerces_ts_for_any_provider():
    """CONTRATO (I3): nem um provider de plugin de terceiro consegue injetar um
    ``ts`` não-float — a coerção mora na dataclass, não só no parser do GOWA."""
    from channels.events import InboundEvent
    for raw in ["2026-08-24T17:43:58Z", "abc", None, 7, True, {"a": 1}]:
        ev = InboundEvent(channel_id="c", provider="qualquer", ts=raw)
        assert isinstance(ev.ts, float), f"ts={raw!r} não foi coagido: {ev.ts!r}"


def test_epoch_helper_reads_both_forms_and_never_guesses_timezone():
    """O helper aceita epoch E RFC 3339, e trata string NAIVE como UTC.

    ⚠️ ``.timestamp()`` de um datetime naive assume hora LOCAL — em BRT isso
    desloca o carimbo em 3h (a armadilha que já mordeu na migração dos
    agendamentos de retorno). O naive tem de ser carimbado como UTC de propósito.
    """
    from gowa.inbound import _epoch
    assert _epoch("2026-08-24T17:43:58Z") == 1787593438.0
    assert _epoch("2026-08-24T17:43:58+00:00") == 1787593438.0
    assert _epoch("2026-08-24T14:43:58-03:00") == 1787593438.0
    assert _epoch("2026-08-24T17:43:58") == 1787593438.0, "naive tem de ser lido como UTC"
    assert _epoch(1787593438) == 1787593438.0
    assert _epoch("1787593438") == 1787593438.0
    for bad in ("", None, "lixo", True, {"a": 1}):
        assert _epoch(bad) == 0.0, f"{bad!r} deveria virar 0.0 (→ time.time() a jusante)"


# ── F5 — os outros TRÊS saves do plano 129, com o payload que quebrou ────────
#
# ⚠️ O plano 129 abriu QUATRO caminhos de save que passaram a repassar o ``ts``
# do provedor. O incidente do plano 141 só foi observado no M3/M4 (texto 1:1)
# porque os canais GOWA de produção não aceitam grupo e ~99% do tráfego deles é
# grupo — descartado no portão de JID ANTES do ponto que quebra. Os outros três
# estavam igualmente quebrados, só que DORMENTES. Cobrir apenas o que sangrou
# deixaria três minas armadas: basta um operador marcar "grupo" no canal.

def test_m5_media_batch_survives_rfc3339_timestamp(build_app):
    """M5 — mídia no batch ([messaging_service] ``ts=item["ts"]``).

    O item da fila carrega o ``ts`` cru até o save, então a mídia do cliente
    morria no mesmo INSERT que o texto — e aqui a perda é ainda mais silenciosa,
    porque a exceção cai no ``except`` do orquestrador DEPOIS de a fila ter sido
    consumida (o que a F4 acabou de tornar visível)."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    img_id = f"img_{uuid.uuid4().hex[:12]}"

    r = built.client.post("/api/webhook/gowa/default", json={
        "event": "message", "payload": {
            "from": f"{phone}@s.whatsapp.net", "id": img_id,
            "from_name": "Cliente Teste",
            "timestamp": _rfc3339(BASE + 700),
            "image": {"path": "statics/media/foto.jpg", "caption": "olha isso"}}})
    assert r.status_code == 200, r.text
    _drain(built, "default", phone)

    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "o inbound de mídia precisa ter materializado o contato"
    row = next((m for m in message_repo.get_all(contact["id"])
                if m.get("msg_id") == img_id), None)
    assert row is not None, (
        "a MÍDIA do cliente com timestamp RFC 3339 foi destruída no batch (M5)")
    assert row["media_type"] == "image", row
    assert row["ts"] == pytest.approx(BASE + 700), (
        f"o carimbo real do provedor tem de sobreviver ao batch; ts={row['ts']}")


def test_m6_group_without_mention_survives_rfc3339_timestamp(build_app):
    """M6 — grupo sem @menção ([message_ingest_service] ``ts=event.ts``).

    ⚠️ **É o caminho dormente de maior estrago.** Ele salva no histórico sem
    rodar o agente; em produção ficou encoberto só porque os canais GOWA nascem
    **sem `group` marcado** (``GOWA_DEFAULT_JID_TYPES``). Um clique no
    ``JidTypePicker`` teria transformado o incidente de "o 1:1 sumiu" em "o
    grupo inteiro sumiu"."""
    group_jid = f"12036311111{uuid.uuid4().int % 10**4:04d}@g.us"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True,                      # sem @menção não dispara mesmo assim
        "message_batch_delay": 0,
        "group_reply_mode": "mention_only",
    })
    grp_id = f"grp_{uuid.uuid4().hex[:12]}"
    r = built.client.post("/api/webhook/gowa/default", json={
        "event": "message", "payload": {
            "chat_id": group_jid, "from": "5511970000050@s.whatsapp.net",
            "id": grp_id, "body": "mensagem no grupo sem mencionar o bot",
            "from_name": "Participante",
            "timestamp": _rfc3339(BASE + 800)}})
    assert r.status_code == 200, r.text
    _drain(built, "default", group_jid)

    contact = contact_repo.get_by_phone(group_jid)
    assert contact is not None, "o grupo precisa ter sido materializado"
    row = next((m for m in message_repo.get_all(contact["id"])
                if m.get("msg_id") == grp_id), None)
    assert row is not None, (
        "a mensagem de GRUPO com timestamp RFC 3339 foi destruída (M6)")
    assert row["ts"] == pytest.approx(BASE + 800), (
        f"o carimbo real do provedor tem de ser preservado; ts={row['ts']}")


def test_m7_outgoing_echo_survives_rfc3339_timestamp(build_app):
    """M7 — eco do próprio envio ([message_ingest_service] ``ts=event.ts``).

    O eco (``is_from_me``) é o que traz de volta a mensagem que o operador
    mandou pelo CELULAR. Quebrado, o painel perdia justamente o histórico que só
    existe fora dele — e sem nenhum sinal, porque ninguém espera bolha nova ao
    responder pelo aparelho."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    echo_id = f"echo_{uuid.uuid4().hex[:12]}"

    r = built.client.post("/api/webhook/gowa/default", json={
        "event": "message", "payload": {
            "from": f"{phone}@s.whatsapp.net", "id": echo_id,
            "body": "respondi pelo celular", "from_name": "Cliente Teste",
            "is_from_me": True,
            "timestamp": _rfc3339(BASE + 900)}})
    assert r.status_code == 200, r.text
    _drain(built, "default", phone)

    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "o eco precisa ter materializado o contato"
    row = next((m for m in message_repo.get_all(contact["id"])
                if m.get("msg_id") == echo_id), None)
    assert row is not None, (
        "o ECO do próprio envio com timestamp RFC 3339 foi destruído (M7)")
    assert row["role"] == "assistant" and row["status"] == "operator", row
    assert row["ts"] == pytest.approx(BASE + 900), (
        f"o carimbo real do provedor tem de ser preservado; ts={row['ts']}")


# ── F4 — a falha de save do inbound deixa de ser silenciosa ─────────────────

def test_f4_inbound_save_failure_leaves_a_trace(build_app, caplog):
    """Uma exceção no ciclo de inbound tem de produzir LOG e CARD, não silêncio.

    ⚠️ Este é o teste que mede a lição do incidente, não o bug. O defeito de
    tipo foi corrigido em três camadas; o que fez ele durar **6 dias em vez de
    minutos** foi outra coisa: o ``except`` do orquestrador engolia tudo, e o
    erro só existia dentro de ``executions.error`` — tabela que ninguém abre sem
    já estar desconfiado. Enquanto isso o painel mostrava uma conversa aberta e
    vazia, indistinguível de um cliente que só abriu o chat e não digitou.

    Note que o alvo aqui é genérico de propósito: qualquer falha no ciclo, não
    só a de carimbo. É o próximo defeito neste trecho que este teste protege.
    """
    import logging
    from unittest.mock import patch

    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()

    ws = built.app.state.deps.ws_manager
    seen: list[tuple[str, dict]] = []
    original = ws.broadcast

    async def _recording(event, data=None):
        seen.append((event, data or {}))
        return await original(event, data)

    boom = RuntimeError("save do inbound explodiu (simulação do plano 141)")
    with patch.object(ws, "broadcast", _recording), \
            patch("db.repositories.message_repo.add", side_effect=boom), \
            caplog.at_level(logging.ERROR):
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{phone}@s.whatsapp.net",
                "id": f"f4_{uuid.uuid4().hex[:12]}",
                "body": "mensagem que o save vai recusar",
                "from_name": "Cliente Teste",
                "timestamp": _rfc3339(BASE + 1000)}})
        assert r.status_code == 200, r.text
        _drain(built, "default", phone)

    # (b) o log grita — e IDENTIFICA o ciclo de inbound.
    # ⚠️ Um `any(levelno >= ERROR)` genérico NÃO serve aqui: o ciclo já emite
    # ERROR incidental por outros motivos, então a asserção larga passa mesmo
    # sem a correção e não protege nada. O que precisa existir é a linha que
    # diz QUAL contato e QUAL canal ficaram sem a mensagem — é com ela que
    # alguém encontra o problema no dia seguinte, não com um traceback solto.
    culpado = [rec for rec in caplog.records
               if rec.levelno >= logging.ERROR
               and "ciclo de inbound" in rec.getMessage()]
    assert culpado, (
        "a falha do ciclo de inbound não deixou registro NOMEADO em ERROR — é "
        "exatamente assim que o incidente do plano 141 durou 6 dias. ERROs "
        f"vistos: {[r.getMessage()[:60] for r in caplog.records if r.levelno >= logging.ERROR]}")
    assert phone in culpado[0].getMessage(), (
        f"o log tem de dizer de QUEM era a mensagem: {culpado[0].getMessage()!r}")

    # (c) o atendente vê
    cards = [d for ev, d in seen
             if ev == "new_message" and (d.get("message") or {}).get("role") == "error"]
    assert cards, (
        "nenhuma bolha de erro foi emitida: para o atendente, a mensagem do "
        f"cliente simplesmente não existiu. Eventos vistos: {[e for e, _ in seen]}")
