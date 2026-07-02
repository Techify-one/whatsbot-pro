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

# requires_prior_call success-aware (plano 29 A1 — portado do nexus)
check("A1: prévia com resultado OK -> permite",
      check_hooks(cfg2, "pagar",
                  [{"tool": "autenticar", "result": "Autenticado com sucesso."}]) is None)
check("A1: prévia que FALHOU ([ERRO]) não satisfaz",
      check_hooks(cfg2, "pagar",
                  [{"tool": "autenticar", "result": "[ERRO] credencial inválida"}]) is not None)
check("A1: prévia 'Erro: ...' (prefixo core) não satisfaz",
      check_hooks(cfg2, "pagar",
                  [{"tool": "autenticar", "result": "Erro: conta não localizada"}]) is not None)
check("A1: prévia 'não existe nenhuma oferta' não satisfaz",
      check_hooks(cfg2, "pagar",
                  [{"tool": "autenticar", "result": "Não existe nenhuma oferta ativa."}]) is not None)
check("A1: última chamada vence (ok depois falha -> bloqueia)",
      check_hooks(cfg2, "pagar",
                  [{"tool": "autenticar", "result": "ok"},
                   {"tool": "autenticar", "result": "[ERRO] expirou"}]) is not None)
check("A1: última chamada vence (falha depois ok -> permite)",
      check_hooks(cfg2, "pagar",
                  [{"tool": "autenticar", "result": "[ERRO] expirou"},
                   {"tool": "autenticar", "result": "ok"}]) is None)
check("A1: resultado legítimo que só MENCIONA erro no meio não é falso-bloqueado",
      check_hooks(cfg2, "pagar",
                  [{"tool": "autenticar",
                    "result": "Pedido 12 teve um erro: pagamento recusado; pedido 13 ok."}]) is None)

# requires_prior_call como LISTA (plano 29 A1/A-i9)
cfg_list = {"pagar": {"requires_prior_call": ["autenticar", "buscar"]}}
check("A1: lista — todas satisfeitas -> permite",
      check_hooks(cfg_list, "pagar",
                  [{"tool": "autenticar", "result": "ok"},
                   {"tool": "buscar", "result": "ok"}]) is None)
_blk_list = check_hooks(cfg_list, "pagar", [{"tool": "autenticar", "result": "ok"}])
check("A1: lista — uma faltando -> bloqueia nomeando a faltante",
      _blk_list is not None and "buscar" in _blk_list and "autenticar" not in _blk_list)

# default_call_limit global (plano 29 A1/A-i7 — config ai_tool_call_limit_per_tool)
check("A1: default global limita tool sem call_limit próprio",
      check_hooks({}, "buscar", [{"tool": "buscar"}] * 3, default_call_limit=3) is not None)
check("A1: default global abaixo do uso -> permite",
      check_hooks({}, "buscar", [{"tool": "buscar"}] * 2, default_call_limit=3) is None)
check("A1: call_limit próprio VENCE o default global (maior)",
      check_hooks({"buscar": {"call_limit": 5}}, "buscar",
                  [{"tool": "buscar"}] * 3, default_call_limit=1) is None)
check("A1: default 0 = ilimitado",
      check_hooks({}, "buscar", [{"tool": "buscar"}] * 50, default_call_limit=0) is None)

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

# ── Plano 29 A1 (era caracterização A0; comportamento mudou de propósito) ──
check("A1: tool sem call_limit e SEM default segue ilimitada (legado preservado)",
      check_hooks({}, "buscar", [{"tool": "buscar"}] * 50) is None)
_blk = check_hooks({"buscar": {"call_limit": 1}}, "buscar", [{"tool": "buscar"}])
check("A1: mensagem de bloqueio (call_limit) orienta a rota de escape",
      _blk is not None and "transferir_agente" in _blk)
_blk_prior = check_hooks(cfg2, "pagar", [])
check("A1: mensagem de bloqueio (requires_prior) orienta a rota de escape",
      _blk_prior is not None and "transferir_agente" in _blk_prior)
_blk_esc = check_hooks(None, "transferir_agente", [{"tool": "transferir_agente", "result": "ok"}])
check("A1: bloqueio do PRÓPRIO transferir_agente não sugere ele mesmo como escape",
      _blk_esc is not None and "use 'transferir_agente'" not in _blk_esc.lower())

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
