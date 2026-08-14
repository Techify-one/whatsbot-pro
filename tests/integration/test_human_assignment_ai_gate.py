"""Plano 96 — humano no comando cala a IA.

Quatro defeitos independentes, um sintoma só ("cliquei em *Atribuir a mim* e a
IA respondeu mesmo assim"). Este arquivo nasceu na **Fase F0** congelando o
comportamento ATUAL (caracterização) e foi **invertido** pelas fases F1–F3 —
cada teste diz, no docstring, qual dos dois lados está asseverando.

Cobertura:

* **corrida gate↔envio** (F1·I3) — o gate é lido antes do LLM; entre ele e o
  envio cabem 1,6s a 75s. Desligar a IA nessa janela agora CALA;
* **guard entre partes do split** (F1·I3b) — desligar entre a parte 1 e a 2
  entrega a 1 e aborta o resto, sem rasgar mensagem no meio;
* **``abort_ai_cycle``** (F1·I4) — cancela quando seguro e sempre incrementa a
  geração; em ``processing``/``sending`` o guard invalida o ciclo sem perder batch;
* **presença do operador** (F1·I7) — atendente digitando SEGURA a IA;
* **``assign`` cala** (F2·I5) — atribuir a humano zera ``ai_active`` e
  ``active_agent_key``; desatribuir continua não tocando na IA.
* **IA privada** (F3·I9) — gate e geração relidos antes de cada parte no wire.

    venv/bin/python -m pytest tests/integration/test_human_assignment_ai_gate.py -q
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.services.messaging_service import (MessagingContext, MessagingService,
                                            abort_ai_cycle,
                                            abort_ai_cycle_for_conversation,
                                            _conversation_ai_active)
from db.repositories import contact_repo, conversation_repo, message_repo
from server.state import AppState


# ── Fakes mínimos ───────────────────────────────────────────────────────────

@dataclass
class _SendResult:
    ok: bool = True
    external_msg_id: str = "MID"
    error: str | None = None


class _FakeOutbound:
    """Só o que ``send_reply`` toca: capabilities / send_text / send_presence."""

    def __init__(self):
        self.sent: list[str] = []

    def capabilities(self, channel_id):
        return SimpleNamespace(groups=False)

    def send_text(self, channel_id, phone, text, reply_to=None, mentions=None):
        self.sent.append(text)
        return _SendResult()

    def send_presence(self, channel_id, phone, action):
        return None


class _FakeWS:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def broadcast(self, event, data=None):
        self.events.append((event, data or {}))


class _FakeContact:
    """Stand-in de ``ContactMemory`` para o gate (só ``id``/``phone`` importam)."""

    def __init__(self, cid: int, phone: str):
        self.id = cid
        self.phone = phone
        self.is_group = False

    def increment_unread_ai(self):
        return None


class _FakeHandler:
    def __init__(self, contact: _FakeContact):
        self._contact = contact
        self.api_key = "sk-test"

    def _get_contact(self, phone, channel_id="default"):
        return self._contact

    def save_assistant_message(self, *a, **kw):
        return None


CHANNEL = "default"


@pytest.fixture
def cycle(_engine_ready):
    """Contato + conversa aberta neutra (IA on, sem humano) + serviço com fakes."""
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    row = contact_repo.get_or_create(phone)
    conversation_repo.resolve_for_contact(
        row["id"], f"{phone}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(row["id"])
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=None, active_agent_key=None, ai_active=1)

    contact = _FakeContact(row["id"], phone)
    outbound = _FakeOutbound()
    state = AppState()
    deps = SimpleNamespace(ws_manager=_FakeWS(), agent_handler=_FakeHandler(contact),
                           state=state)
    svc = MessagingService(MessagingContext(
        deps=deps,
        agent_handler=deps.agent_handler,
        ws_manager=deps.ws_manager,
        state=state,
        # Delays zerados: o teste mede o gate, não a humanização.
        settings={"split_messages": True, "split_message_delay": 0,
                  "response_delay_min": 0, "response_delay_max": 0},
        outbound=outbound,
        channel_ai_enabled=lambda _cid: True,
    ))
    yield SimpleNamespace(svc=svc, deps=deps, state=state, outbound=outbound,
                          phone=phone, contact=contact, conv=conv)


def _turn_ai_off(conv_id: int, assignee: int = 42) -> None:
    """O que o painel faz ao clicar em *Atribuir a mim* (``set_ai(0)``)."""
    conversation_repo.assign_agent(
        conv_id, assignee_user_id=assignee, active_agent_key=None, ai_active=0)


# ── F1·I2 — o gate endurecido ───────────────────────────────────────────────

def test_gate_bloqueia_humano_mesmo_com_agente_vinculado(cycle):
    """D2: dono humano ⇒ IA muda, INDEPENDENTE de ``active_agent_key``.

    Inverte deliberadamente o contrato antigo (``test_human_gate`` assegurava
    que um agente vinculado neutralizava o assignee). Era cara ou coroa: o
    único escritor de ``active_agent_key`` é a tool ``transferir_agente``, então
    a conversa ficava muda se e somente se a IA não tivesse roteado no turno
    anterior — foi assim que a conversa 12831 voltou a falar sozinha.
    """
    conversation_repo.assign_agent(
        cycle.conv["id"], assignee_user_id=42, active_agent_key="roteador", ai_active=1)
    assert _conversation_ai_active(cycle.contact) is False


def test_gate_sem_humano_nao_muda(cycle):
    """D5: 'IA ativa sem subagente' continua válida — o endurecimento só dispara
    com ``assignee_user_id``."""
    conversation_repo.assign_agent(
        cycle.conv["id"], assignee_user_id=None, active_agent_key=None, ai_active=1)
    assert _conversation_ai_active(cycle.contact) is True


# ── F1·I3 — a corrida entre o gate e o envio ────────────────────────────────

def test_desligar_a_ia_durante_o_llm_cala_o_envio(cycle):
    """F0 congelou o oposto: a resposta saía 1,6–75s depois do clique.

    O gate roda antes do LLM; ``_send_with_typing_guard`` é o ÚLTIMO ponto antes
    de a mensagem virar irreversível, e agora ele reconsulta o veredito.
    """
    _turn_ai_off(cycle.conv["id"])
    sent = asyncio.run(
        cycle.svc._send_with_typing_guard(CHANNEL, cycle.phone, "oi"))
    assert sent is False
    assert cycle.outbound.sent == []


def test_conversa_saudavel_continua_enviando(cycle):
    """Contraprova do teste acima: sem humano, o guard não atrapalha."""
    sent = asyncio.run(
        cycle.svc._send_with_typing_guard(CHANNEL, cycle.phone, "oi"))
    assert sent is True
    assert cycle.outbound.sent == ["oi"]


def test_guard_falha_aberto(cycle):
    """Risco §5: um erro de leitura do gate NÃO pode calar uma conversa
    saudável — o guard herda o fail-open do gate."""
    import app.services.messaging_service as ms

    original = ms._conversation_ai_active
    ms._conversation_ai_active = lambda _c: (_ for _ in ()).throw(RuntimeError("db off"))
    try:
        asyncio.run(cycle.svc._send_with_typing_guard(CHANNEL, cycle.phone, "oi"))
    finally:
        ms._conversation_ai_active = original
    assert cycle.outbound.sent == ["oi"]


# ── F1·I3b — o guard entre partes do split ──────────────────────────────────

def test_desligar_entre_as_partes_para_o_resto(cycle):
    """Parte 1 já entregue fica; da 2ª em diante, nada sai.

    Abortar no meio do laço é o limite LIMPO — por isso ``abort_ai_cycle`` não
    cancela a task durante o envio (ver o teste do ``sending``).
    """
    conv_id = cycle.conv["id"]
    real_send = cycle.outbound.send_text

    def _send_and_take_over(channel_id, phone, text, reply_to=None, mentions=None):
        result = real_send(channel_id, phone, text, reply_to=reply_to, mentions=mentions)
        _turn_ai_off(conv_id)  # o operador assume logo depois da 1ª parte
        return result

    cycle.outbound.send_text = _send_and_take_over
    asyncio.run(cycle.svc.send_reply(CHANNEL, cycle.phone, '["um", "dois", "três"]'))
    assert cycle.outbound.sent == ["um"]


# ── F1·I4 — o seam de aborto ────────────────────────────────────────────────

def test_abort_cancela_o_ciclo_em_voo(cycle):
    """Atribuir/desligar/enviar passam a ter alavanca sobre a task."""
    async def _run():
        key = (CHANNEL, cycle.phone)
        task = asyncio.create_task(asyncio.sleep(30))
        cycle.state.processing_tasks[key] = task
        await asyncio.sleep(0)
        abort_ai_cycle(cycle.deps, CHANNEL, cycle.phone)
        await asyncio.sleep(0)
        return task.cancelled() or task.done()

    assert asyncio.run(_run()) is True


def test_abort_nao_cancela_durante_o_envio(cycle):
    """Risco §5: cancelar com ``state.sending`` ligado rasgaria o split
    (partes 1–2 entregues, a 3 não). Quem interrompe ali é o guard entre partes."""
    async def _run():
        key = (CHANNEL, cycle.phone)
        task = asyncio.create_task(asyncio.sleep(30))
        cycle.state.processing_tasks[key] = task
        cycle.state.sending[key] = True
        await asyncio.sleep(0)
        abort_ai_cycle(cycle.deps, CHANNEL, cycle.phone)
        await asyncio.sleep(0)
        cancelled = task.cancelled()
        task.cancel()
        return cancelled

    assert asyncio.run(_run()) is False


def test_abort_em_processing_invalida_a_geracao_sem_perder_o_batch(cycle):
    """A janela dominante do bug era o LLM, quando ``processing=True``.

    A task não pode ser cancelada ali (o batch já foi removido de ``pending``),
    mas o epoch muda e o guard do mesmo ciclo recusa a resposta. Um envio manual
    agora funciona nessa janela mesmo sem alterar o gate persistido da conversa.
    """
    async def _run():
        key = (CHANNEL, cycle.phone)
        epoch = cycle.svc._abort_epoch(CHANNEL, cycle.phone)
        task = asyncio.create_task(asyncio.sleep(30))
        cycle.state.processing_tasks[key] = task
        cycle.state.processing[key] = True
        try:
            cancelled = abort_ai_cycle(cycle.deps, CHANNEL, cycle.phone)
            sent = await cycle.svc._send_with_typing_guard(
                CHANNEL, cycle.phone, "resposta obsoleta", abort_epoch=epoch)
            return cancelled, task.cancelled(), sent
        finally:
            cycle.state.processing.pop(key, None)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert asyncio.run(_run()) == (False, False, False)
    assert cycle.outbound.sent == []


def test_abort_durante_delay_bloqueia_tambem_a_primeira_parte(cycle):
    """O guard precisa ficar imediatamente antes da parte 1.

    ``sending`` já está ligado durante o delay humanizado; antes, assumir nesses
    1–3 segundos não cancelava a task e a primeira bolha ainda escapava.
    """
    cycle.svc.settings["response_delay_min"] = 0.2
    cycle.svc.settings["response_delay_max"] = 0.2

    async def _run():
        key = (CHANNEL, cycle.phone)
        epoch = cycle.svc._abort_epoch(CHANNEL, cycle.phone)
        task = asyncio.create_task(cycle.svc._send_with_typing_guard(
            CHANNEL, cycle.phone, "primeira parte", abort_epoch=epoch))
        cycle.state.processing_tasks[key] = task
        for _ in range(100):
            if cycle.state.sending.get(key):
                break
            await asyncio.sleep(0.005)
        assert cycle.state.sending.get(key) is True
        assert abort_ai_cycle(cycle.deps, CHANNEL, cycle.phone) is False
        return await asyncio.wait_for(task, timeout=2.0)

    assert asyncio.run(_run()) is False
    assert cycle.outbound.sent == []
    assert cycle.state.msg_count == 0
    assert not any(event == "status" for event, _data in cycle.deps.ws_manager.events)


def test_abort_sem_ciclo_e_no_op(cycle):
    """Best-effort: nunca levanta, mesmo sem task nenhuma registrada."""
    abort_ai_cycle(cycle.deps, CHANNEL, cycle.phone)
    abort_ai_cycle(SimpleNamespace(), CHANNEL, cycle.phone)


def test_abort_por_conversa_resolve_o_canal_da_inbox_exata(cycle, monkeypatch):
    """O mesmo contato pode ter conversas em vários canais.

    O aborto precisa usar o canal da conversa recebida, não o da conversa mais
    recente do contato, senão cancela a task errada em uma instalação multicanal.
    """
    target_channel = "cloud-da-conversa"
    monkeypatch.setattr(
        conversation_repo, "get_with_channel",
        lambda conv_id: {"id": conv_id, "channel_id": target_channel},
    )
    monkeypatch.setattr(
        conversation_repo, "channel_id_for_contact",
        lambda _contact_id: "canal-mais-recente-mas-errado",
    )

    async def _run():
        key = (target_channel, cycle.phone)
        task = asyncio.create_task(asyncio.sleep(30))
        cycle.state.processing_tasks[key] = task
        await asyncio.sleep(0)
        cancelled = abort_ai_cycle_for_conversation(cycle.deps, cycle.conv)
        await asyncio.sleep(0)
        return cancelled, task.cancelled() or task.done()

    assert asyncio.run(_run()) == (True, True)


# ── F1·I7 — presença do operador segura a IA ────────────────────────────────

def test_operador_digitando_segura_a_ia(cycle):
    """D3/P2: SEGURAR, não cancelar — o atendente pode desistir do texto."""
    key = (CHANNEL, cycle.phone)
    cycle.state.operator_typing_state[key] = {"active": True, "last_ts": time.time()}

    async def _run():
        waiter = asyncio.create_task(
            cycle.svc._wait_typing_paused(CHANNEL, cycle.phone))
        await asyncio.sleep(0.5)
        still_waiting = not waiter.done()
        cycle.state.operator_typing_state[key] = {"active": False, "last_ts": time.time()}
        await asyncio.wait_for(waiter, timeout=2.0)
        return still_waiting

    assert asyncio.run(_run()) is True


def test_presenca_do_operador_obsoleta_libera(cycle):
    """Aba fechada sem ``stop``: 15s sem heartbeat (o painel reemite a cada 10s)
    ⇒ a IA não fica travada."""
    key = (CHANNEL, cycle.phone)
    cycle.state.operator_typing_state[key] = {"active": True, "last_ts": time.time() - 60}
    asyncio.run(asyncio.wait_for(
        cycle.svc._wait_typing_paused(CHANNEL, cycle.phone), timeout=2.0))


# ── F2·I5 — atribuir a humano cala ──────────────────────────────────────────

def test_assign_a_humano_cala_a_ia(cycle):
    """D1: ``assign`` (tela Atendimentos, plugin ``agendamento_retorno``) passa a
    escrever as três colunas de posse, como o ``assign_unified`` já fazia."""
    from app.services import conversation_service as conv_svc

    conversation_repo.assign_agent(
        cycle.conv["id"], assignee_user_id=None, active_agent_key="roteador", ai_active=1)
    conv = conversation_repo.get(cycle.conv["id"])
    updated = asyncio.run(conv_svc.assign(cycle.deps, conv, 42))

    assert updated["assignee_user_id"] == 42
    assert updated["ai_active"] in (0, False)
    assert not updated["active_agent_key"]
    assert _conversation_ai_active(cycle.contact) is False


def test_desatribuir_nao_mexe_na_ia(cycle):
    """⚠️ Assimetria deliberada: limpar o dono NÃO religa a IA (senão soltar uma
    conversa devolveria o cliente ao bot sem ninguém pedir)."""
    from app.services import conversation_service as conv_svc

    conversation_repo.assign_agent(
        cycle.conv["id"], assignee_user_id=7, active_agent_key=None, ai_active=0)
    conv = conversation_repo.get(cycle.conv["id"])
    updated = asyncio.run(conv_svc.assign(cycle.deps, conv, None))

    assert updated["assignee_user_id"] is None
    assert updated["ai_active"] in (0, False)


def test_cenario_do_clique_no_meio_do_ciclo(cycle):
    """F5 · caso 15132 — a reprodução do sintoma que originou o plano.

    Cliente escreve → o ciclo começa → o operador clica em *Atribuir a mim*
    (``set_ai(0)``) enquanto o balão "IA respondendo…" está no ar → a resposta
    que o LLM acabou de produzir NÃO chega ao cliente. Exercita a corrente
    inteira: ``set_ai`` escreve as 3 colunas, aborta o ciclo, e o guard de envio
    recusa o que já estava a caminho.
    """
    from app.services import conversation_service as conv_svc

    async def _run():
        async def _ciclo():
            await asyncio.sleep(0.2)          # o LLM "pensando"
            await cycle.svc._send_with_typing_guard(CHANNEL, cycle.phone, "resposta da IA")

        key = (CHANNEL, cycle.phone)
        task = asyncio.create_task(_ciclo())
        cycle.state.processing_tasks[key] = task
        await asyncio.sleep(0.05)

        conv = conversation_repo.get(cycle.conv["id"])
        await conv_svc.set_ai(cycle.deps, conv, 0, actor_id=42)  # "Atribuir a mim"

        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.CancelledError:
            pass  # abortado antes mesmo de chegar ao envio — também é sucesso

    asyncio.run(_run())
    assert cycle.outbound.sent == []


def test_cenario_da_conversa_12831(cycle):
    """Reprodução do caso real (§2.4): posse vence → ``set_ai(1)`` revincula o
    'roteador' → o ``agendamento_retorno`` chama ``assign`` → antes o gate NÃO
    calava (ai_active=1 + assignee + agente). Agora cala."""
    from app.services import conversation_service as conv_svc

    conversation_repo.assign_agent(
        cycle.conv["id"], assignee_user_id=None, active_agent_key="roteador", ai_active=1)
    assert _conversation_ai_active(cycle.contact) is True  # cliente escreve, IA responde

    conv = conversation_repo.get(cycle.conv["id"])
    asyncio.run(conv_svc.assign(cycle.deps, conv, 5))  # vencimento do retorno
    assert _conversation_ai_active(cycle.contact) is False


# ── F3·I9 — a IA da nota privada ────────────────────────────────────────────

def _poll(pred, timeout: float = 4.0, step: float = 0.02) -> bool:
    """Espera a task de fundo (``_run_private_ai``) rodar na loop do TestClient."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


