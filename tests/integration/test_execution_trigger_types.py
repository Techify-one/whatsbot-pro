"""Plano 164 · F3/F4 — execução registrada fora do ciclo do webhook.

Caso real: conversa 17028 em produção (2026-09-16), uma nota privada disparou
um turno comercial→roteador→fechamento sem NENHUMA linha em
``executions``/``execution_steps`` — o webhook e o sandbox eram os únicos
gatilhos que abriam execução.

F3: a IA da nota privada ("IA lê") passa a rodar dentro de uma execução
``private_note`` completa (canal, conversa, texto de entrada/saída, passos por
agente, link ``execution_id`` na resposta salva).

F4: ``run_turn`` (chamado por QUALQUER plugin via ``aprocess_message``, sem uma
execução já aberta — ``retornos``/``agendamento_retorno``) ganha uma rede de
segurança ``ai_turn``. O webhook e a nota privada já abrem a própria execução
ANTES de chegar em ``run_turn``, então continuam com EXATAMENTE 1 execução por
ciclo — a rede não pode aninhar (``aensure_execution`` não abre 2ª vez).

O LLM é stubado em ``agno_engine.run_async`` (mesmo corte de
test_execution_message_link.py / test_routing_reason.py) para exercitar o
``run_turn`` REAL — ao contrário de ``tests.fakes.fake_agent_reply``, que
stuba ``aprocess_message`` inteiro e pularia o próprio código sob teste.

Rodar: venv/bin/python -m pytest tests/integration/test_execution_trigger_types.py -q
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import patch

from agent import agno_engine, agent_factory
from agent.agno_engine import EngineResult
from agent.execution import track_step
from ai_engine import dynamic_registry
from db.repositories import execution_repo, message_repo


def _seed_default_agent() -> None:
    agent_factory.seed_default_agent()
    dynamic_registry.invalidate()


def _poll(pred, timeout: float = 4.0, step: float = 0.02) -> bool:
    """Espera a task de fundo (``_run_private_ai``) rodar na loop do TestClient."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


def _fake_run_async(reply: str):
    async def _fake(handler, contact, sender, messages, active_tools, model_config=None):
        track_step("llm_request", {"model": "fake/model", "engine": "agno",
                                   "context_messages": len(messages), "tools": []})
        track_step("llm_response", {"model": "fake/model", "engine": "agno",
                                    "prompt_tokens": 10, "completion_tokens": 5,
                                    "has_tool_calls": False})
        return EngineResult(reply=reply, executed_tools=[],
                            usage={"prompt_tokens": 10, "completion_tokens": 5,
                                   "total_tokens": 15})
    return _fake


def _rand_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def test_aprocess_message_sem_execucao_aberta_gera_ai_turn(build_app):
    """F4a: plugin que chama ``aprocess_message`` DIRETO (sem webhook/nota
    privada em volta, como ``retornos``/``agendamento_retorno``) — antes rodava
    com o ContextVar vazio e ``track_step`` virava no-op; a rede de segurança
    agora abre uma execução ``ai_turn`` completa, com passos."""
    _seed_default_agent()
    built = build_app(["gowa"])
    phone = _rand_phone()

    with patch.object(agno_engine, "run_async",
                      side_effect=_fake_run_async("oi, tudo bem?")):
        result = built.client.portal.call(
            built.agent_handler.aprocess_message, phone, "oi")

    assert result.reply == "oi, tudo bem?"
    execs = execution_repo.list_executions(phone=phone)
    assert len(execs) == 1, f"esperava 1 execução, veio {len(execs)}"
    assert execs[0]["trigger_type"] == "ai_turn"
    assert execs[0]["status"] == "completed"
    full = execution_repo.get_by_id(execs[0]["id"])
    assert full["steps"], "a rede de segurança precisa registrar passos (llm_request/response)"
    assert full["input_text"] == "oi"


