"""Standalone tests for the audit trail (plano 07 Fase 0/1) — Postgres de teste, no HTTP.

    venv/bin/python tests/test_audit.py
"""

import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.pg import init_test_engine  # noqa: E402
init_test_engine(reset=True)

from db.repositories import audit_repo  # noqa: E402
from db import audit_actions  # noqa: E402
from server import audit_listener  # noqa: E402
from server.audit_context import ActorCtx, set_current_actor, reset_current_actor  # noqa: E402

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


# ── Migration created the table ─────────────────────────────────────────
from sqlalchemy import inspect as _inspect  # noqa: E402
from db.engine import get_engine  # noqa: E402
_cols = {c["name"] for c in _inspect(get_engine()).get_columns("audit_log")}
check("migration 0017 criou audit_log",
      {"actor_type", "action", "resource_type", "before_json", "after_json",
       "request_id", "created_at"} <= _cols)

# ── add + query + count ─────────────────────────────────────────────────
_id = audit_repo.add(action="config.update", resource_type="config",
                     resource_id="abc", after={"x": 1})
check("add retorna id", isinstance(_id, int) and _id > 0)
rows = audit_repo.query()
check("query devolve a linha", len(rows) == 1 and rows[0]["action"] == "config.update")
check("actor_type default system", rows[0]["actor_type"] == "system")
check("count = 1", audit_repo.count() == 1)

# ── sanitização de segredos (na ingestão) ───────────────────────────────
audit_repo.add(action="config.update", resource_type="config",
               after={"openrouter_api_key": "sk-SECRET", "model": "gpt",
                      "nested": {"access_token": "tok", "ok": 2},
                      "list": [{"password": "p"}]})
_last = audit_repo.query(limit=1)[0]
_aj = _last["after_json"]
check("segredo top-level mascarado", "sk-SECRET" not in _aj and '"openrouter_api_key": "***"' in _aj)
check("segredo aninhado mascarado", '"access_token": "***"' in _aj)
check("segredo em lista mascarado", '"password": "***"' in _aj)
check("valor não-segredo preservado", '"model": "gpt"' in _aj)

# ── filtros ─────────────────────────────────────────────────────────────
audit_repo.add(action="plugin.enable", resource_type="plugin", resource_id="lembretes",
               actor_type="user", actor_user_id=7)
check("filtro por action", len(audit_repo.query(action="plugin.enable")) == 1)
check("filtro por resource_type", len(audit_repo.query(resource_type="plugin")) == 1)
check("filtro por actor_user_id", len(audit_repo.query(actor_user_id=7)) == 1)
check("count com filtro", audit_repo.count(resource_type="config") == 2)
_da = audit_repo.distinct_actions()
check("distinct_actions lista actions", "config.update" in _da["actions"] and "plugin.enable" in _da["actions"])

# ── listener: evento na allowlist grava; fora não ───────────────────────
_before = audit_repo.count()


def _ctx(name):
    return types.SimpleNamespace(event_name=name)


audit_listener.audit_event_handler(_ctx("tool_override.changed"), {"name": "buscar"})
check("listener grava evento auditável (tool_override.changed)",
      audit_repo.count() == _before + 1 and
      audit_repo.query(action="tool.override")[0]["resource_id"] == "buscar")
audit_listener.audit_event_handler(_ctx("message.sent"), {"phone": "5511"})
check("listener ignora evento fora da allowlist", audit_repo.count() == _before + 1)
audit_listener.audit_event_handler(_ctx("config.changed"),
                                   {"keys_changed": ["x"], "openrouter_api_key": "sk"})
_cfg = audit_repo.query(action="config.update", limit=1)[0]
check("listener mascara segredo do payload", "sk" not in (_cfg["after_json"] or ""))

# ── before/after: emit sites anexam _audit_before/_audit_after ──────────────
audit_listener.audit_event_handler(_ctx("config.changed"), {
    "keys_changed": ["model"], "ts": 0,
    "_audit_before": {"model": "old-model"},
    "_audit_after": {"model": "new-model"},
})
_cfg2 = audit_repo.query(action="config.update", limit=1)[0]
check("before gravado (estado antigo)", '"model": "old-model"' in (_cfg2["before_json"] or ""))
check("after = _audit_after explícito", '"model": "new-model"' in (_cfg2["after_json"] or ""))
check("after não vaza chaves _audit_* nem o payload bruto",
      "_audit_" not in (_cfg2["after_json"] or "") and "keys_changed" not in (_cfg2["after_json"] or ""))
# Segredo no before também é mascarado.
audit_listener.audit_event_handler(_ctx("config.changed"), {
    "keys_changed": ["openrouter_api_key"], "ts": 0,
    "_audit_before": {"openrouter_api_key": "sk-OLD"},
    "_audit_after": {"openrouter_api_key": "sk-NEW"},
})
_cfg3 = audit_repo.query(action="config.update", limit=1)[0]
check("segredo no before é mascarado",
      "sk-OLD" not in (_cfg3["before_json"] or "") and "sk-NEW" not in (_cfg3["after_json"] or ""))
# tag.deleted carrega o estado anterior.
audit_listener.audit_event_handler(_ctx("tag.deleted"), {
    "name": "VIP", "ts": 0, "_audit_before": {"name": "VIP", "color": "#f00"}})
_tagdel = audit_repo.query(action="tag.delete", limit=1)[0]
check("tag.delete grava before", '"color": "#f00"' in (_tagdel["before_json"] or ""))
check("tag.delete resource_id = nome", _tagdel["resource_id"] == "VIP")

# ── ator via contextvar ─────────────────────────────────────────────────
_tok = set_current_actor(ActorCtx(id=42, type="user", label="Thiago", ip="1.2.3.4", request_id="req1"))
audit_listener.audit_event_handler(_ctx("plugin.disabled"), {"plugin_id": "x"})
reset_current_actor(_tok)
_row = audit_repo.query(action="plugin.disable", limit=1)[0]
check("listener usa ator do contextvar", _row["actor_user_id"] == 42 and _row["actor_label"] == "Thiago")
check("listener captura ip/request_id", _row["ip_address"] == "1.2.3.4" and _row["request_id"] == "req1")

# ── purge (única deleção) ───────────────────────────────────────────────
import time as _time  # noqa: E402
audit_repo.add(action="old.event", resource_type="config", created_at=_time.time() - 100000)
_n = audit_repo.count()
_removed = audit_repo.purge(_time.time() - 50000)
check("purge remove só linhas antigas", _removed == 1 and audit_repo.count() == _n - 1)

# ── invariante append-only: sem update/delete-by-id no repo ─────────────
_src = (PROJECT_ROOT / "db" / "repositories" / "audit_repo.py").read_text()
check("repo não expõe update()", "def update(" not in _src)
check("repo não expõe delete por id (só purge)",
      "def delete(" not in _src and _src.count("sa_delete(") == 1)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
