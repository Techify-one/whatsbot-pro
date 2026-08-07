"""Plano 36 — tela de logs de Execução: filtros novos + persistência + poda.

Cobre as frentes de backend do plano 36 contra o Postgres de teste (pytest-collected
em ``tests/endpoints``, engine process-global via ``_engine_ready``):

* **F4 filtros** — ``list_executions``/``count`` por ``conversation_id`` e janela de
  data; o endpoint ``GET /api/executions`` repassa os params (e ``YYYY-MM-DD``
  inválido dropa o filtro em vez de 500).
* **F2 tool result** — o entrypoint real de tool grava ``tool_executed.data.result``
  (truncado) além dos ``args``.
* **F3 contexto** — ``_capture_llm_context`` grava ``llm_context`` (system + msgs,
  base64 removido, truncado) com o kill-switch ON; nada com OFF.
* **F3 poda** — ``prune_executions`` remove por quantidade e por dias, levando os
  ``execution_steps`` junto (FK CASCADE).
"""

from __future__ import annotations

import sqlalchemy as sa

import agent.agno_engine as engine
from agent.execution import prune_executions, set_current_execution
from db.engine import get_engine
from db.repositories import config_repo, execution_repo
from db.tables import executions
from server.routes.executions import _parse_date


# ── helpers ─────────────────────────────────────────────────────────────────

def _mk_exec(*, conversation_id=None, started_at=None, phone="5500000036",
             channel_id=None, channel_label=None) -> int:
    eid = execution_repo.create(
        phone, "webhook", conversation_id=conversation_id,
        channel_id=channel_id, channel_label=channel_label,
    )
    if started_at is not None:
        with get_engine().begin() as conn:
            conn.execute(sa.update(executions)
                         .where(executions.c.id == eid)
                         .values(started_at=started_at))
    return eid


class _FakeHandler:
    """Minimal handler whose _dispatch_tool returns an oversized result string."""

    def __init__(self, result):
        self._result = result

    def _dispatch_tool(self, contact, name, args):
        return self._result


# ── F1: create/set_channel round-trip ───────────────────────────────────────

def test_create_and_set_channel(_engine_ready):
    eid = _mk_exec(conversation_id=36001, channel_id="gowa", channel_label="WhatsApp")
    row = execution_repo.get_by_id(eid)
    assert row["conversation_id"] == 36001
    assert row["channel_id"] == "gowa"
    assert row["channel_label"] == "WhatsApp"

    execution_repo.set_channel(eid, conversation_id=36002, channel_label="Telegram")
    row = execution_repo.get_by_id(eid)
    assert row["conversation_id"] == 36002
    assert row["channel_label"] == "Telegram"
    assert row["channel_id"] == "gowa"  # não sobrescrito (partial update)


# ── F4: repo filters ─────────────────────────────────────────────────────────

def test_filter_by_conversation(_engine_ready):
    a = _mk_exec(conversation_id=36101)
    b = _mk_exec(conversation_id=36101)
    c = _mk_exec(conversation_id=36102)
    got = {r["id"] for r in execution_repo.list_executions(conversation_id=36101, limit=1000)
           if r["id"] in {a, b, c}}
    assert got == {a, b}
    assert execution_repo.count(conversation_id=36101) >= 2
    assert execution_repo.count(conversation_id=36102) >= 1


def test_filter_by_date_window(_engine_ready):
    day = _parse_date("2026-06-15")
    inside = _mk_exec(conversation_id=36201, started_at=day + 3600)       # 2026-06-15 01:00
    outside = _mk_exec(conversation_id=36201, started_at=day + 3 * 86400)  # 2026-06-18
    df = _parse_date("2026-06-15")
    dt = _parse_date("2026-06-15", end_of_day=True)
    got = {r["id"] for r in execution_repo.list_executions(date_from=df, date_to=dt, limit=1000)
           if r["id"] in {inside, outside}}
    assert got == {inside}


def test_parse_date_variants(_engine_ready):
    assert isinstance(_parse_date("2026-07-01"), float)
    assert _parse_date("") is None
    assert _parse_date(None) is None
    assert _parse_date("garbage") is None
    df = _parse_date("2026-07-01")
    dt = _parse_date("2026-07-01", end_of_day=True)
    assert dt > df and (dt - df) > 86000  # fim-de-dia inclusivo


# ── F4: endpoint layer ───────────────────────────────────────────────────────

def test_endpoint_filter_by_conversation(client):
    a = _mk_exec(conversation_id=36301)
    _mk_exec(conversation_id=36302)
    r = client.get("/api/executions?conversation_id=36301&limit=1000")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    items = body["data"]["items"]
    assert a in [it["id"] for it in items]
    assert all(it["conversation_id"] == 36301 for it in items)


def test_endpoint_bad_date_is_dropped_not_500(client):
    r = client.get("/api/executions?date_from=not-a-date")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── F2: tool result persisted (drives the real sync entrypoint) ──────────────

