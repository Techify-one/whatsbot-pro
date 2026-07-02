"""Standalone tests for the multi-agent routing/handoff (plano 06).

Exercises agent_factory.build_for_contact precedence (conversa→inbox→default) and
the transferir_agente tool directly against a temp DB — no LLM / no HTTP needed.

    venv/bin/python tests/test_agent_routing.py
"""

import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_tmpdir = tempfile.mkdtemp(prefix="whatsbot_routing_test_")
_db_path = Path(_tmpdir) / "whatsbot.db"

from db import init_db  # noqa: E402
init_db(_db_path)

from sqlalchemy import update  # noqa: E402
from db.engine import get_engine  # noqa: E402
from db.tables import inboxes  # noqa: E402
from db.repositories import (  # noqa: E402
    agent_repo, conversation_repo, contact_repo,
)
from agent import agent_factory  # noqa: E402
from agent.tools import transferir_agente, CORE_TOOLS  # noqa: E402
from ai_engine import dynamic_registry  # noqa: E402  (config cache; API invalida via _emit_changed)

_passed = 0
_failed = 0


def check(label: str, cond: bool):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


class FakeHandler:
    """Plano 22: single AI engine — build_for_contact ignores the handler."""
    def __init__(self, *_args, **_kwargs):
        pass


class FakeContact:
    def __init__(self, cid, phone):
        self.id = cid
        self.phone = phone
        self.is_group = False


class FakeCtx:
    def __init__(self, handler, contact):
        self.handler = handler
        self.contact = contact


# ── Seed agents (prompt inline em cada agente) ──────────────────────
agent_factory.seed_default_agent()
agent_repo.save("suporte", display_name="Suporte",
                prompt="Você é o agente de SUPORTE técnico.",
                model_config={"model": "test/model"}, tool_names=None, enabled=True)
agent_repo.save("vendas", display_name="Vendas", prompt="Você é o agente de vendas.",
                model_config={"model": "test/model"}, tool_names=None, enabled=True)
agent_repo.save("triagem", display_name="Triagem", prompt="Você é a triagem.",
                model_config={"model": "test/model"}, tool_names=None, enabled=True,
                is_router=True, routing_targets=["vendas"])

# Contact + open conversation
c = contact_repo.get_or_create("5511888880001")
conv = conversation_repo.resolve_for_contact(
    c["id"], "5511888880001@s.whatsapp.net", reopen_if_closed=True)
contact = FakeContact(c["id"], "5511888880001")
handler_on = FakeHandler()
ctx = FakeCtx(handler_on, contact)

print("transferir_agente em CORE_TOOLS:")
check("registrado em CORE_TOOLS",
      any(s["function"]["name"] == "transferir_agente" for s, _ in CORE_TOOLS))

print("\nbuild_for_contact — precedência:")
spec = agent_factory.build_for_contact(handler_on, contact)
check("sem binding -> agente default",
      spec is not None and spec.agent_key == agent_repo.DEFAULT_AGENT_KEY)

print("\ntransferir_agente — validação:")
r = transferir_agente.execute(ctx, {"agente": ""})
check("agente vazio -> erro", r.startswith("Erro:"))
r = transferir_agente.execute(ctx, {"agente": "inexistente"})
check("destino inexistente -> erro lista disponíveis", "não existe" in r)

print("\ntransferir_agente — handoff persistente:")
r = transferir_agente.execute(ctx, {"agente": "suporte"})
check("transfere p/ suporte -> confirma", "Suporte" in r)
conv2 = conversation_repo.get_open_for_contact(c["id"])
check("conversa ganha active_agent_key=suporte", conv2["active_agent_key"] == "suporte")
r = transferir_agente.execute(ctx, {"agente": "suporte"})
check("transferir p/ o mesmo -> idempotente/aviso", "já está atendendo" in r)

print("\nbuild_for_contact resolve o agente vinculado:")
spec = agent_factory.build_for_contact(handler_on, contact)
check("agora resolve 'suporte'", spec is not None and spec.agent_key == "suporte")
check("prompt do suporte renderizado", "suporte" in spec.base_prompt.lower())

print("\nrouter allowlist (routing_targets):")
conversation_repo.set_agent(conv["id"], "triagem")  # router c/ targets=["vendas"]
r = transferir_agente.execute(ctx, {"agente": "suporte"})
check("destino fora da allowlist do router -> erro", "destinos permitidos" in r)
r = transferir_agente.execute(ctx, {"agente": "vendas"})
check("destino na allowlist -> ok", "Vendas" in r)

