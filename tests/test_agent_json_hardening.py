"""Hardening da resolução de agentes contra JSON corrompido/duplo-codificado.

Plano 34 (Track A). Caracteriza — e depois trava — o comportamento quando uma
linha de ``ai_agents`` guarda um campo JSON **duplo-codificado** (uma string JSON
dentro de outra), a corrupção que derrubava 100% das conversas de IA.

Repro read-only do QA (§2.5 do plano): ``SELECT model_config FROM ai_agents
WHERE agent_key='default'`` cujo ``repr`` começa com ``"`` = a coluna TEXT guarda
uma string JSON (dupla-codificação), não o objeto.

    venv/bin/python tests/test_agent_json_hardening.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.pg import init_test_engine  # noqa: E402
init_test_engine(reset=True)

from sqlalchemy import update  # noqa: E402
from db.engine import get_engine  # noqa: E402
from db.tables import ai_agents  # noqa: E402
from db.repositories import agent_repo, contact_repo, conversation_repo  # noqa: E402
from agent import agent_factory  # noqa: E402
from ai_engine import dynamic_registry  # noqa: E402

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
    def __init__(self, *_a, **_k):
        pass


class FakeContact:
    def __init__(self, cid, phone):
        self.id = cid
        self.phone = phone
        self.is_group = False


def _corrupt_field(agent_key: str, column, python_value) -> str:
    """Grava um valor DUPLO-codificado na coluna TEXT (uma string JSON dentro de
    outra) e devolve o texto cru gravado, simulando a corrupção original."""
    double = json.dumps(json.dumps(python_value, ensure_ascii=False),
                        ensure_ascii=False)
    with get_engine().begin() as conn:
        conn.execute(
            update(ai_agents).where(ai_agents.c.agent_key == agent_key)
            .values({column: double})
        )
    dynamic_registry.invalidate()
    return double


# ── Setup: agente default limpo + contato ────────────────────────────────
agent_factory.seed_default_agent()
c = contact_repo.get_or_create("5511777770034")
conversation_repo.resolve_for_contact(
    c["id"], "5511777770034@s.whatsapp.net", reopen_if_closed=True)
contact = FakeContact(c["id"], "5511777770034")
handler = FakeHandler()

print("baseline (linha limpa):")
spec = agent_factory.build_for_contact(handler, contact)
check("linha limpa resolve o default",
      spec is not None and spec.agent_key == agent_repo.DEFAULT_AGENT_KEY)

# ── F0: caracteriza o crash atual em model_config duplo-codificado ────────
# F0: caracteriza o bug; F4 inverte para NÃO levantar (IA responde no default).
print("\nF0 — model_config duplo-codificado (crash atual):")
raw = _corrupt_field(agent_repo.DEFAULT_AGENT_KEY, "model_config", {"model": "x/y"})
# Repro read-only do QA: o texto gravado começa com aspas => dupla-codificação.
check("model_config gravado começa com '\"' (duplo)", raw.startswith('"'))

# O consumidor lê a linha e vê uma STRING onde esperava dict.
_row = agent_repo.get(agent_repo.DEFAULT_AGENT_KEY)
check("agent_repo devolve model_config como str (decode de 1 camada)",
      isinstance(_row.get("model_config"), str))

raised = False
try:
    agent_factory.build_for_contact(handler, contact)
except agent_factory.AgentResolutionError:
    raised = True
check("build_for_contact LEVANTA AgentResolutionError (crash de hoje)", raised)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