def test_webhook_continua_com_exatamente_uma_execucao(build_app):
    """F4b, guarda-costas do F0 item 4: a rede de ``run_turn`` NÃO pode
    aninhar dentro da execução ``webhook`` que o ciclo já abriu."""
    _seed_default_agent()
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "response_delay_min": 0, "response_delay_max": 0,
    })
    phone = _rand_phone()
    try:
        with patch.object(agno_engine, "run_async",
                          side_effect=_fake_run_async("resposta única")):
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": f"p164_{phone}",
                    "body": "oi", "from_name": "Cliente"}})
            assert r.status_code == 200, r.text

            async def _drain():
                import asyncio as _a
                for _ in range(6):
                    task = built.app.state.deps.state.processing_tasks.get(("default", phone))
                    if task is None:
                        break
                    try:
                        await _a.wait_for(task, timeout=5.0)
                    except Exception:
                        pass
                    await _a.sleep(0)
            built.client.portal.call(_drain)
    finally:
        built.settings.set("auto_reply", False)

    execs = execution_repo.list_executions(phone=phone)
    assert len(execs) == 1, f"esperava exatamente 1 execução, veio {len(execs)}"
    assert execs[0]["trigger_type"] == "webhook"
    assert execs[0]["status"] == "completed"


def test_nota_privada_gera_execucao_private_note_com_link_na_resposta(build_app):
    """F3: 1 execução ``private_note`` (não ``ai_turn`` — a rede de F4 vira
    no-op porque já há uma execução aberta), com canal/conversa/textos e a
    resposta salva linkada por ``execution_id``."""
    from db.repositories import contact_repo, conversation_repo

    _seed_default_agent()
    built = build_app(["gowa"])
    phone = _rand_phone()
    row = contact_repo.get_or_create(phone)
    conversation_repo.resolve_for_contact(
        row["id"], f"{phone}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(row["id"])
    # Conversa LIVRE explícita (mesmo padrão de test_human_assignment_ai_gate.py):
    # sem isso o gate por-conversa pode achar "atendente no comando" e bloquear
    # a resposta ao cliente antes mesmo de chegar no que este teste cobre.
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=None, active_agent_key=None, ai_active=1)

    router = built.app.state.deps.outbound_router
    sent: list = []
    original = router.send_text

    def _spy(channel_id, to, text, **kw):
        sent.append(text)
        return original(channel_id, to, text, **kw)

    router.send_text = _spy
    try:
        with patch.object(agno_engine, "run_async",
                          side_effect=_fake_run_async("resposta da IA privada")):
            r = built.client.post(f"/api/contacts/{phone}/private-message", json={
                "text": "responde o cliente",
                "conversation_id": conv["id"],
                "ai_read": True,
                "ai_reply": True,
            })
            assert r.status_code == 200, r.text
            assert _poll(lambda: bool(sent), timeout=2.0), "a IA privada não respondeu a tempo"
            # ``sent`` só marca o envio ao wire — o save da resposta, o
            # ``response_sent``/``output_text`` e o fechamento da execução (fora
            # do ``async with aensure_execution``) ainda rodam DEPOIS, na mesma
            # task de fundo. Espera a execução sair de "running" antes de ler.
            assert _poll(lambda: any(
                e["status"] != "running"
                for e in execution_repo.list_executions(phone=phone)
            ), timeout=2.0), "a execução não fechou a tempo"
    finally:
        router.send_text = original

    execs = execution_repo.list_executions(phone=phone)
    assert len(execs) == 1, (
        f"esperava exatamente 1 execução (private_note, sem aninhar ai_turn), "
        f"veio {[e['trigger_type'] for e in execs]}")
    execution = execs[0]
    assert execution["trigger_type"] == "private_note"
    assert execution["status"] == "completed"
    assert execution["conversation_id"] == conv["id"]
    assert execution["channel_id"] == "default"
    assert execution["input_text"] == "responde o cliente"
    assert execution["output_text"] == "resposta da IA privada"

    assistant_rows = [m for m in message_repo.get_all(row["id"]) if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["execution_id"] == execution["id"]