print("\ninbox.default_agent_key (precedência intermediária):")
conversation_repo.set_agent(conv["id"], None)  # limpa o binding da conversa
with get_engine().begin() as cn:
    cn.execute(update(inboxes).where(inboxes.c.id == conv["inbox_id"])
               .values(default_agent_key="suporte"))
check("sem active, com inbox default -> resolve inbox default",
      agent_factory.resolve_active_agent_key(contact) == "suporte")

# Agente vinculado desativado cai no default (fail-safe)
with get_engine().begin() as cn:
    cn.execute(update(inboxes).where(inboxes.c.id == conv["inbox_id"])
               .values(default_agent_key=None))
conversation_repo.set_agent(conv["id"], "suporte")
agent_repo.save("suporte", display_name="Suporte",
                prompt="Você é o agente de SUPORTE técnico.",
                model_config={"model": "test/model"}, tool_names=None, enabled=False)
dynamic_registry.invalidate()  # a API faz isso via _emit_changed; aqui o save é direto
spec = agent_factory.build_for_contact(handler_on, contact)
check("agente vinculado desativado -> cai no default",
      spec is not None and spec.agent_key == agent_repo.DEFAULT_AGENT_KEY)

print("\nhooks_config — enforcement no entrypoint do agno:")
from agent import agno_engine  # noqa: E402

class _DispatchSpy(FakeHandler):
    def __init__(self):
        super().__init__(True)
        self.dispatched = []
    def _dispatch_tool(self, contact, name, args):
        self.dispatched.append(name)
        return "ok"

spy = _DispatchSpy()
executed = []
# call_limit=1 já satisfeito -> a 2ª chamada deve bloquear sem despachar
executed.append({"tool": "buscar"})
ep = agno_engine._make_sync_entrypoint(spy, contact, "5511888880001", "buscar",
                                       executed, {"buscar": {"call_limit": 1}})
out = ep()
check("hooks: 2ª chamada bloqueada retorna aviso ao LLM", "limite" in out)
check("hooks: tool bloqueada NÃO foi despachada", "buscar" not in spy.dispatched)
check("hooks: registra skipped/blocked", executed[-1].get("blocked") is not None)
# Sem hooks_config -> despacha normalmente
ep2 = agno_engine._make_sync_entrypoint(spy, contact, "5511888880001", "buscar", [], None)
ep2()
check("sem hooks_config: despacha normal", "buscar" in spy.dispatched)

print("\nexecution_repo — routing_steps + step agent_key (migration 0016):")
from db.repositories import execution_repo  # noqa: E402

_ex = execution_repo.create("5511888880001", "test")
execution_repo.add_step(_ex, "llm_request", {"x": 1}, agent_key="triagem")
execution_repo.add_step(_ex, "llm_request", {"x": 2}, agent_key="vendas")
execution_repo.set_routing_steps(_ex, [{"from": "triagem", "to": "vendas", "depth": 1}])
_full = execution_repo.get_by_id(_ex)
check("execution.routing_steps persistido (JSON)",
      '"from": "triagem"' in (_full.get("routing_steps") or ""))
_steps = _full.get("steps") or []
check("execution_steps.agent_key gravado por passo",
      {s.get("agent_key") for s in _steps} == {"triagem", "vendas"})

print("\núnico roteador (plano 29 Eixo B):")
# "triagem" é o roteador atual (seed lá em cima). Promover outro rebaixa ela.
agent_repo.save("comercial", display_name="Comercial",
                prompt="Você é o comercial.", model_config={"model": "test/model"},
                tool_names=None, enabled=True,
                is_router=True, routing_targets=["vendas"])
_tri = agent_repo.get("triagem")
_com = agent_repo.get("comercial")
check("promover 2º roteador rebaixa o anterior (radio)",
      _com["is_router"] and not _tri["is_router"])
check("get_router devolve o único roteador",
      (agent_repo.get_router() or {}).get("agent_key") == "comercial")
_tri_hist = agent_repo.list_history("triagem")
check("rebaixamento bumpa versão + snapshot do rebaixado",
      _tri_hist and _tri_hist[0]["version"] == _tri["version"])

# Cinto de segurança no banco: índice único parcial barra violação direta.
import sqlalchemy.exc  # noqa: E402
from sqlalchemy import text as _sql_text  # noqa: E402
try:
    with get_engine().begin() as cn:
        cn.execute(_sql_text(
            "UPDATE ai_agents SET is_router = 1 WHERE agent_key = 'triagem'"))
    _violated = False
except sqlalchemy.exc.IntegrityError:
    _violated = True
check("índice único parcial barra 2º roteador direto no banco", _violated)
check("estado pós-violação: só 'comercial' segue roteador",
      (agent_repo.get_router() or {}).get("agent_key") == "comercial")

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
