"""Translate a FilterSpec into an injection-safe SQLAlchemy Core WHERE (plano 08).

Every value becomes a bind param; keys/operators are validated against the allowlist
in registry.py before any clause is built. cattr:<key> keys are re-validated against a
regex AND the caller-supplied set of active filterable conversation keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import and_, or_, not_, select

from db.tables import conversations, contacts, contact_tags, tags
from db.filters.registry import (
    FilterError, DIMENSIONS, OPS, CATTR_PREFIX, CATTR_KEY_RE,
)

_CATTR_RE = re.compile(CATTR_KEY_RE)


@dataclass
class FilterContext:
    user_id: int | None = None
    now: float = 0.0
    cattr_keys: frozenset = field(default_factory=frozenset)  # active filterable conversation keys


def build_where(spec, ctx: FilterContext):
    """Return a Core ColumnElement (or None if no clauses) for the spec."""
    clauses = [_build_clause(c, ctx) for c in spec.clauses]
    clauses = [c for c in clauses if c is not None]
    if not clauses:
        return None
    return or_(*clauses) if spec.match == "or" else and_(*clauses)


def _check_op(dim_key: str, allowed: frozenset, op: str) -> None:
    if op not in OPS:
        raise FilterError(f"Operador desconhecido: {op!r}.")
    if op not in allowed:
        raise FilterError(f"Operador {op!r} não permitido para {dim_key!r}.")


def _build_clause(clause, ctx: FilterContext):
    key = clause.attribute_key
    op = clause.operator
    values = clause.values or []

    if key.startswith(CATTR_PREFIX):
        return _cattr_clause(key[len(CATTR_PREFIX):], op, values, ctx)

    dim = DIMENSIONS.get(key)
    if dim is None:
        raise FilterError(f"Atributo não filtrável: {key!r}.")
    _check_op(dim.key, dim.ops, op)

    kind = dim.kind
    if kind == "enum":
        for v in values:
            if v not in dim.enum:
                raise FilterError(f"Valor inválido para {key!r}: {v!r}.")
        col = conversations.c.status
        return _scalar_clause(col, op, values)
    if kind == "bool":
        truthy = str(values[0]).lower() in ("1", "true", "yes", "sim") if values else False
        return conversations.c.is_archived == (1 if truthy else 0)
    if kind == "int":
        col = conversations.c.inbox_id if key == "inbox_id" else conversations.c.display_id
        return _scalar_clause(col, op, [_to_int(v, key) for v in values])
    if kind == "text":
        return _scalar_clause(conversations.c.priority, op, [str(v) for v in values])
    if kind == "assignee":
        return _assignee_clause(op, values, ctx)
    if kind == "reltime":
        threshold = _resolve_reltime(values[0] if values else "", ctx.now)
        return conversations.c.last_activity_at > threshold
    if kind == "labels":
        return _labels_clause(values)
    if kind == "q":
        return _q_clause(values[0] if values else "")
    raise FilterError(f"Tipo de dimensão não suportado: {kind!r}.")  # pragma: no cover


def _scalar_clause(col, op: str, values: list):
    if op == "equal_to":
        return col == values[0]
    if op == "not_equal_to":
        return col != values[0]
    if op == "in":
        return col.in_(values)
    if op == "is_present":
        return col.isnot(None)
    if op == "is_not_present":
        return col.is_(None)
    if op == "greater_than":
        return col > values[0]
    if op == "less_than":
        return col < values[0]
    if op == "between":
        return col.between(values[0], values[1])
    if op == "contains":
        return col.like(f"%{values[0]}%")
    if op == "does_not_contain":
        return not_(col.like(f"%{values[0]}%"))
    raise FilterError(f"Operador não implementado: {op!r}.")  # pragma: no cover


def _assignee_clause(op: str, values: list, ctx: FilterContext):
    col = conversations.c.assignee_user_id
    if op == "is_present":
        return col.isnot(None)
    if op == "is_not_present":
        return col.is_(None)
    resolved = []
    for v in values:
        if str(v).lower() == "me":
            if ctx.user_id is None:
                raise FilterError("Filtro 'me' requer um usuário autenticado.")
            resolved.append(ctx.user_id)
        else:
            resolved.append(_to_int(v, "assignee"))
    if op == "equal_to":
        return col == resolved[0]
    if op == "in":
        return col.in_(resolved)
    raise FilterError(f"Operador {op!r} não permitido para assignee.")  # pragma: no cover


def _labels_clause(values: list):
    names = [str(v) for v in values if str(v).strip()]
    if not names:
        raise FilterError("Filtro de etiquetas requer ao menos um valor.")
    sub = (select(contact_tags.c.contact_id)
           .join(tags, tags.c.id == contact_tags.c.tag_id)
           .where(tags.c.name.in_(names)))
    return conversations.c.contact_id.in_(sub)


def _q_clause(term: str):
    term = str(term).strip()
    if not term:
        raise FilterError("Busca vazia.")
    like = f"%{term}%"
    sub = (select(contacts.c.id)
           .where(or_(contacts.c.name.ilike(like), contacts.c.phone.like(like))))
    return conversations.c.contact_id.in_(sub)


def _cattr_clause(raw_key: str, op: str, values: list, ctx: FilterContext):
    if not _CATTR_RE.match(raw_key):
        raise FilterError(f"Chave de atributo inválida: {raw_key!r}.")
    if raw_key not in ctx.cattr_keys:
        raise FilterError(f"Atributo {raw_key!r} não é filtrável.")
    allowed = frozenset({"equal_to", "not_equal_to", "is_present", "is_not_present",
                         "contains", "does_not_contain", "in"})
    _check_op(f"cattr:{raw_key}", allowed, op)
    # generic JSON indexing → JSON_EXTRACT (SQLite) / ->> (Postgres); key is allowlisted
    # AND regex-validated, so no JSON-path break-out is possible.
    expr = conversations.c.custom_attributes[raw_key].as_string()
    return _scalar_clause(expr, op, [str(v) for v in values])


def _to_int(v, key: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise FilterError(f"Valor numérico inválido para {key!r}: {v!r}.")


def _resolve_reltime(s: str, now: float) -> float:
    s = str(s).strip().lower()
    units = {"d": 86400, "h": 3600, "m": 60, "w": 604800}
    if len(s) >= 2 and s[-1] in units and s[:-1].isdigit():
        return now - int(s[:-1]) * units[s[-1]]
    try:
        return float(s)  # absolute epoch fallback
    except ValueError:
        raise FilterError(f"Valor de tempo relativo inválido: {s!r}.")


def available_dimensions(cattr_defs: list[dict]) -> list[dict]:
    """The filter-schema: static dimensions + cattr:<key> for filterable conversation defs."""
    dims = []
    for d in DIMENSIONS.values():
        entry = {"key": d.key, "label": d.label, "kind": d.kind, "ops": sorted(d.ops)}
        if d.enum:
            entry["enum"] = sorted(d.enum)
        dims.append(entry)
    for cd in cattr_defs:
        dims.append({
            "key": f"{CATTR_PREFIX}{cd['attribute_key']}",
            "label": cd.get("display_name") or cd["attribute_key"],
            "kind": "cattr",
            "ops": ["equal_to", "not_equal_to", "is_present", "is_not_present",
                    "contains", "does_not_contain", "in"],
        })
    return dims
