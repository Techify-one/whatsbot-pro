"""Plano 164 · F2 — ``aensure_execution``: garante execução sem duplicar nem
vazar o ContextVar.

Cobre os 4 casos do plano: abre+fecha; não aninha (uma já aberta é reusada e
NÃO fechada por quem não abriu); status ``failed``/error="cancelled" em
``CancelledError``; status ``failed`` com a mensagem da exceção; e o
ContextVar volta a ``None`` depois em todos os casos.

Rodar: venv/bin/python -m pytest tests/integration/test_ensure_execution.py -q
"""

from __future__ import annotations

import asyncio

import pytest

from agent.execution import get_current_execution_id, set_current_execution
from db.repositories import execution_repo
from server.execution import aensure_execution

PHONE = "5511930000041"


def test_opens_and_closes_as_completed(_engine_ready):
    async def _run():
        async with aensure_execution(PHONE, "ai_turn") as (exec_id, opened_here):
            assert exec_id is not None
            assert opened_here is True
            assert get_current_execution_id() == exec_id
        return exec_id

    exec_id = asyncio.run(_run())
    row = execution_repo.get_by_id(exec_id)
    assert row["status"] == "completed"
    assert row["trigger_type"] == "ai_turn"
    assert get_current_execution_id() is None


def test_does_not_nest_inside_an_open_execution(_engine_ready):
    async def _run():
        async with aensure_execution(PHONE, "webhook") as (outer_id, outer_opened):
            assert outer_opened is True
            async with aensure_execution(PHONE, "ai_turn") as (inner_id, inner_opened):
                assert inner_id == outer_id, "reaproveita a execução já aberta"
                assert inner_opened is False
            # A execução externa continua ABERTA — quem não abriu não fecha.
            assert get_current_execution_id() == outer_id
            row = execution_repo.get_by_id(outer_id)
            assert row["status"] == "running"
        return outer_id

    outer_id = asyncio.run(_run())
    assert execution_repo.get_by_id(outer_id)["status"] == "completed"
    assert get_current_execution_id() is None


def test_cancelled_error_marks_failed_and_reraises(_engine_ready):
    captured = {}

    async def _run():
        with pytest.raises(asyncio.CancelledError):
            async with aensure_execution(PHONE, "private_note") as (exec_id, opened_here):
                captured["exec_id"] = exec_id
                raise asyncio.CancelledError()

    asyncio.run(_run())
    row = execution_repo.get_by_id(captured["exec_id"])
    assert row["status"] == "failed"
    assert row["error"] == "cancelled"
    assert get_current_execution_id() is None


def test_other_exception_marks_failed_with_message_and_reraises(_engine_ready):
    captured = {}

    async def _run():
        with pytest.raises(ValueError, match="boom"):
            async with aensure_execution(PHONE, "ai_turn") as (exec_id, opened_here):
                captured["exec_id"] = exec_id
                raise ValueError("boom")

    asyncio.run(_run())
    row = execution_repo.get_by_id(captured["exec_id"])
    assert row["status"] == "failed"
    assert row["error"] == "boom"
    assert get_current_execution_id() is None


def test_open_failure_fails_open_without_raising(_engine_ready, monkeypatch):
    """Banco fora ao ABRIR não pode derrubar o turno — yield (None, False)."""
    from server import execution as execution_module

    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(execution_module, "astart_execution", _boom)

    async def _run():
        async with aensure_execution(PHONE, "ai_turn") as (exec_id, opened_here):
            assert exec_id is None
            assert opened_here is False

    asyncio.run(_run())
    assert get_current_execution_id() is None


@pytest.fixture(autouse=True)
def _reset_execution_contextvar():
    set_current_execution(None)
    yield
    set_current_execution(None)
