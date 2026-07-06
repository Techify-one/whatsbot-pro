"""Unit tests puros para ``coerce_json`` N-camadas (plano 34 F1).

Sem DB, sem HTTP — só o helper de decode.

    venv/bin/python tests/test_coerce_json.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.repositories._mapping import coerce_json  # noqa: E402

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


single = '{"a": 1}'
double = json.dumps(single)              # '"{\\"a\\": 1}"'
triple = json.dumps(double)

print("coerce_json — desembrulho N-camadas:")
check("single-encoded dict → dict", coerce_json(single, {}) == {"a": 1})
check("double-encoded dict → dict", coerce_json(double, {}) == {"a": 1})
check("triple-encoded dict → dict", coerce_json(triple, {}) == {"a": 1})

list_single = '[1, 2, 3]'
list_double = json.dumps(list_single)
check("single-encoded list → list", coerce_json(list_single, []) == [1, 2, 3])
check("double-encoded list → list", coerce_json(list_double, []) == [1, 2, 3])

print("\ncoerce_json — passthrough e defaults:")
check("dict pronto passa intacto", coerce_json({"a": 1}, {}) == {"a": 1})
check("list pronta passa intacta", coerce_json([1, 2], []) == [1, 2])
check("None → default", coerce_json(None, {"d": 1}) == {"d": 1})
check("'' → default", coerce_json("", {"d": 1}) == {"d": 1})
check("número escalar decodifica", coerce_json("42", None) == 42)
check("bool escalar decodifica", coerce_json("true", None) is True)

print("\ncoerce_json — string que sobra vira default (P2):")
check("string solta não-JSON → default", coerce_json("texto solto", {"d": 1}) == {"d": 1})
check("string JSON de conteúdo ('\"hi\"') → default", coerce_json('"hi"', {"d": 1}) == {"d": 1})
check("JSON malformado → default", coerce_json('{bad', []) == [])

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
