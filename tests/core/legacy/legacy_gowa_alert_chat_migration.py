"""Auto-migração do chat_id do alerta de desconexão (plano 32).

Quando o grupo de destino do alerta vira supergrupo, o Telegram troca o chat_id
e devolve o novo em ``parameters.migrate_to_chat_id`` na resposta de erro. A
interceptação central em ``_tg_call`` (assets/plugin_examples/gowa/alerts.py)
deve: (1) persistir o novo id em ``config``, (2) reexecutar a chamada UMA vez com
o novo chat_id, (3) devolver sucesso ao caller — tudo transparente.

Este teste é standalone (o plugin ``gowa`` não é exercitado pela suíte core) e
NÃO toca no banco: ``httpx`` e ``config_repo.set`` são mockados.

    source venv/bin/activate
    python -m tests.core.legacy.legacy_gowa_alert_chat_migration
"""

import os
import sys
import asyncio
import importlib.util
from pathlib import Path
from tests.paths import PROJECT_ROOT as ROOT

os.environ.setdefault("WHATSBOT_TEST", "1")

sys.path.insert(0, str(ROOT))


# ── Test runner (mirrors tests/test_gowa_plugin.py) ────────────────────────
passed = 0
failed = 0
errors = []


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        msg = f"  FAIL {name}" + (f" -- {detail}" if detail else "")
        print(msg)
        errors.append(msg)


def section(title: str):
    print(f"\n{'-'*60}")
    print(f"  {title}")
    print(f"{'-'*60}")


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


alerts = _load_module(
    str(ROOT / "assets" / "plugin_examples" / "gowa" / "alerts.py"),
    "test_gowa_alerts")


# ── Fake httpx: a queue of JSON responses shared across (recursive) clients ──
class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, shared):
        self._shared = shared

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self._shared["calls"].append({"url": url, "json": json})
        return _FakeResp(self._shared["responses"].pop(0))


def _install_httpx(shared):
    """Patch httpx.AsyncClient in the loaded module with a shared response queue."""
    class _Factory:
        def __call__(self, *a, **k):
            return _FakeClient(shared)
    alerts.httpx.AsyncClient = _Factory()


# ── Capture config_repo.set without a DB ────────────────────────────────────
_orig_set = alerts.config_repo.set
_persisted: list[tuple] = []


def _fake_set(key, value):
    _persisted.append((key, value))


alerts.config_repo.set = _fake_set
_orig_client = alerts.httpx.AsyncClient

MIG = {"ok": False, "error_code": 400,
       "description": "Bad Request: group chat was upgraded to a supergroup chat",
       "parameters": {"migrate_to_chat_id": -1009990001112}}
OK42 = {"ok": True, "result": {"message_id": 42}}


try:
    loop = asyncio.new_event_loop()

    # ── 1. Migração detectada → persiste novo id + reexecuta com sucesso ──
    section("chat_id migra para supergrupo → auto-atualiza e reenvia")
    _persisted.clear()
    shared = {"responses": [MIG, OK42], "calls": []}
    _install_httpx(shared)
    mid = loop.run_until_complete(alerts._tg_send("TKN", "-100307", "oi"))

    check("_tg_send devolve o message_id do retry (42)", mid == 42, f"mid={mid}")
    check("exatamente 2 chamadas HTTP (original + 1 retry)",
          len(shared["calls"]) == 2, f"calls={len(shared['calls'])}")
    check("retry usa o novo chat_id (str do migrate_to_chat_id)",
          shared["calls"][1]["json"].get("chat_id") == "-1009990001112",
          str(shared["calls"][1]["json"].get("chat_id")))
    check("novo chat_id persistido em config uma vez",
          _persisted == [("plugin.gowa.disconnect_alert_chat_id", "-1009990001112")],
          str(_persisted))

    # ── 2. Anti-loop: se o retry TAMBÉM migrar, não recorre de novo ──
    section("guarda anti-loop: retry que também migra não recorre")
    _persisted.clear()
    shared2 = {"responses": [MIG, MIG], "calls": []}
    _install_httpx(shared2)
    data = loop.run_until_complete(
        alerts._tg_call("TKN", "sendMessage", {"chat_id": "-100307", "text": "x"}))
    check("no 2º erro devolve o data cru (não recorre)", data.get("ok") is False)
    check("no máximo 2 chamadas HTTP (1 retry só)",
          len(shared2["calls"]) == 2, f"calls={len(shared2['calls'])}")
    check("persiste config só uma vez (não em cascata)",
          len(_persisted) == 1, str(_persisted))

    # ── 3. Erro comum (sem migrate_to_chat_id) não dispara persist/retry ──
    section("erro comum não migra")
    _persisted.clear()
    shared3 = {"responses": [{"ok": False, "description": "chat not found"}], "calls": []}
    _install_httpx(shared3)
    data = loop.run_until_complete(
        alerts._tg_call("TKN", "sendMessage", {"chat_id": "-100307", "text": "x"}))
    check("erro sem migrate_to_chat_id: uma só chamada", len(shared3["calls"]) == 1)
    check("erro sem migrate_to_chat_id: nada persistido", _persisted == [])

    # ── 4. Chamada sem chat_id (não é envio de chat) nunca migra ──
    section("chamada sem chat_id não migra")
    _persisted.clear()
    shared4 = {"responses": [MIG], "calls": []}
    _install_httpx(shared4)
    data = loop.run_until_complete(alerts._tg_call("TKN", "getMe", {}))
    check("payload sem chat_id: sem retry", len(shared4["calls"]) == 1)
    check("payload sem chat_id: sem persist", _persisted == [])

    loop.close()
finally:
    alerts.config_repo.set = _orig_set
    alerts.httpx.AsyncClient = _orig_client


# ═══════════════════════════════════════════════════════════════════
#  Results
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'='*60}")
if failed:
    sys.exit(1)