def test_tool_result_persisted_truncated(_engine_ready):
    big = "R" * (engine.TOOL_RESULT_MAX_CHARS + 500)
    eid = _mk_exec()
    set_current_execution(eid)
    try:
        schema = {"type": "function", "function": {
            "name": "busca_36", "description": "d",
            "parameters": {"type": "object", "properties": {}}}}
        executed: list = []
        fns = engine.build_functions(_FakeHandler(big), object(), "5500000036",
                                     [schema], executed, is_async=False)
        out = fns["busca_36"].entrypoint()
    finally:
        set_current_execution(None)

    assert out == big  # o LLM recebe o feedback completo (não truncado)
    row = execution_repo.get_by_id(eid)
    steps = [s for s in row["steps"] if s["step_type"] == "tool_executed"]
    assert len(steps) == 1
    data = steps[0]["data"]
    assert data["tool"] == "busca_36"
    assert data["result"].endswith("… (truncado)")          # persistido truncado
    assert len(data["result"]) <= engine.TOOL_RESULT_MAX_CHARS + 20


def test_tool_without_return_does_not_break(_engine_ready):
    eid = _mk_exec()
    set_current_execution(eid)
    try:
        schema = {"type": "function", "function": {
            "name": "vazia_36", "description": "d",
            "parameters": {"type": "object", "properties": {}}}}
        fns = engine.build_functions(_FakeHandler(None), object(), "5500000036",
                                     [schema], [], is_async=False)
        fns["vazia_36"].entrypoint()
    finally:
        set_current_execution(None)
    row = execution_repo.get_by_id(eid)
    steps = [s for s in row["steps"] if s["step_type"] == "tool_executed"]
    assert len(steps) == 1
    assert steps[0]["data"].get("result") is None


# ── F3: context capture kill-switch ──────────────────────────────────────────

class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


def test_llm_context_captured_when_on(_engine_ready):
    config_repo.set("execution_capture_context", True)
    try:
        eid = _mk_exec()
        set_current_execution(eid)
        convo = [
            _Msg("user", "olá tudo bem"),
            _Msg("assistant", "oi!"),
            _Msg("user", "data:audio/ogg;base64," + "Z" * 400),  # base64 → removido
        ]
        engine._capture_llm_context("você é um bot", convo, "deepseek/x")
        set_current_execution(None)

        row = execution_repo.get_by_id(eid)
        ctx = [s for s in row["steps"] if s["step_type"] == "llm_context"]
        assert len(ctx) == 1
        msgs = ctx[0]["data"]["messages"]
        assert msgs[0]["role"] == "system"
        assert len(msgs) == 4  # system + 3
        assert "[base64 removido]" in msgs[3]["content"]
    finally:
        config_repo.set("execution_capture_context", False)


def test_llm_context_not_captured_when_off(_engine_ready):
    config_repo.set("execution_capture_context", False)
    eid = _mk_exec()
    set_current_execution(eid)
    engine._capture_llm_context("sys", [_Msg("user", "x")], "m")
    set_current_execution(None)
    row = execution_repo.get_by_id(eid)
    assert not any(s["step_type"] == "llm_context" for s in row["steps"])


def test_llm_context_truncates_long_message(_engine_ready):
    config_repo.set("execution_capture_context", True)
    try:
        eid = _mk_exec()
        set_current_execution(eid)
        long_text = "palavra " * 400  # ~3200 chars, com espaços (não é base64)
        engine._capture_llm_context(None, [_Msg("user", long_text)], "m")
        set_current_execution(None)
        row = execution_repo.get_by_id(eid)
        ctx = [s for s in row["steps"] if s["step_type"] == "llm_context"][0]
        msg = ctx["data"]["messages"][0]
        assert msg["truncated"] is True
        assert msg["content"].endswith("… (truncado)")
    finally:
        config_repo.set("execution_capture_context", False)


# ── F3: pruning (count + days, CASCADE) ──────────────────────────────────────

def test_prune_by_days_cascades_steps(_engine_ready):
    day = _parse_date("2026-05-01")
    old = _mk_exec(started_at=day)           # bem antigo
    recent = _mk_exec()                       # agora
    set_current_execution(old)
    from agent.execution import track_step
    track_step("llm_request", {"model": "m"})
    set_current_execution(None)

    # retenção de 1 dia remove o antigo (e seus steps por CASCADE), mantém o recente.
    prune_executions(1_000_000, retention_days=1)
    assert execution_repo.get_by_id(old) is None
    assert execution_repo.get_by_id(recent) is not None
    # steps do antigo sumiram junto (a execução não existe mais).
    with get_engine().connect() as conn:
        from db.tables import execution_steps
        left = conn.execute(
            sa.select(sa.func.count())
            .select_from(execution_steps)
            .where(execution_steps.c.execution_id == old)
        ).scalar()
    assert left == 0


def test_prune_by_count_keeps_newest(_engine_ready):
    ids = [_mk_exec(phone="5500000099") for _ in range(5)]
    # manter só as 2 mais novas ENTRE as nossas seria frágil (a tabela é compartilhada);
    # em vez disso confirmamos que prune por quantidade não derruba as recentes e
    # remove alguma coisa quando o total excede o teto.
    before = execution_repo.count()
    prune_executions(max(1, before - 3))
    after = execution_repo.count()
    assert after <= before
    # as 2 mais novas que criamos sobrevivem (nunca são as mais antigas do banco).
    assert execution_repo.get_by_id(ids[-1]) is not None
    assert execution_repo.get_by_id(ids[-2]) is not None
