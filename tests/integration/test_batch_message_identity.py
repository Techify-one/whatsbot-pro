"""Plano 146 — o batch de texto NÃO mescla mais o histórico.

Incidente que originou o plano (conversa **10886** de produção, 19/08): a cliente
mandou duas frases seguidas; o batch as gravou num ``content`` único, sob o
``msg_id`` da **segunda**. O atendente clicou "Responder" na **primeira** e a
bolha citada saiu **"Mensagem original indisponível"** — o ``msg_id`` citado nunca
foi persistido. Como a linha combinada também herdava o ``ts`` da segunda frase, a
resposta ainda renderizava **acima** do que ela citava.

Medição em produção (2026-08-28): 2.128 mensagens com citação, **33 órfãs**, todas
nativas, a primeira em 22/07 (quando o plano 75 passou a capturar a citação) e a
última no próprio dia da medição — ~1 por dia.

A mescla continua existindo onde serve: o ``combined`` é a entrada do CICLO da IA
(um turno, uma chamada de LLM, uma resposta). O que mudou é que ela deixou de ser
a linha do banco.

Este arquivo nasceu na **F0.1 caracterizando o bug** — A, B e C descreviam a
mescla e passavam, D e E falhavam. A **F1 inverteu A, B e C no mesmo commit** (é
o registro da mudança) e tornou D e E verdes:

===  ==========================================================  ==========================================
      Antes da F1 (caracterização do defeito)                     Depois da F1 (o contrato)
===  ==========================================================  ==========================================
 A    1 linha; msg_id/ts da última; textos colados                N linhas, cada uma com a sua identidade
 B    1 ``message.saved`` batch_text, com o texto combinado       N eventos, um por mensagem (P1 = (a))
 C    autoritativo com ``supersedes``                             sem ``supersedes``; cada bolha reconcilia
 D    citação à 1ª mensagem ficava órfã                           resolve
 E    resposta renderizava acima das duas                         ordena entre elas
===  ==========================================================  ==========================================

⚠️ ``build_inbound_saved_message`` **mantém** o parâmetro ``supersedes`` e o
frontend mantém ``dropSuperseded``: as linhas já mescladas continuam no banco (o
plano não é retroativo — o msg_id engolido não existe em lugar nenhum, não há
backfill possível) e um rollback do core tem de encontrar o cliente preparado.

    WHATSBOT_TEST_DB_URL=... venv/bin/python -m pytest \
        tests/integration/test_batch_message_identity.py -q
"""

from __future__ import annotations

import asyncio
import uuid

from db.repositories import contact_repo, message_repo
from tests.golden import EventRecorder


# Epoch fixo no passado (Nov/2023): separa sem ambiguidade o "ts real do provedor"
# do ``time.time()`` do INSERT.
BASE = 1_700_000_000.0

# ⚠️ NÃO use 0 aqui. Com delay zero o orquestrador consome a fila antes do 2º POST
# e as duas mensagens caem em batches DIFERENTES — o teste passaria a caracterizar
# outra coisa. Com delay > 0, o 2º POST cancela e re-agenda o orquestrador
# (``schedule_orchestrator``, messaging_service.py:1323), que então acha os DOIS
# itens em ``pending_messages``. É o oposto do idioma dos outros testes de inbound.
BATCH_DELAY = 0.4


def _new_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _drain(built, channel_id: str, phone: str) -> None:
    """Espera o ciclo do batch terminar, na loop do TestClient.

    Idioma de ``test_inbound_provider_ts_ordering``. O timeout é generoso porque o
    ciclo paga o ``ai_sequential_delay`` (2s FIXOS no call site — o default está no
    ``ai_settings.value(...)``, não no config global, então ``settings_overrides``
    não o encurta) além do próprio ``BATCH_DELAY``."""
    async def _run():
        for _ in range(8):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=15.0)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_run)