def _private_ai_send(built, *, assignee, reply="resposta da IA privada",
                     take_over_after_first: bool = False,
                     ai_active: int | None = None,
                     tool_calls: list | None = None) -> list:
    """Nota privada com "IA lê" + "responder no chat" numa conversa atribuída.

    Devolve os envios que chegaram AO CLIENTE (o transporte é espionado no
    ``outbound_router``, o mesmo ponto por onde a resposta sairia de verdade).

    ``ai_active``/``tool_calls`` (plano 122) permitem montar o outro fechamento de
    portão: o que a PRÓPRIA IA faz ao chamar ``transfer_to_human`` — ``ai_active=0``
    **sem** dono humano, com a tool registrada no turno.
    """
    from tests.fakes import fake_agent_reply

    handler = built.agent_handler
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    row = contact_repo.get_or_create(phone)
    # ``_get_contact`` só materializa a conversa quando uma mensagem é gravada — o
    # gate precisa dela existindo ANTES, para poder ter dono.
    conversation_repo.resolve_for_contact(
        row["id"], f"{phone}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(row["id"])
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=assignee, active_agent_key=None,
        ai_active=(0 if assignee else 1) if ai_active is None else ai_active)

    sent: list = []
    router = built.app.state.deps.outbound_router
    original = router.send_text

    def _spy(channel_id, to, text, **kw):
        sent.append(text)
        result = original(channel_id, to, text, **kw)
        if take_over_after_first and len(sent) == 1:
            # A atribuição acontece DEPOIS da primeira ida ao wire e ANTES da
            # próxima iteração do split.
            conversation_repo.assign_agent(
                conv["id"], assignee_user_id=42,
                active_agent_key=None, ai_active=0)
        return result

    router.send_text = _spy
    try:
        with fake_agent_reply(reply, handler=handler, tool_calls=tool_calls):
            r = built.client.post(f"/api/contacts/{phone}/private-message", json={
                "text": "responde o cliente",
                "conversation_id": conv["id"],
                "ai_read": True,
                "ai_reply": True,
            })
            assert r.status_code == 200, r.text
            _poll(lambda: bool(sent), timeout=2.0)
            if take_over_after_first:
                # Espere a task alcançar o guard da segunda parte antes de
                # restaurar o transporte; só observar ``sent == [primeira]`` cedo
                # demais daria um falso verde.
                assert _poll(lambda: any(
                    m.get("role") == "system_notice"
                    and "foi interrompida" in (m.get("content") or "")
                    for m in message_repo.get_by_conversation(conv["id"], limit=20)
                ), timeout=2.0)
    finally:
        router.send_text = original
    return sent


