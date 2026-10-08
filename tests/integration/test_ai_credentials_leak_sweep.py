"""Plano 177 · F7 — varredura de vazamento do cofre de credenciais.

Grava uma credencial com um valor SENTINELA único, exercita a API e execuções
reais de tool (uma que DEVOLVE o segredo, uma que FALHA com ele, uma que o
imprime) e depois percorre TODAS as tabelas do schema ``public`` — exceto as duas
do próprio cofre — atrás do sentinela. O esperado é zero.

Também afirma que o valor não aparece em ``caplog`` nem em ``execution_steps``
(onde o resultado de uma tool é persistido pelo motor, ``agno_engine``).

⚠️ A varredura procura o valor BRUTO. Base64/URL-encode/fatiamento escapam — é o
limite declarado (D9), travado em ``test_ai_credentials_isolation.py``.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import text

from agent import tool_isolation
from agent.tool_isolation import REDACTED, run_isolated_tool
from db.engine import get_engine
from db.repositories import credential_repo as repo

CRED = "p177_sweep"
TOOL_ECHO = "p177_sweep_echo"
TOOL_FAIL = "p177_sweep_fail"
TOOL_PRINT = "p177_sweep_print"
VAULT_TABLES = {"ai_credentials", "ai_credential_tools"}

# único por execução: um resto de outra rodada nunca é confundido com vazamento
SENTINEL = f"SENT-{uuid.uuid4().hex}"

_HEAD = ('SCHEMA = {"type": "function", "function": {"name": "%s", "description": "d", '
         '"parameters": {"type": "object", "properties": {}}}}\n')


def _code(name: str, body: str) -> str:
    return (_HEAD % name) + "def execute(ctx, args):\n" + "".join(
        f"    {line}\n" for line in body.strip().splitlines())


@pytest.fixture(autouse=True)
def _vault(_engine_ready):
    previous = tool_isolation.logger.disabled
    tool_isolation.logger.disabled = False   # o fileConfig do Alembic o desliga
    repo.delete(CRED)
    repo.upsert_credential(CRED, "varredura", [
        {"key": "url", "secret": False, "value": "https://sweep.exemplo.com"},
        {"key": "chave", "secret": True, "value": SENTINEL},
    ])
    repo.set_tool_links(CRED, [TOOL_ECHO, TOOL_FAIL, TOOL_PRINT])
    yield
    repo.delete(CRED)
    tool_isolation.logger.disabled = previous


def _leaks() -> list[tuple[str, int]]:
    found = []
    with get_engine().connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))]
        for t in tables:
            if t in VAULT_TABLES:
                continue
            n = conn.execute(
                text(f'SELECT count(*) FROM "{t}" WHERE "{t}"::text LIKE :p'),
                {"p": f"%{SENTINEL}%"}).scalar()
            if n:
                found.append((t, n))
    return found


def _run_all_three():
    return [
        run_isolated_tool(TOOL_ECHO, _code(TOOL_ECHO, 'return "k=" + ctx.credential("p177_sweep")["chave"]'), None, {}),
        run_isolated_tool(TOOL_FAIL, _code(TOOL_FAIL, 'raise RuntimeError("falhou " + ctx.credential("p177_sweep")["chave"])'), None, {}),
        run_isolated_tool(TOOL_PRINT, _code(TOOL_PRINT, 'print("vazou " + ctx.credential("p177_sweep")["chave"])\nreturn "ok"'), None, {}),
    ]


def test_the_sentinel_really_is_in_the_vault(_vault):
    """Sem isto a varredura 'zero' poderia ser um falso verde (cofre vazio)."""
    with get_engine().connect() as conn:
        n = conn.execute(
            text('SELECT count(*) FROM ai_credentials WHERE ai_credentials::text LIKE :p'),
            {"p": f"%{SENTINEL}%"}).scalar()
    assert n == 1


def test_sweep_finds_a_planted_leak(_engine_ready):
    """Anti-vácuo da varredura: se ela não acha um vazamento plantado, não vale."""
    marker = f"PLANT-{uuid.uuid4().hex}"
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO config (key, value) VALUES (:k, :v)"),
                     {"k": f"p177_plant_{uuid.uuid4().hex[:8]}", "v": marker})
    try:
        with get_engine().connect() as conn:
            hits = conn.execute(
                text('SELECT count(*) FROM "config" WHERE "config"::text LIKE :p'),
                {"p": f"%{marker}%"}).scalar()
        assert hits == 1
    finally:
        with get_engine().begin() as conn:
            conn.execute(text("DELETE FROM config WHERE value = :v"), {"v": marker})


def test_outputs_of_the_three_runs_are_redacted(caplog):
    caplog.set_level(logging.DEBUG)
    echo, fail, printed = _run_all_three()
    assert echo == f"k={REDACTED}"
    assert SENTINEL not in fail and REDACTED in fail
    assert printed == "ok"
    assert any("AI tool" in r.getMessage() for r in caplog.records), caplog.text
    assert SENTINEL not in caplog.text


def test_no_table_outside_the_vault_holds_the_secret(caplog, _engine_ready):
    caplog.set_level(logging.DEBUG)
    _run_all_three()
    # o que a API do cofre toca também: lista, vínculo, ficha da tool
    assert _leaks() == []


def test_execution_steps_never_receive_the_raw_secret(_engine_ready):
    """O motor persiste o resultado da tool em ``execution_steps``
    (``track_step("tool_executed", {..., "result": ...})``); o que chega lá é a
    string JÁ redigida que sai de ``run_isolated_tool``. Reproduz esse passo com o
    repositório real e varre o banco."""
    from db.repositories import execution_repo

    result = run_isolated_tool(
        TOOL_ECHO, _code(TOOL_ECHO, 'return "k=" + ctx.credential("p177_sweep")["chave"]'), None, {})
    assert SENTINEL not in result
    exec_id = execution_repo.create("5511900000177", "webhook")
    try:
        execution_repo.add_step(
            exec_id, "tool_executed", {"tool": TOOL_ECHO, "args": {}, "result": result})
        with get_engine().connect() as conn:
            stored = conn.execute(
                text("SELECT data FROM execution_steps WHERE execution_id = :e"),
                {"e": exec_id}).scalar()
        assert REDACTED in stored and SENTINEL not in stored
        assert _leaks() == []
    finally:
        with get_engine().begin() as conn:
            conn.execute(text("DELETE FROM executions WHERE id = :e"), {"e": exec_id})
