"""Unit tests for ai_engine.model_factory (pure, no DB/agno).

    venv/bin/python -m tests.core.legacy.legacy_model_factory
"""

import sys
from pathlib import Path
from tests.paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

from ai_engine import model_factory as mf

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


D = 2048  # default_max_tokens

# Legacy path: model_config None → só id (fallback) + max_tokens default
k = mf.build_kwargs(None, fallback_model="legacy/model", default_max_tokens=D)
check("legado: id=fallback", k["id"] == "legacy/model")
check("legado: max_tokens=default", k["max_tokens"] == D)
check("legado: sem temperature/top_p/reasoning", "temperature" not in k and "reasoning_effort" not in k)

# model_config explícito
k = mf.build_kwargs({"model": "openai/gpt-x", "temperature": 0.3, "top_p": 0.9, "max_tokens": 500},
                    fallback_model="x", default_max_tokens=D)
check("mc: id do model_config", k["id"] == "openai/gpt-x")
check("mc: temperature", k["temperature"] == 0.3)
check("mc: top_p", k["top_p"] == 0.9)
check("mc: max_tokens", k["max_tokens"] == 500)

# Aliases: max_output_tokens→max_tokens, thinking_level→reasoning_effort
# (valor acima do piso de raciocínio — o clamp do piso tem testes próprios abaixo)
k = mf.build_kwargs({"model": "m", "max_output_tokens": 1777, "thinking_level": "high"},
                    fallback_model="x", default_max_tokens=D)
check("alias max_output_tokens→max_tokens", k["max_tokens"] == 1777)
check("alias thinking_level→reasoning_effort", k.get("reasoning_effort") == "high")

# Plano 37 B2 (barreira): round-trip EXATO do que o formulário de agente emite —
# model_config = {"model": ..., "thinking_level": "low"} (sem max_tokens explícito).
# thinking_level→reasoning_effort E o piso de max_tokens sobe pra ≥1024.
k = mf.build_kwargs({"model": "openai/gpt-5.2", "thinking_level": "low"},
                    fallback_model="x", default_max_tokens=D)
check("B2: thinking_level=low → reasoning_effort=low", k.get("reasoning_effort") == "low")
check("B2: thinking_level → piso max_tokens ≥ 1024", k["max_tokens"] >= 1024)
check("B2: id do model_config preservado", k["id"] == "openai/gpt-5.2")

# A5 (plano 31 F4): piso de max_tokens — valor baixo demais não emudece o bot
k = mf.build_kwargs({"model": "m", "max_tokens": 10}, fallback_model="x", default_max_tokens=D)
check("piso: max_tokens=10 sobe pro piso (sem reasoning)",
      k["max_tokens"] == mf.MIN_MAX_TOKENS == 256)
k = mf.build_kwargs({"model": "m", "max_tokens": 10, "reasoning_effort": "high"},
                    fallback_model="x", default_max_tokens=D)
check("piso: max_tokens=10 + reasoning sobe pro piso de raciocínio",
      k["max_tokens"] == mf.MIN_MAX_TOKENS_REASONING == 1024)
k = mf.build_kwargs({"model": "m", "max_tokens": 500, "reasoning_effort": "low"},
                    fallback_model="x", default_max_tokens=D)
check("piso: 500 + reasoning -> 1024", k["max_tokens"] == 1024)
k = mf.build_kwargs({"model": "m", "max_tokens": 2000, "reasoning_effort": "high"},
                    fallback_model="x", default_max_tokens=D)
check("piso: acima do piso passa intacto (2000 + reasoning)", k["max_tokens"] == 2000)
k = mf.build_kwargs({"model": "m"}, fallback_model="x", default_max_tokens=D,
                    variables={"max_tokens": "32"})
check("piso: var global baixa também é clampada", k["max_tokens"] == 256)

# Cascade: variável global usada quando model_config não traz
k = mf.build_kwargs({"model": "m"}, fallback_model="x", default_max_tokens=D,
                    variables={"temperature": "0.7"})
check("cascade: var global temperature (coerce str→float)", k["temperature"] == 0.7)

# Cascade: per-agent {param}_{agent} vence o global
k = mf.build_kwargs({"model": "m", "_agent_key": "vendas"}, fallback_model="x", default_max_tokens=D,
                    variables={"temperature": "0.7", "temperature_vendas": "0.1"})
check("cascade: per-agent vence global", k["temperature"] == 0.1)

# Cascade: model_config vence variável
k = mf.build_kwargs({"model": "m", "_agent_key": "vendas", "temperature": 0.9}, fallback_model="x",
                    default_max_tokens=D, variables={"temperature_vendas": "0.1"})
check("cascade: model_config vence var", k["temperature"] == 0.9)

# _agent_key nunca vaza para os kwargs do OpenAILike
check("_agent_key não vaza", "_agent_key" not in k)

# 12.2 (plano 31 F5): frequency/presence penalties na mesma cascata de tuning
k = mf.build_kwargs({"model": "m", "frequency_penalty": 0.5, "presence_penalty": 0.3},
                    fallback_model="x", default_max_tokens=D)
check("penalty: model_config frequency_penalty", k["frequency_penalty"] == 0.5)
check("penalty: model_config presence_penalty", k["presence_penalty"] == 0.3)
k = mf.build_kwargs({"model": "m", "_agent_key": "vendas"}, fallback_model="x",
                    default_max_tokens=D,
                    variables={"frequency_penalty": "0.2", "frequency_penalty_vendas": "0.9"})
check("penalty: per-agent vence global (coerce str→float)", k["frequency_penalty"] == 0.9)
k = mf.build_kwargs({"model": "m"}, fallback_model="x", default_max_tokens=D)
check("penalty: ausente não emite kwarg",
      "frequency_penalty" not in k and "presence_penalty" not in k)

# Valor de variável inválido é ignorado (não quebra)
k = mf.build_kwargs({"model": "m"}, fallback_model="x", default_max_tokens=D,
                    variables={"temperature": "não-numero", "max_tokens": "lixo"})
check("var inválida: temperature ignorada", "temperature" not in k)
check("var inválida: max_tokens cai no default", k["max_tokens"] == D)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