def test_nota_privada_nao_fala_em_conversa_atribuida(build_app):
    """P1: bloquear o envio ao cliente. Era o único caminho de saída SEM gate
    nenhum — 3 dos 22 incidentes, com a resposta saindo 28s a 75s depois."""
    assert _private_ai_send(build_app(["gowa"]), assignee=42) == []


def test_nota_privada_continua_falando_em_conversa_livre(build_app):
    """Contraprova: sem dono humano, o comportamento é o de sempre."""
    assert _private_ai_send(build_app(["gowa"]), assignee=None) != []


def test_nota_privada_reconsulta_gate_entre_partes(build_app):
    """Assumir depois da primeira parte preserva a entregue e corta o restante."""
    sent = _private_ai_send(
        build_app(["gowa"]), assignee=None,
        reply='["primeira", "segunda"]', take_over_after_first=True)
    assert sent == ["primeira"]


# ── Plano 122 · a IA não pode calar a si mesma ──────────────────────────────
#
# O plano 96 endureceu o guard para o caso do HUMANO. Faltava o caso em que quem
# fecha o portão é a própria IA: ``transfer_to_human`` grava ``ai_active=0`` no
# meio do turno e o guard, ao reconsultar, descarta a despedida que aquele mesmo
# turno acabou de escrever (226 transferências mudas em produção desde 31/07).
#
# O discriminador é a ÉPOCA: toda tomada humana passa por ``abort_ai_cycle``, que
# a incrementa antes de qualquer outra coisa; ``transfer_to_human`` não a toca.


