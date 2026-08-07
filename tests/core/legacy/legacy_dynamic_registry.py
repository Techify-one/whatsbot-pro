"""Unit tests for ai_engine.dynamic_registry (cache hit / invalidation / TTL).

    venv/bin/python -m tests.core.legacy.legacy_dynamic_registry
"""

import sys
from pathlib import Path
from tests.paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

from ai_engine import dynamic_registry as dr

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


# Counter loader: increments each time it actually runs.
_calls = {"n": 0}


def _loader():
    _calls["n"] += 1
    return f"value-{_calls['n']}"


dr.invalidate()
_calls["n"] = 0

# 1st read computes; 2nd read is a cache hit (loader NOT called again)
v1 = dr.cached("k", _loader)
v2 = dr.cached("k", _loader)
check("1ª leitura computa", v1 == "value-1")
check("2ª leitura é cache hit (loader não roda)", v2 == "value-1" and _calls["n"] == 1)

# invalidate() forces recompute
dr.invalidate()
v3 = dr.cached("k", _loader)
check("após invalidate, recomputa", v3 == "value-2" and _calls["n"] == 2)

# Different keys are independent
dr.cached("outra", _loader)
check("chave distinta computa separado", _calls["n"] == 3)
check("stats reporta entradas", dr.stats()["entries"] >= 2)

# TTL expiry: drop TTL to 0 so the entry is immediately stale
_old_ttl = dr.TTL_SECONDS
dr.TTL_SECONDS = 0.0
dr.invalidate()
_calls["n"] = 0
dr.cached("k", _loader)
dr.cached("k", _loader)  # TTL 0 → always stale → recompute
check("TTL 0 -> sempre recomputa", _calls["n"] == 2)
dr.TTL_SECONDS = _old_ttl

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