def _post(built, phone: str, msg_id: str, body: str, ts: float,
          reply_to: str | None = None) -> None:
    """POST de um inbound GOWA de texto — SEM drenar (o drain é do batch inteiro)."""
    payload: dict = {
        "from": f"{phone}@s.whatsapp.net",
        "id": msg_id,
        "body": body,
        "timestamp": ts,
        "from_name": "Cliente Teste",
    }
    if reply_to:
        payload["reply_message_id"] = reply_to
    r = built.client.post("/api/webhook/gowa/default",
                          json={"event": "message", "payload": payload})
    assert r.status_code == 200, r.text


def _post_batch(built, phone: str, msgs: list[tuple[str, str, float]]) -> None:
    """POST N mensagens que têm de cair no MESMO batch, e drena uma vez só."""
    for msg_id, body, ts in msgs:
        _post(built, phone, msg_id, body, ts)
    _drain(built, "default", phone)


def _user_rows(phone: str) -> list[dict]:
    """Linhas ``role='user'`` do contato, em ordem cronológica."""
    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "o inbound precisa ter materializado o contato"
    return [r for r in message_repo.get_all(contact["id"]) if r.get("role") == "user"]


def _build(build_app):
    return build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": BATCH_DELAY})


# ── A — uma linha por mensagem do cliente ────────────────────────────────────

def test_batch_saves_one_row_per_client_message(build_app):
    """CONTRATO: duas mensagens no mesmo batch viram DUAS linhas, cada uma com o
    seu ``msg_id``, o seu ``ts`` do provedor e só o seu texto.

    ⚠️ **INVERTIDO NA F1.** Até o plano 146 este teste afirmava o contrário — UMA
    linha, com a identidade da segunda mensagem e os dois textos colados por uma
    quebra de linha. Era a caracterização do defeito, não o comportamento
    desejado."""
    built = _build(build_app)
    phone = _new_phone()
    first, second = f"a1_{uuid.uuid4().hex[:10]}", f"a2_{uuid.uuid4().hex[:10]}"

    _post_batch(built, phone, [
        (first, "Vou tentar abrir o computador", BASE + 10),
        (second, "Eu vou ver ainda se a vm está lá", BASE + 30),
    ])

    rows = _user_rows(phone)
    assert len(rows) == 2, \
        f"uma linha por mensagem do cliente; obtido={[r['content'] for r in rows]}"
    a, b = rows
    assert (a["msg_id"], a["ts"], a["content"]) == (
        first, BASE + 10, "Vou tentar abrir o computador")
    assert (b["msg_id"], b["ts"], b["content"]) == (
        second, BASE + 30, "Eu vou ver ainda se a vm está lá")
    assert "\n" not in a["content"] and "\n" not in b["content"], \
        "nenhuma linha carrega o texto de outra"
    # O núcleo da correção: o msg_id da PRIMEIRA sobrevive e é resolvível.
    # (``get_by_msg_ids`` EXIGE escopo — sem ele levanta ValueError.)
    found = message_repo.get_by_msg_ids([first], conversation_id=a["conversation_id"])
    assert first in found, "o msg_id da 1ª mensagem tem de sobreviver ao save"


# ── B — um message.saved por mensagem ────────────────────────────────────────