_TRANSFER_TOOL_CALL = [{"tool": "transfer_to_human", "args": {}, "result": "ok"}]


def _transfer_closes_the_gate(conv_id: int) -> None:
    """O que ``transfer_to_human`` escreve (agent/tools/transfer_to_human.py:96-98).

    ⚠️ NÃO confundir com ``_turn_ai_off``, que simula o painel: aqui
    ``assignee_user_id`` fica ``None`` — ninguém assumiu, a IA se auto-desligou.
    """
    conversation_repo.assign_agent(
        conv_id, assignee_user_id=None, active_agent_key=None, ai_active=0)


def _make_fake_handoff_run(reply: str):
    """Stub do motor AGNO que se comporta como ``transfer_to_human``.

    Fecha o gate da conversa DENTRO do turno (é o que a tool faz em
    transfer_to_human.py:96-98) e registra a chamada em ``executed_tools`` — sem
    esses dois efeitos juntos o teste não reproduz o bug, só o encena."""
    from agent.agno_engine import EngineResult
    from agent.execution import track_step

    async def _fake(handler, contact, sender, messages, active_tools,
                    model_config=None):
        track_step("llm_request", {"model": "fake/model", "engine": "agno",
                                   "context_messages": len(messages), "tools": []})
        conv = conversation_repo.get_open_for_contact(contact.id)
        if conv:
            _transfer_closes_the_gate(conv["id"])
        track_step("llm_response", {"model": "fake/model", "engine": "agno",
                                    "prompt_tokens": 10, "completion_tokens": 5,
                                    "has_tool_calls": True})
        return EngineResult(reply=reply, executed_tools=list(_TRANSFER_TOOL_CALL),
                            usage={"prompt_tokens": 10, "completion_tokens": 5,
                                   "total_tokens": 15})

    return _fake


