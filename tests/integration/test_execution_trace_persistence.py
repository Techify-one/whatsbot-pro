"""Plano 51 · sub-plano 01 · Fase 4 — módulo compartilhado ``execution_trace``.

Fixtures de execução com/sem ``execution_id`` e com/sem ``llm_context``:
link preciso quando presente, fuzzy quando ausente; ``exact_context`` devolve
o capturado (um por hop) e ``None`` senão; ``agent_chain`` reconstrói
roteador→spoke de ``routing_steps``; o bundle sinaliza exato vs aproximado.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import update as sa_update

from app.services import execution_trace
from db.engine import get_engine
from db.repositories import contact_repo, execution_repo, message_repo
from db.tables import executions


def _finish(exec_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == exec_id)
                     .values(status="completed", completed_at=time.time()))


def _routing(exec_id: int, steps: list[dict]) -> None:
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == exec_id)
                     .values(routing_steps=json.dumps(steps), agent_key=steps[-1]["to"]))


def test_linked_message_resolves_without_fuzzy(_engine_ready):
    phone = "5511975520001"
    eid = execution_repo.create(phone)
    _finish(eid)
    contact = contact_repo.get_or_create(phone, "Trace")
    row = message_repo.add(contact["id"], "assistant", "resposta",
                           status="sent", msg_id="TR1", agent_key="default",
                           execution_id=eid)

    bundle = execution_trace.reconstruct_for_message(row, phone=phone)
    assert bundle["linked"] is True
    assert bundle["execution"]["id"] == eid
    # Sem captura de contexto → aproximado, com o approx preenchido.
    assert bundle["exact"] is False
    assert bundle["contexts"] is None
    assert bundle["approx"] is not None


def test_legacy_message_falls_back_to_fuzzy(_engine_ready):
    phone = "5511975520002"
    eid = execution_repo.create(phone)
    _finish(eid)
    contact = contact_repo.get_or_create(phone, "Legacy")
    row = message_repo.add(contact["id"], "assistant", "resposta antiga",
                           status="sent", msg_id="TR2")  # sem execution_id

    found = execution_trace.find_execution_for_message(row, phone=phone)
    assert found and found["id"] == eid  # via janela de ts (DL2)
    bundle = execution_trace.reconstruct_for_message(row, phone=phone)
    assert bundle["linked"] is False
    assert bundle["execution"]["id"] == eid


def test_exact_context_reads_llm_context_per_hop(_engine_ready):
    phone = "5511975520003"
    eid = execution_repo.create(phone)
    execution_repo.add_step(eid, "llm_context",
                            {"model": "m1", "messages": [
                                {"role": "system", "content": "prompt roteador"}]},
                            agent_key="roteador")
    execution_repo.add_step(eid, "tool_executed",
                            {"tool": "transferir_agente",
                             "args": {"destino": "comercial"}},
                            agent_key="roteador")
    execution_repo.add_step(eid, "llm_context",
                            {"model": "m2", "messages": [
                                {"role": "system", "content": "prompt comercial"}]},
                            agent_key="comercial")
    _routing(eid, [{"from": "roteador", "to": "comercial", "depth": 1,
                    "reason": "quer comprar"}])
    _finish(eid)

    execution = execution_repo.get_by_id(eid)
    assert execution_trace.agent_chain(execution) == ["roteador", "comercial"]
    used = execution_trace.tools_used(execution)
    assert used == [{"tool": "transferir_agente",
                     "args": {"destino": "comercial"},
                     "result": None, "agent_key": "roteador"}]
    hops = execution_trace.exact_context(execution)
    assert [h["agent_key"] for h in hops] == ["roteador", "comercial"]
    assert hops[0]["messages"][0]["content"] == "prompt roteador"


def test_single_agent_chain_from_agent_key(_engine_ready):
    phone = "5511975520004"
    eid = execution_repo.create(phone)
    with get_engine().begin() as conn:
        conn.execute(sa_update(executions).where(executions.c.id == eid)
                     .values(agent_key="default"))
    _finish(eid)
    execution = execution_repo.get_by_id(eid)
    assert execution_trace.agent_chain(execution) == ["default"]


def test_no_execution_degrades_to_empty_bundle(_engine_ready):
    bundle = execution_trace.reconstruct_for_message(
        {"_id": 999999, "ts": 1.0, "msg_id": "NOPE"}, phone="5500000000000")
    assert bundle["execution"] is None
    assert bundle["linked"] is False
    assert bundle["agent_chain"] == []
    assert bundle["tools_used"] == []
    assert bundle["exact"] is False
