"""Hardening da resolução de agentes contra JSON corrompido/duplo-codificado.

Plano 34 (Track A). Uma linha de ``ai_agents`` com um campo JSON **duplo-codificado**
(uma string JSON dentro de outra) derrubava 100% das conversas de IA:
``build_for_contact`` fazia ``dict(agent["model_config"])`` sobre a *string* interna
e estourava, reclassificado como ``AgentResolutionError`` ("banco quebrado") → nada
era enviado ao cliente.

Repro read-only do QA (§2.5 do plano): ``SELECT model_config FROM ai_agents
WHERE agent_key='default'`` cujo ``repr`` começa com ``"`` = a coluna TEXT guarda
uma string JSON (dupla-codificação), não o objeto.

Histórico de fases:
- **F0** caracterizou o crash ANTES do conserto: com ``coerce_json`` de 1 camada,
  ``build_for_contact`` **levantava** ``AgentResolutionError`` (ver commit F0).
- **F1** (``coerce_json`` N-camadas) faz o campo duplo desembrulhar já no repo, então
  ``model_config`` recuperável volta ao objeto real e o turno degrada em vez de
  estourar. Este arquivo passou a asseverar o comportamento pós-conserto.
- **F2** cobre o piso de emergência + ``hooks_config``/``routing_targets``.
- **F4** cobre o caminho end-to-end e o caminho feliz.

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


def _double_encode(python_value) -> str:
    """Texto DUPLO-codificado (uma string JSON dentro de outra)."""
    return json.dumps(json.dumps(python_value, ensure_ascii=False),
                      ensure_ascii=False)


def _write_raw(agent_key: str, column: str, raw_text: str) -> None:
    """Grava um texto cru na coluna TEXT e invalida o cache do registry."""
    with get_engine().begin() as conn:
        conn.execute(
            update(ai_agents).where(ai_agents.c.agent_key == agent_key)
            .values({column: raw_text})
        )
    dynamic_registry.invalidate()


def _reset_default_clean() -> None:
    """Restaura a linha `default` para um model_config limpo."""
    agent_repo.save(
        agent_repo.DEFAULT_AGENT_KEY, display_name="Agente padrão",
        prompt=agent_factory.DEFAULT_SYSTEM_PROMPT,
        model_config={"model": agent_factory.DEFAULT_MODEL},
        tool_names=None, enabled=True,
    )
    dynamic_registry.invalidate()


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
check("linha limpa usa o modelo default",
      spec.model == agent_factory.DEFAULT_MODEL)

# ── F1: model_config duplo-codificado RECUPERÁVEL → desembrulha no repo ────
# F0 caracterizou o crash pré-conserto (ver commit F0). Com o unwrap N-camadas,
# o objeto real é recuperado e build_for_contact NÃO levanta.
print("\nF1 — model_config duplo-codificado recuperável:")
raw = _double_encode({"model": "x/y"})
check("texto gravado começa com '\"' (duplo)", raw.startswith('"'))
_write_raw(agent_repo.DEFAULT_AGENT_KEY, "model_config", raw)

_row = agent_repo.get(agent_repo.DEFAULT_AGENT_KEY)
check("agent_repo desembrulha model_config para dict (N-camadas)",
      isinstance(_row.get("model_config"), dict))

raised = False
try:
    spec = agent_factory.build_for_contact(handler, contact)
except agent_factory.AgentResolutionError:
    raised = True
    spec = None
check("build_for_contact NÃO levanta (degrada)", not raised)
check("model real recuperado do duplo-encoding", spec is not None and spec.model == "x/y")

# ── F1: model_config IRRECUPERÁVEL → cai no default, ainda sem raise ───────
print("\nF1 — model_config irrecuperável (string pura duplo-codificada):")
_write_raw(agent_repo.DEFAULT_AGENT_KEY, "model_config", _double_encode("lixo solto"))
raised = False
try:
    spec = agent_factory.build_for_contact(handler, contact)
except agent_factory.AgentResolutionError:
    raised = True
    spec = None
check("build_for_contact NÃO levanta (degrada p/ default)", not raised)
check("cai no DEFAULT_MODEL quando não recupera",
      spec is not None and spec.model == agent_factory.DEFAULT_MODEL)

_reset_default_clean()

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