def _run_handoff_turn(build_app, phone: str, reply: str):
    """Turno REAL pelo webhook: ingest → LLM → tool → guard → envio → save.

    Ao contrário dos testes de seam acima, este exercita o CABEAMENTO (o call site
    que deriva ``allow_self_handoff`` de ``result.tool_calls``) — é o que ficaria
    verde com o guard consertado e o call site esquecido."""
    from unittest.mock import patch

    from agent import agent_factory
    from ai_engine import dynamic_registry

    agent_factory.seed_default_agent()
    dynamic_registry.invalidate()
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "split_messages": True, "split_message_delay": 0,
        "response_delay_min": 0, "response_delay_max": 0,
    })
    try:
        from agent import agno_engine
        with patch.object(agno_engine, "run_async",
                          side_effect=_make_fake_handoff_run(reply)):
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": f"p122_{phone}",
                    "body": "quero falar com um atendente",
                    "from_name": "Cliente"}})
            assert r.status_code == 200, r.text

            async def _drain():
                for _ in range(6):
                    task = built.app.state.deps.state.processing_tasks.get(
                        ("default", phone))
                    if task is None:
                        break
                    try:
                        await asyncio.wait_for(task, timeout=5.0)
                    except Exception:
                        pass
                    await asyncio.sleep(0)

            built.client.portal.call(_drain)
    finally:
        built.settings.set("auto_reply", False)

    row = contact_repo.get_by_phone(phone)
    assert row, "contato não materializado pelo turno"
    conv = conversation_repo.get_open_for_contact(row["id"])
    return row, conv


