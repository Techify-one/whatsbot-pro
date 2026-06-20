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
    agent_repo, prompt_repo, conversation_repo, contact_repo,
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
    def __init__(self, ai_engine_enabled=True):
        self.ai_engine_enabled = ai_engine_enabled
        self.model = "test/model"
        self.system_prompt = "Prompt base legado"


class FakeContact:
    def __init__(self, cid, phone):
        self.id = cid
        self.phone = phone
        self.is_group = False


class FakeCtx:
    def __init__(self, handler, contact):
        self.handler = handler
        self.contact = contact


# ── Seed agents/prompts ─────────────────────────────────────────────
agent_factory.seed_default_agent({"system_prompt": "Olá, sou o atendimento.",
                                  "model": "test/model"})
prompt_repo.save("suporte_prompt", "Você é o agente de SUPORTE técnico.")
agent_repo.save("suporte", display_name="Suporte", prompt_key="suporte_prompt",
                model_config={"model": "test/model"}, tool_names=None, enabled=True)
agent_repo.save("vendas", display_name="Vendas", prompt_key="default",
                model_config={"model": "test/model"}, tool_names=None, enabled=True)
agent_repo.save("triagem", display_name="Triagem", prompt_key="default",
                model_config={"model": "test/model"}, tool_names=None, enabled=True,
                is_router=True, routing_targets=["vendas"])

# Contact + open conversation
c = contact_repo.get_or_create("5511888880001")
conv = conversation_repo.resolve_for_contact(
    c["id"], "5511888880001@s.whatsapp.net", reopen_if_closed=True)
contact = FakeContact(c["id"], "5511888880001")
handler_on = FakeHandler(True)
ctx = FakeCtx(handler_on, contact)

print("transferir_agente em CORE_TOOLS:")
check("registrado em CORE_TOOLS",
      any(s["function"]["name"] == "transferir_agente" for s, _ in CORE_TOOLS))

print("\nbuild_for_contact — precedência:")
spec = agent_factory.build_for_contact(handler_on, contact)
check("sem binding -> agente default",
      spec is not None and spec.agent_key == agent_repo.DEFAULT_AGENT_KEY)
check("flag OFF -> None (caminho legado)",
      agent_factory.build_for_contact(FakeHandler(False), contact) is None)

print("\ntransferir_agente — gating e validação:")
r = transferir_agente.execute(FakeCtx(FakeHandler(False), contact), {"agente": "suporte"})
check("flag OFF -> erro de roteamento desativado", r.startswith("Erro: o roteamento"))
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
agent_repo.save("suporte", display_name="Suporte", prompt_key="suporte_prompt",
                model_config={"model": "test/model"}, tool_names=None, enabled=False)
dynamic_registry.invalidate()  # a API faz isso via _emit_changed; aqui o save é direto
spec = agent_factory.build_for_contact(handler_on, contact)
check("agente vinculado desativado -> cai no default",
      spec is not None and spec.agent_key == agent_repo.DEFAULT_AGENT_KEY)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
