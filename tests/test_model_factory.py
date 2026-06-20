"""Unit tests for ai_engine.model_factory (pure, no DB/agno).

    venv/bin/python tests/test_model_factory.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
k = mf.build_kwargs({"model": "m", "max_output_tokens": 777, "thinking_level": "high"},
                    fallback_model="x", default_max_tokens=D)
check("alias max_output_tokens→max_tokens", k["max_tokens"] == 777)
check("alias thinking_level→reasoning_effort", k.get("reasoning_effort") == "high")

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

# Valor de variável inválido é ignorado (não quebra)
k = mf.build_kwargs({"model": "m"}, fallback_model="x", default_max_tokens=D,
                    variables={"temperature": "não-numero", "max_tokens": "lixo"})
check("var inválida: temperature ignorada", "temperature" not in k)
check("var inválida: max_tokens cai no default", k["max_tokens"] == D)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