# ── Lado A · a despedida PASSA (inverte a caracterização da F0) ─────────────

def test_despedida_da_transferencia_chega_ao_cliente(cycle):
    """A F0 assegurava o OPOSTO: o guard descartava a despedida inteira.

    Quem fechou o gate foi este turno; a época está intacta, então não houve
    tomada humana nenhuma para respeitar."""
    _transfer_closes_the_gate(cycle.conv["id"])
    sent = asyncio.run(cycle.svc._send_with_typing_guard(
        CHANNEL, cycle.phone, "já vou te transferir para um atendente",
        allow_self_handoff=True))
    assert sent is True
    assert cycle.outbound.sent == ["já vou te transferir para um atendente"]


def test_despedida_atravessa_o_split_inteiro(cycle):
    """A F0 assegurava o OPOSTO: zero partes entregues.

    Trava o item 4 da F1 — sem o perdão em ``send_reply`` a despedida morreria na
    parte 1/N, e ``split_messages`` é default ``True``: o conserto ficaria pela
    metade sem nenhum teste reclamando."""
    _transfer_closes_the_gate(cycle.conv["id"])
    sent = asyncio.run(cycle.svc.send_reply(
        CHANNEL, cycle.phone, '["um", "dois", "três"]', allow_self_handoff=True))
    assert sent is True
    assert cycle.outbound.sent == ["um", "dois", "três"]


