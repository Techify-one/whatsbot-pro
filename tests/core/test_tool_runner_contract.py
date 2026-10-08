"""Caracterização do contrato de I/O do ``agent.tool_runner`` (plano 177 · F0).

Trava o envelope ATUAL do runner isolado ANTES de a entrega de credenciais
(F3) mexer nele: quem não usa credencial tem de continuar vendo exatamente o
mesmo. Sobe o subprocesso real (``python -m agent.tool_runner``) e fala com ele
pelo stdin, como o host faz — sem banco, sem app.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(request: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "agent.tool_runner"],
        input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
        capture_output=True, cwd=str(REPO_ROOT), env=env, timeout=30,
    )


def _envelope(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout.decode("utf-8"))


_ECHO = '''
SCHEMA = {"type": "function", "function": {"name": "eco_contrato",
          "description": "d", "parameters": {"type": "object", "properties": {}}}}

def execute(ctx, args):
    return "phone=%s;grupo=%s;handler=%r;plugin=%r;args=%s" % (
        ctx.phone, ctx.is_group, ctx.handler, ctx.plugin_id,
        json.dumps(args, sort_keys=True))

import json
'''


def test_execute_envelope_ok_and_context_snapshot():
    proc = _run({
        "name": "eco_contrato", "code": _ECHO, "args": {"a": 1},
        "context": {"phone": "5511999990000", "is_group": False,
                    "contact": {"name": "Fulano"}, "plugin_id": None},
    })
    assert proc.returncode == 0, proc.stderr
    env = _envelope(proc)
    assert env == {
        "ok": True,
        "result": "phone=5511999990000;grupo=False;handler=None;plugin=None;args={\"a\": 1}",
    }


def test_unknown_ctx_attribute_degrades_to_none():
    code = _ECHO.replace("ctx.handler", "ctx.plugin_db")
    proc = _run({"name": "eco_contrato", "code": code, "args": {}, "context": {}})
    assert proc.returncode == 0, proc.stderr
    assert "handler=None" in _envelope(proc)["result"]


def test_tool_exception_is_a_handled_outcome_on_exit_0():
    code = (
        'SCHEMA = {"type": "function", "function": {"name": "quebra"}}\n'
        'def execute(ctx, args):\n'
        '    raise ValueError("boom")\n'
    )
    proc = _run({"name": "quebra", "code": code, "args": {}, "context": {}})
    assert proc.returncode == 0
    assert _envelope(proc) == {"ok": False, "error": "ValueError: boom"}


def test_none_result_round_trips():
    code = (
        'SCHEMA = {"type": "function", "function": {"name": "nada"}}\n'
        'def execute(ctx, args):\n'
        '    return None\n'
    )
    proc = _run({"name": "nada", "code": code, "args": {}, "context": {}})
    assert proc.returncode == 0
    assert _envelope(proc) == {"ok": True, "result": None}


def test_tool_prints_never_corrupt_the_stdout_envelope():
    code = (
        'SCHEMA = {"type": "function", "function": {"name": "fala"}}\n'
        'def execute(ctx, args):\n'
        '    print("ruido")\n'
        '    return "limpo"\n'
    )
    proc = _run({"name": "fala", "code": code, "args": {}, "context": {}})
    assert proc.returncode == 0
    assert _envelope(proc) == {"ok": True, "result": "limpo"}
    assert "ruido" in proc.stderr.decode("utf-8")


def test_empty_code_is_a_runner_level_failure_on_exit_1():
    proc = _run({"name": "vazia", "code": "", "args": {}, "context": {}})
    assert proc.returncode == 1
    assert "empty/invalid code" in proc.stderr.decode("utf-8")


def test_describe_mode_returns_schemas_and_ignores_extra_keys():
    # O describe NÃO executa ``execute`` e não precisa de contexto algum.
    proc = _run({"mode": "describe", "name": "eco_contrato", "code": _ECHO})
    assert proc.returncode == 0, proc.stderr
    env = _envelope(proc)
    assert env["ok"] is True
    assert [s["function"]["name"] for s in env["schemas"]] == ["eco_contrato"]
