"""Unit tests for ai_engine.hooks (declarative tool-call gates, pure).

    venv/bin/python tests/test_hooks.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_engine.hooks import check_hooks

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


# Sem hooks_config → nunca bloqueia
check("sem config -> None", check_hooks(None, "x", []) is None)
check("config vazio -> None", check_hooks({}, "x", []) is None)
check("tool sem regra -> None", check_hooks({"y": {"call_limit": 1}}, "x", []) is None)

# call_limit
cfg = {"buscar": {"call_limit": 2}}
check("call_limit: 0 chamadas -> permite", check_hooks(cfg, "buscar", []) is None)
ex1 = [{"tool": "buscar"}]
check("call_limit: 1<2 -> permite", check_hooks(cfg, "buscar", ex1) is None)
ex2 = [{"tool": "buscar"}, {"tool": "buscar"}]
check("call_limit: 2>=2 -> bloqueia", check_hooks(cfg, "buscar", ex2) is not None)
check("call_limit: chamadas skipped não contam",
      check_hooks(cfg, "buscar", [{"tool": "buscar", "skipped": True},
                                  {"tool": "buscar", "skipped": True}]) is None)
check("call_limit: 0 -> bloqueia já na 1ª",
      check_hooks({"buscar": {"call_limit": 0}}, "buscar", []) is not None)

# requires_prior_call
cfg2 = {"pagar": {"requires_prior_call": "autenticar"}}
check("requires_prior: sem prévia -> bloqueia",
      check_hooks(cfg2, "pagar", []) is not None)
check("requires_prior: com prévia -> permite",
      check_hooks(cfg2, "pagar", [{"tool": "autenticar"}]) is None)
check("requires_prior: prévia skipped não conta",
      check_hooks(cfg2, "pagar", [{"tool": "autenticar", "skipped": True}]) is not None)

# Combinação: limit + requires
cfg3 = {"pagar": {"call_limit": 1, "requires_prior_call": "autenticar"}}
check("combo: sem prévia -> bloqueia (requires)",
      check_hooks(cfg3, "pagar", [{"tool": "outra"}]) is not None)
check("combo: com prévia, 0 chamadas -> permite",
      check_hooks(cfg3, "pagar", [{"tool": "autenticar"}]) is None)

# Robustez: config malformado nunca quebra
check("config malformado (cfg não-dict) -> None",
      check_hooks({"x": "lixo"}, "x", []) is None)
check("call_limit bool é ignorado (True não é limite)",
      check_hooks({"x": {"call_limit": True}}, "x", [{"tool": "x"}]) is None)

# ── Caracterização plano 29 (Fase A0) — baseline ANTES dos guardrails A1 ──
# Estes checks documentam o comportamento ATUAL; A1 muda cada um de propósito
# (success-aware, lista de priors, default global) e atualiza os checks junto.
check("A0: prior que rodou e FALHOU ainda satisfaz requires_prior_call (muda em A1)",
      check_hooks(cfg2, "pagar",
                  [{"tool": "autenticar", "result": "[ERRO] credencial inválida"}]) is None)
check("A0: requires_prior_call em LISTA é ignorado — não bloqueia (muda em A1)",
      check_hooks({"pagar": {"requires_prior_call": ["autenticar"]}}, "pagar", []) is None)
check("A0: tool sem call_limit próprio é ilimitada — sem default global (muda em A1)",
      check_hooks({}, "buscar", [{"tool": "buscar"}] * 50) is None)
_blk = check_hooks({"buscar": {"call_limit": 1}}, "buscar", [{"tool": "buscar"}])
check("A0: mensagem de bloqueio não orienta rota de escape (muda em A1)",
      _blk is not None and "transferir_agente" not in _blk)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