def test_ia_privada_entrega_a_despedida(build_app):
    """A F0 assegurava o OPOSTO: o terceiro caminho de saída também engolia a
    despedida — e ainda gravava um card dizendo que "um atendente assumiu"."""
    assert _private_ai_send(
        build_app(["gowa"]), assignee=None, ai_active=0,
        tool_calls=_TRANSFER_TOOL_CALL) == ["resposta da IA privada"]


def test_despedida_e_salva_e_nao_vira_takeover(build_app):
    """Turno completo pelo webhook — o teste que cobre o CABEAMENTO.

    Três asserções que o fio de produção exige juntas: a despedida (a) foi
    persistida como ``assistant`` (o save só roda para ``sent_parts``, então isso
    prova a ida ao wire), (b) NÃO produziu o card "A IA assumiu a conversa"
    (D4 — seria absurdo logo depois de "SISTEMA pausou a IA") e (c) o card de
    pausa continua no fio."""
    from server import system_notices

    phone = "5511975122001"
    row, conv = _run_handoff_turn(build_app, phone, "já vou te transferir!")

    replies = [m for m in message_repo.get_all(row["id"]) if m["role"] == "assistant"]
    assert [m["content"] for m in replies] == ["já vou te transferir!"]
    assert replies[0]["status"] == "sent"

    assert system_notices.has_event(conv["id"], "ai_takeover") is False
    assert system_notices.has_event(conv["id"], "ai_off") is True


