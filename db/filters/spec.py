"""FilterSpec: normalizes both flat query params and Chatwoot-style payloads (plano 08).

Both entry points produce the SAME list of Clause objects, so GET ?status=open and
POST [{attribute_key:'status', filter_operator:'equal_to', values:['open']}] translate
to an identical WHERE.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from db.filters.registry import FilterError, CATTR_PREFIX


@dataclass
class Clause:
    attribute_key: str
    operator: str
    values: list


@dataclass
class FilterSpec:
    clauses: list = field(default_factory=list)   # combined per `match`
    match: str = "and"                            # 'and' | 'or' (flat, P78)
    limit: int = 50
    offset: int = 0


_RESERVED = {"limit", "offset", "match"}


def _infer_operator(key: str, value: str) -> tuple[str, list]:
    """Map a flat ``key=value`` into (operator, values) with sensible defaults."""
    low = value.strip().lower()
    if low in ("present", "is_present"):
        return "is_present", []
    if low in ("absent", "none", "is_not_present", "unassigned"):
        return "is_not_present", []
    if key == "since":
        return "greater_than", [value.strip()]
    if key == "q":
        return "contains", [value]
    if key in ("labels", "conv_labels"):
        return "in", [v.strip() for v in value.split(",") if v.strip()]
    if "," in value:
        return "in", [v.strip() for v in value.split(",") if v.strip()]
    return "equal_to", [value]


def from_params(params: dict) -> FilterSpec:
    """Build a spec from flat query params (?status=open&labels=vip,lead&since=7d&cattr:plan=gold)."""
    spec = FilterSpec()
    op_overrides = {
        raw_key[:-4]: str(raw_val)
        for raw_key, raw_val in params.items()
        if str(raw_key).endswith("__op") and raw_val is not None
    }
    for raw_key, raw_val in params.items():
        if raw_key in _RESERVED or str(raw_key).endswith("__op"):
            continue
        if raw_val is None:
            continue
        value = str(raw_val)
        if raw_key in op_overrides:
            op = op_overrides[raw_key]
            values = _values_for_operator(op, value)
        else:
            op, values = _infer_operator(raw_key, value)
        spec.clauses.append(Clause(attribute_key=raw_key, operator=op, values=values))
    spec.match = str(params.get("match", "and")).lower()
    spec.limit = _coerce_int(params.get("limit"), 50, 1, 200)
    spec.offset = _coerce_int(params.get("offset"), 0, 0, 10_000_000)
    return spec


def _values_for_operator(op: str, value: str) -> list:
    if op in ("is_present", "is_not_present"):
        return []
    if op in ("in", "between") or "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return [value]


def from_payload(payload: dict) -> FilterSpec:
    """Build a spec from a structured payload: {match?, limit?, offset?, filters:[{attribute_key, filter_operator, values}]}."""
    spec = FilterSpec()
    raw = payload.get("filters") or payload.get("payload") or []
    if not isinstance(raw, list):
        raise FilterError("`filters` deve ser uma lista.")
    for item in raw:
        if not isinstance(item, dict):
            raise FilterError("Cada filtro deve ser um objeto.")
        key = item.get("attribute_key")
        op = item.get("filter_operator") or item.get("operator")
        values = item.get("values", [])
        if not key or not op:
            raise FilterError("Filtro requer attribute_key e filter_operator.")
        if not isinstance(values, list):
            values = [values]
        spec.clauses.append(Clause(attribute_key=str(key), operator=str(op), values=values))
    spec.match = str(payload.get("match", "and")).lower()
    spec.limit = _coerce_int(payload.get("limit"), 50, 1, 200)
    spec.offset = _coerce_int(payload.get("offset"), 0, 0, 10_000_000)
    return spec


def _coerce_int(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))