def test_batch_text_emits_one_message_saved_per_message(build_app):
    """CONTRATO (P1 = opção (a)): o batch de texto emite UM ``message.saved``
    ``source='batch_text'`` **por mensagem**, alinhado ao ramo de mídia, que
    sempre emitiu um por item (``source='batch_media'``).

    ⚠️ **INVERTIDO NA F1.** Antes o evento era único por batch e levava o texto
    COMBINADO. A auditoria da F0.2 percorreu os quatro assinantes reais
    (``protocolos``, ``retornos``, ``telegram``, ``debug_bus``) e nenhum depende
    de "1 evento por turno" de um jeito que quebre."""
    built = _build(build_app)
    phone = _new_phone()

    with EventRecorder() as rec:
        _post_batch(built, phone, [
            (f"b1_{uuid.uuid4().hex[:10]}", "primeira", BASE + 10),
            (f"b2_{uuid.uuid4().hex[:10]}", "segunda", BASE + 30),
        ])
        rec.drain()

    saved = [p for p in rec.by_name("message.saved")
             if p.get("phone") == phone and p.get("source") == "batch_text"]
    assert len(saved) == 2, f"um evento por mensagem; obtido={len(saved)}"
    assert [p["text"] for p in saved] == ["primeira", "segunda"], \
        "cada payload leva o texto DA SUA mensagem, na ordem de chegada"
    assert all(p.get("conversation_id") for p in saved), \
        "todo evento resolve a conversa (plano 123 F2)"


# ── C — o autoritativo não manda mais colapsar ───────────────────────────────

def test_batch_authoritative_has_no_supersedes(build_app):
    """CONTRATO: o ``new_message`` autoritativo pós-save (plano 57) sai UM POR
    MENSAGEM e **sem** ``supersedes`` — cada bolha otimista de t=0 reconcilia com
    a sua própria linha pelo ``msg_id``, como a mídia sempre fez.

    ⚠️ **INVERTIDO NA F1.** Antes havia um único autoritativo levando
    ``supersedes=[msg_id das engolidas]``, mandando o painel APAGAR as bolhas das
    mensagens anteriores. Era isso que fazia o operador ver certo ao vivo e
    mesclado ao reabrir.

    ``build_inbound_saved_message`` **mantém** o parâmetro e o frontend mantém
    ``dropSuperseded`` (M7/R6): linhas mescladas legadas seguem no banco e um
    rollback do core tem de encontrar o cliente preparado."""
    built = _build(build_app)
    phone = _new_phone()
    first, second = f"c1_{uuid.uuid4().hex[:10]}", f"c2_{uuid.uuid4().hex[:10]}"

    ws = built.app.state.deps.ws_manager
    original = ws.broadcast
    seen: list[tuple[str, dict]] = []

    async def _spy(event, data=None):
        seen.append((event, data or {}))
        return await original(event, data)

    ws.broadcast = _spy
    try:
        _post_batch(built, phone, [
            (first, "primeira", BASE + 10),
            (second, "segunda", BASE + 30),
        ])
    finally:
        ws.broadcast = original

    # O autoritativo é o único new_message que carrega o ``_id`` real da linha
    # (o otimista de t=0, emitido pelo ingest, não tem).
    authoritative = [d for ev, d in seen
                     if ev == "new_message" and d.get("phone") == phone
                     and (d.get("message") or {}).get("_id")]
    assert [d["message"]["msg_id"] for d in authoritative] == [first, second], \
        "um autoritativo por mensagem, na ordem de chegada"
    assert not any("supersedes" in d["message"] for d in authoritative), \
        "nenhum autoritativo manda o painel colapsar — não há mais o que colapsar"


# ── D — O DEFEITO RELATADO: a citação à 1ª mensagem resolve ──────────────────