# ── Lado B · o perdão NÃO vaza (o plano 96 continua de pé) ──────────────────

def test_perdao_nao_sobrevive_a_atribuicao_humana(cycle):
    """⭐ O teste central do D2 — o que impede este plano de desfazer o 96.

    Turno que transferiu (perdão LIGADO) **e** operador que assumiu no meio
    (época+1) ⇒ nada sai. A época é checada ANTES do perdão em
    ``_cycle_may_continue``; inverter as duas linhas faz este teste ficar
    vermelho, que é exatamente o ponto."""
    epoch = cycle.svc._abort_epoch(CHANNEL, cycle.phone)
    _transfer_closes_the_gate(cycle.conv["id"])
    abort_ai_cycle(cycle.deps, CHANNEL, cycle.phone)  # o humano assumiu
    sent = asyncio.run(cycle.svc._send_with_typing_guard(
        CHANNEL, cycle.phone, "despedida", abort_epoch=epoch,
        allow_self_handoff=True))
    assert sent is False
    assert cycle.outbound.sent == []


def test_perdao_nao_vaza_para_o_turno_seguinte(cycle):
    """D3: o perdão é parâmetro de chamada e morre no ``return``.

    Turno 1 transfere e se despede; turno 2 (sem transferência, gate ainda
    fechado) fica calado — senão a IA voltaria a falar numa conversa já entregue
    a um humano."""
    _transfer_closes_the_gate(cycle.conv["id"])
    primeiro = asyncio.run(cycle.svc._send_with_typing_guard(
        CHANNEL, cycle.phone, "já vou te transferir", allow_self_handoff=True))
    segundo = asyncio.run(cycle.svc._send_with_typing_guard(
        CHANNEL, cycle.phone, "ah, e mais uma coisa"))
    assert (primeiro, segundo) == (True, False)
    assert cycle.outbound.sent == ["já vou te transferir"]


def test_tool_pulada_por_filtro_nao_ganha_perdao(cycle):
    """``skipped=True`` ⇒ a tool NÃO rodou ⇒ o gate não foi fechado por este turno.

    Trava o ``not tc.get('skipped')`` do predicado: sem ele, um ``filter.tool.args``
    que aborte a transferência daria à IA uma licença para falar numa conversa que
    outra coisa qualquer desligou."""
    from app.services.messaging_service import _turn_handed_off

    assert _turn_handed_off([{"tool": "transfer_to_human"}]) is True
    assert _turn_handed_off([{"tool": "transfer_to_human", "skipped": True}]) is False
    assert _turn_handed_off([{"tool": "transferir_agente"}]) is False
    assert _turn_handed_off(None) is False

    _transfer_closes_the_gate(cycle.conv["id"])
    sent = asyncio.run(cycle.svc._send_with_typing_guard(
        CHANNEL, cycle.phone, "não deveria sair",
        allow_self_handoff=_turn_handed_off(
            [{"tool": "transfer_to_human", "skipped": True}])))
    assert sent is False
    assert cycle.outbound.sent == []


def test_envio_do_operador_durante_o_turno_continua_cortando(cycle):
    """``_operator_took_over`` (contacts.py:350-362) chama ``abort_ai_cycle``.

    Enviar como atendente não desliga a IA da conversa — só invalida a geração.
    Com ou sem perdão, a resposta em voo é descartada."""
    epoch = cycle.svc._abort_epoch(CHANNEL, cycle.phone)
    abort_ai_cycle(cycle.deps, CHANNEL, cycle.phone)  # o que a rota de envio faz
    for perdao in (False, True):
        sent = asyncio.run(cycle.svc._send_with_typing_guard(
            CHANNEL, cycle.phone, "resposta obsoleta", abort_epoch=epoch,
            allow_self_handoff=perdao))
        assert sent is False
    assert cycle.outbound.sent == []
