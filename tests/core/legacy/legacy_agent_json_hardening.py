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
- **F5** (Track B) migrou as 4 colunas JSON de ``ai_agents`` para **JSONB nativo**,
  então as checagens de formato bruto abaixo asseveram objeto/array nativo (não mais
  string single-encoded em TEXT). A resiliência (build_for_contact degrada/recupera)
  segue idêntica — o ``coerce_json`` de leitura continua desembrulhando legado.

    venv/bin/python -m tests.core.legacy.legacy_agent_json_hardening
"""

import json
import logging
import sys
from pathlib import Path
from tests.paths import PROJECT_ROOT

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


def _read_raw(agent_key: str, column: str) -> str | None:
    """Lê o texto CRU da coluna (sem passar por coerce_json)."""
    from sqlalchemy import select
    with get_engine().connect() as conn:
        return conn.execute(
            select(ai_agents.c[column]).where(ai_agents.c.agent_key == agent_key)
        ).scalar()


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


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _with_error_log(fn):
    """Roda ``fn`` capturando ERRORs do logger de agent_factory."""
    cap = _LogCapture()
    lg = logging.getLogger("agent.agent_factory")
    lg.addHandler(cap)
    try:
        result = fn()
    finally:
        lg.removeHandler(cap)
    return result, cap.records


# ── F2: hooks_config duplo-codificado → coerção tolerante, sem raise ──────
print("\nF2 — hooks_config duplo-codificado:")
_write_raw(agent_repo.DEFAULT_AGENT_KEY, "hooks_config",
           _double_encode({"transferir_agente": {"call_limit": 2}}))
raised = False
try:
    spec = agent_factory.build_for_contact(handler, contact)
except agent_factory.AgentResolutionError:
    raised = True
    spec = None
check("build_for_contact NÃO levanta com hooks_config duplo", not raised)
check("hooks_config recuperado no _hooks_config",
      spec is not None
      and spec.model_config.get("_hooks_config") == {"transferir_agente": {"call_limit": 2}})
_reset_default_clean()

# ── F2: routing_targets duplo-codificado → sem raise ──────────────────────
print("\nF2 — routing_targets duplo-codificado (roteador):")
agent_repo.save("triagem34", display_name="Triagem", prompt="Você é a triagem.",
                model_config={"model": "t/t"}, tool_names=None, enabled=True,
                is_router=True, routing_targets=["vendas"])
_write_raw("triagem34", "routing_targets", _double_encode(["vendas", "suporte"]))
# vincula o contato ao roteador para exercer a leitura de routing_targets
_conv = conversation_repo.get_open_for_contact(c["id"])
conversation_repo.set_agent(_conv["id"], "triagem34")
dynamic_registry.invalidate()
raised = False
try:
    spec = agent_factory.build_for_contact(handler, contact)
except agent_factory.AgentResolutionError:
    raised = True
    spec = None
check("build_for_contact NÃO levanta com routing_targets duplo", not raised)
check("resolveu o roteador triagem34", spec is not None and spec.agent_key == "triagem34")
# volta o contato para o default e limpa
conversation_repo.set_agent(_conv["id"], None)
_reset_default_clean()

# ── F2: agente default desativado → piso de emergência + logger.error ─────
print("\nF2 — agente default desativado → piso de emergência:")
with get_engine().begin() as conn:
    conn.execute(update(ai_agents)
                 .where(ai_agents.c.agent_key == agent_repo.DEFAULT_AGENT_KEY)
                 .values(enabled=0))
dynamic_registry.invalidate()
(spec, records) = _with_error_log(lambda: agent_factory.build_for_contact(handler, contact))
check("piso: retorna AgentSpec (não levanta)", spec is not None)
check("piso: agent_key = default", spec is not None and spec.agent_key == agent_repo.DEFAULT_AGENT_KEY)
check("piso: usa DEFAULT_MODEL", spec is not None and spec.model == agent_factory.DEFAULT_MODEL)
check("piso: emite logger.error", any(r.levelno == logging.ERROR for r in records))
_reset_default_clean()

# ── F3: guarda defensiva no agent_repo — caller passa STRING crua ─────────
# Simula um caller (ex.: plugin de terceiro) que passa uma string JSON já
# codificada em vez de dict. Antes: json.dumps(string) → dupla codificação.
print("\nF3 — agent_repo._native_json_field coage string crua (objeto JSONB, sem duplicar):")
agent_repo.save("f3repo", display_name="F3", prompt="x",
                model_config='{"model": "z/z"}',  # STRING crua (dirty caller)
                tool_names='["save_contact_info"]', enabled=True)
raw_mc = _read_raw("f3repo", "model_config")
check("model_config gravado é objeto JSONB nativo (F5, não string dupla)",
      isinstance(raw_mc, dict) and raw_mc == {"model": "z/z"})
check("model_config relido é dict com o modelo certo",
      agent_repo.get("f3repo").get("model_config") == {"model": "z/z"})
raw_tn = _read_raw("f3repo", "tool_names")
check("tool_names gravado é array JSONB nativo (F5)",
      isinstance(raw_tn, list) and raw_tn == ["save_contact_info"])
check("tool_names relido é lista", agent_repo.get("f3repo").get("tool_names") == ["save_contact_info"])
agent_repo.delete("f3repo")

# ── F3: helper da rota + save_agent_prompt sobre linha suja ───────────────
# Reproduz o caminho de save_agent_prompt: grava linha default DUPLO-codificada,
# saneia via _coerce_agent_json_fields e regrava — releitura crua tem que voltar
# single-encoded (sem tripla codificação).
print("\nF3 — _coerce_agent_json_fields + patch de prompt sobre linha suja:")
from server.routes.ai_engine import _coerce_agent_json_fields  # noqa: E402
_write_raw(agent_repo.DEFAULT_AGENT_KEY, "model_config", _double_encode({"model": "w/w"}))
_write_raw(agent_repo.DEFAULT_AGENT_KEY, "hooks_config", _double_encode({"h": 1}))
existing = agent_repo.get(agent_repo.DEFAULT_AGENT_KEY)
clean = _coerce_agent_json_fields(existing)
check("helper devolve model_config dict", clean["model_config"] == {"model": "w/w"})
check("helper devolve hooks_config dict", clean["hooks_config"] == {"h": 1})
agent_repo.save(
    agent_repo.DEFAULT_AGENT_KEY, display_name=existing.get("display_name") or "",
    prompt="Prompt novo via wizard",
    model_config=clean["model_config"], tool_names=clean["tool_names"],
    enabled=bool(existing.get("enabled", True)),
    description=existing.get("description", ""),
    is_router=bool(existing.get("is_router", False)),
    routing_targets=clean["routing_targets"], hooks_config=clean["hooks_config"],
)
raw_after = _read_raw(agent_repo.DEFAULT_AGENT_KEY, "model_config")
check("após patch: model_config é objeto JSONB nativo (F5, sem tripla codificação)",
      isinstance(raw_after, dict) and raw_after == {"model": "w/w"})
check("após patch: model_config relido volta ao objeto",
      agent_repo.get(agent_repo.DEFAULT_AGENT_KEY).get("model_config") == {"model": "w/w"})
check("após patch: prompt atualizado",
      agent_repo.get(agent_repo.DEFAULT_AGENT_KEY).get("prompt") == "Prompt novo via wizard")
_reset_default_clean()

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