def test_reply_quoting_first_batch_message_resolves(build_app):
    """REGRESSÃO do defeito relatado (conversa 10886). Vermelho antes da F1.

    O atendente responde (botão direito) à PRIMEIRA das duas mensagens do batch.
    O alvo tem de ser resolvível: presente na página, ou hidratado em ``quoted``
    por ``_hydrate_quoted`` (conversations.py:44). Antes da F1 não era nem um nem
    outro — a linha não existia — e o painel desenhava "Mensagem original
    indisponível"."""
    built = _build(build_app)
    phone = _new_phone()
    first, second = f"d1_{uuid.uuid4().hex[:10]}", f"d2_{uuid.uuid4().hex[:10]}"

    _post_batch(built, phone, [
        (first, "Vou tentar abrir o computador", BASE + 10),
        (second, "Eu vou ver ainda se a vm está lá", BASE + 30),
    ])

    contact = contact_repo.get_by_phone(phone)
    conv_id = _user_rows(phone)[0]["conversation_id"]

    # O atendente cita a PRIMEIRA (o clique com o botão direito nela).
    message_repo.add(contact["id"], "assistant", "Sem problemas, fico no aguardo.",
                     status="operator", msg_id=f"dr_{uuid.uuid4().hex[:10]}",
                     reply_to_msg_id=first, conversation_id=conv_id, ts=BASE + 40)

    found = message_repo.get_by_msg_ids([first], conversation_id=conv_id)
    assert first in found, (
        "a mensagem citada tem de existir como LINHA para a citação resolver — "
        "antes da F1 o batch descartava o msg_id da 1ª e a bolha virava "
        "'Mensagem original indisponível' (conversa 10886)")
    assert found[first]["content"] == "Vou tentar abrir o computador", \
        "a citação mostra SÓ o texto da mensagem citada, não as duas coladas"


# ── E — o 2º sintoma: a resposta sobe acima do que ela cita ──────────────────

def test_reply_between_two_batch_messages_orders_between(build_app):
    """REGRESSÃO do 2º sintoma. Vermelho antes da F1.

    A resposta escrita ENTRE as duas mensagens do cliente tem de renderizar ENTRE
    elas. Antes, a linha combinada herdava o ``ts`` da segunda (BASE+30) e a
    resposta de BASE+20 ficava ANTES das duas — o que o print do incidente
    mostra."""
    built = _build(build_app)
    phone = _new_phone()
    first, second = f"e1_{uuid.uuid4().hex[:10]}", f"e2_{uuid.uuid4().hex[:10]}"
    reply = f"er_{uuid.uuid4().hex[:10]}"

    _post_batch(built, phone, [
        (first, "primeira do cliente", BASE + 10),
        (second, "segunda do cliente", BASE + 30),
    ])

    contact = contact_repo.get_by_phone(phone)
    conv_id = _user_rows(phone)[0]["conversation_id"]
    message_repo.add(contact["id"], "assistant", "resposta do atendente",
                     status="operator", msg_id=reply, reply_to_msg_id=first,
                     conversation_id=conv_id, ts=BASE + 20)

    order = [r.get("msg_id") for r in message_repo.get_by_conversation(conv_id)]
    assert first in order, f"a 1ª mensagem do batch tem de estar no fio; ordem={order}"
    assert order.index(first) < order.index(reply) < order.index(second), (
        "a ordem por (ts, id) tem de ser 1ª do cliente → resposta → 2ª do cliente; "
        f"ordem obtida={order}")


# ── F2 — a hidratação fora da página, e o fallback que não pode sumir ────────

def test_quoted_target_outside_page_is_hydrated(build_app):
    """A correção tem de valer também quando o alvo saiu da janela paginada.

    O painel resolve a citação client-side varrendo só a janela carregada; para o
    alvo fora dela quem resolve é ``_hydrate_quoted`` (plano 75 F10). Antes da F1
    nem essa saída existia para a 1ª mensagem de um batch: não havia linha.

    Chamado direto na função (e não pela rota) de propósito — a rota exigiria
    criar um usuário, e criar usuário LIGA o RBAC para o resto da suíte, que
    compartilha o banco do processo."""
    from server.routes.conversations import _hydrate_quoted

    built = _build(build_app)
    phone = _new_phone()
    first, second = f"h1_{uuid.uuid4().hex[:10]}", f"h2_{uuid.uuid4().hex[:10]}"

    _post_batch(built, phone, [
        (first, "primeira do batch, o alvo da citação", BASE + 10),
        (second, "segunda do batch", BASE + 30),
    ])

    contact = contact_repo.get_by_phone(phone)
    conv_id = _user_rows(phone)[0]["conversation_id"]
    reply = f"hr_{uuid.uuid4().hex[:10]}"
    message_repo.add(contact["id"], "assistant", "respondendo à primeira",
                     status="operator", msg_id=reply, reply_to_msg_id=first,
                     conversation_id=conv_id, ts=BASE + 40)

    # Página que NÃO contém o alvo (só a resposta) — o caso do keyset.
    page = [{"msg_id": reply, "reply_to_msg_id": first, "content": "respondendo à primeira"}]
    _hydrate_quoted(page, conv_id)

    assert "quoted" in page[0], \
        "o alvo fora da página tem de voltar hidratado em ``quoted``"
    assert page[0]["quoted"]["content"] == "primeira do batch, o alvo da citação"
    assert page[0]["quoted"]["role"] == "user"


