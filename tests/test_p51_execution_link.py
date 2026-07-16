"""Plano 51 · sub-plano 01 · Fase 1 — link preciso ``messages.execution_id``.

Parte A (caracterização, ANTES da mudança): um turno de IA com split em 2 partes
grava DUAS rows ``assistant`` com ``agent_key``/``msg_id``/``status='sent'`` —
trava o fluxo crítico de save de resposta (``messaging_service`` →
``save_assistant_message`` → ``add_message`` → ``message_repo.add``).

Parte B (após a mudança): as mesmas duas rows carregam o MESMO ``execution_id``
não-nulo apontando para a execução do turno, e ``find_execution_for_message``
resolve via link (O(1)) sem cair no fuzzy.

O LLM é stubbado no seam ``agno_engine.run_async`` (mesmo corte da
caracterização de execuções — o handler/writer reais continuam rodando).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from db.repositories import contact_repo, execution_repo, message_repo


def _drain_orchestrator(built, channel_id: str, phone: str) -> None:
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


def _seed_default_agent() -> None:
    from agent import agent_factory
    from ai_engine import dynamic_registry

    agent_factory.seed_default_agent()
    dynamic_registry.invalidate()


def _make_fake_run_async(reply: str):
    from agent.execution import track_step
    from agent.agno_engine import EngineResult

    async def _fake(handler, contact, sender, messages, active_tools,
                    model_config=None):
        model_id = (model_config or {}).get("model") or "fake/model"
        track_step("llm_request", {"model": model_id, "engine": "agno",
                                   "context_messages": len(messages), "tools": []})
        track_step("llm_response", {"model": model_id, "engine": "agno",
                                    "prompt_tokens": 10, "completion_tokens": 5,
                                    "has_tool_calls": False})
        return EngineResult(reply=reply, executed_tools=[],
                            usage={"prompt_tokens": 10, "completion_tokens": 5,
                                   "total_tokens": 15})

    return _fake


def _run_split_turn(build_app, phone: str) -> list[dict]:
    """Fire one AI-replied webhook turn with split ON ('["a","b"]' reply) and
    return the persisted assistant rows (oldest-first)."""
    _seed_default_agent()
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "split_messages": True, "split_message_delay": 0,
        "response_delay_min": 0, "response_delay_max": 0,
    })
    fake = _make_fake_run_async('["parte um da resposta", "parte dois da resposta"]')
    try:
        from agent import agno_engine
        with patch.object(agno_engine, "run_async", side_effect=fake):
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": f"p51_{phone}",
                    "body": "oi, me responde em duas partes",
                    "from_name": "Cliente"}})
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
    finally:
        built.settings.set("auto_reply", False)

    contact = contact_repo.get_by_phone(phone)
    assert contact, "contato não materializado pelo turno"
    rows = [m for m in message_repo.get_all(contact["id"])
            if m["role"] == "assistant"]
    assert len(rows) == 2, f"esperava 2 partes salvas, veio {len(rows)}"
    return rows


# ── Parte A — caracterização do save de resposta (split em 2 partes) ─────────

def test_split_reply_saves_two_assistant_rows(build_app):
    rows = _run_split_turn(build_app, "5511975510001")
    assert rows[0]["content"] == "parte um da resposta"
    assert rows[1]["content"] == "parte dois da resposta"
    for row in rows:
        assert row["status"] == "sent"
        assert row["msg_id"] == "FAKE_SENT"
        assert row["agent_key"] == "default"


# ── Parte B — o link preciso (após a Fase 1) ─────────────────────────────────

def test_split_reply_parts_share_execution_id(build_app):
    phone = "5511975510002"
    rows = _run_split_turn(build_app, phone)
    exec_ids = {row.get("execution_id") for row in rows}
    assert len(exec_ids) == 1, f"partes com execution_id divergente: {exec_ids}"
    exec_id = exec_ids.pop()
    assert exec_id is not None, "execution_id não estampado no save da resposta"

    execution = execution_repo.get_by_id(exec_id)
    assert execution and execution["phone"] == phone

    # O helper O(1) resolve via link, sem janela de ts.
    found = message_repo.find_execution_for_message(rows[0])
    assert found and found["id"] == exec_id


def test_find_execution_falls_back_to_none_for_legacy_rows(build_app):
    """Linha legada (execution_id NULL) não quebra: o helper devolve None e o
    consumidor (plugin) cai no fuzzy dele — DL2."""
    built = build_app(["gowa"])  # noqa: F841 — garante app/DB de pé
    contact = contact_repo.get_or_create("5511975510003", "Legacy")
    legacy = message_repo.add(contact["id"], "assistant", "resposta antiga",
                              status="sent", msg_id="LEGACY_1")
    assert legacy.get("execution_id") is None
    assert message_repo.find_execution_for_message(legacy) is None