def test_quoted_target_that_never_existed_still_falls_back(build_app):
    """GUARD: citação a um ``msg_id`` que NUNCA existiu continua sem ``quoted``.

    É o fallback "Mensagem original indisponível" do painel
    ([MessageBubble.js:111](../../web/static/js/components/contacts/MessageBubble.js#L111)).
    A correção do plano 146 não pode fazê-lo desaparecer: mensagem citada de antes
    da instalação, apagada, ou de outra conversa continua sem origem — e o painel
    tem de dizer isso, não inventar."""
    from server.routes.conversations import _hydrate_quoted

    built = _build(build_app)
    phone = _new_phone()
    _post_batch(built, phone, [(f"n1_{uuid.uuid4().hex[:10]}", "só uma", BASE + 10)])
    conv_id = _user_rows(phone)[0]["conversation_id"]

    ghost = f"nunca_existiu_{uuid.uuid4().hex[:10]}"
    page = [{"msg_id": "qualquer", "reply_to_msg_id": ghost, "content": "cita um fantasma"}]
    _hydrate_quoted(page, conv_id)

    assert "quoted" not in page[0], \
        "alvo inexistente não pode ganhar um ``quoted`` inventado"


# ── F4 — o que o modelo passa a ver (P2 = opção (a)) ────────────────────────

def test_ai_context_shows_two_consecutive_user_turns(build_app):
    """CONTRATO da P2 (a): o modelo passa a ver N turnos ``user`` consecutivos,
    no lugar de um turno com o texto colado.

    ⚠️ O achado que fixou a decisão: o batch chama
    ``aprocess_message(phone, combined, save_user_message=False, …)``, e com
    ``save_user_message=False`` o argumento ``text`` é **descartado**
    ([agent_run_service.py:269-270](../../app/services/agent_run_service.py#L269)) —
    o que o modelo recebe é só o histórico lido do banco
    ([agent_run_service.py:279](../../app/services/agent_run_service.py#L279)).
    Ou seja: quem mesclava para a IA era a LINHA, não o argumento. Mudar o save
    muda a entrada do modelo, e é por isso que isto é testado explicitamente em
    vez de ficar como efeito colateral a descobrir depois.

    O conteúdo é o mesmo e a ordem é a mesma; muda a forma e a contagem da janela
    (``max_context_messages``, padrão 10 — mantido, ver P3)."""
    from agent.memory import ContactMemory

    built = _build(build_app)
    phone = _new_phone()
    _post_batch(built, phone, [
        (f"i1_{uuid.uuid4().hex[:10]}", "Vou tentar abrir o computador", BASE + 10),
        (f"i2_{uuid.uuid4().hex[:10]}", "Eu vou ver ainda se a vm está lá", BASE + 30),
    ])

    ctx = ContactMemory(phone).get_context_messages(10)
    users = [m for m in ctx if m.get("role") == "user"]
    assert [m["content"] for m in users] == [
        "Vou tentar abrir o computador",
        "Eu vou ver ainda se a vm está lá",
    ], f"dois turnos user consecutivos, na ordem de chegada; obtido={users}"
