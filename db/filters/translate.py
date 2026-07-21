"""Translate a FilterSpec into an injection-safe SQLAlchemy Core WHERE (plano 08).

Every value becomes a bind param; keys/operators are validated against the allowlist
in registry.py before any clause is built. cattr:<key> keys are re-validated against a
regex AND the caller-supplied set of active filterable conversation keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import and_, or_, not_, select, func, exists, literal

from db.tables import (conversations, contacts, contact_tags, tags, inboxes,
                       conversation_labels, conversation_label_links, mentions)
from db.filters.registry import (
    FilterError, DIMENSIONS, OPS, CATTR_PREFIX, CATTR_KEY_RE, INTERNAL_DIMS,
)

_CATTR_RE = re.compile(CATTR_KEY_RE)


@dataclass
class FilterContext:
    user_id: int | None = None
    now: float = 0.0
    cattr_keys: frozenset = field(default_factory=frozenset)  # active filterable conversation keys
    contact_cattr_keys: frozenset = field(default_factory=frozenset)


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

    if key.startswith(f"{CATTR_PREFIX}contact:"):
        return _contact_cattr_clause(key[len(f"{CATTR_PREFIX}contact:"):], op, values, ctx)
    if key.startswith(f"{CATTR_PREFIX}conversation:"):
        return _cattr_clause(key[len(f"{CATTR_PREFIX}conversation:"):], op, values, ctx)
    if key.startswith(CATTR_PREFIX):
        return _cattr_clause(key[len(CATTR_PREFIX):], op, values, ctx)

    dim = DIMENSIONS.get(key)
    if dim is None:
        raise FilterError(f"Atributo não filtrável: {key!r}.")
    _check_op(dim.key, dim.ops, op)

    kind = dim.kind
    if kind == "enum":
        values = [str(v) for v in values]   # coerce: dict/list values never reach `in`
        for v in values:
            if v not in dim.enum:
                raise FilterError(f"Valor inválido para {key!r}: {v!r}.")
        return _scalar_clause(conversations.c.status, op, values)
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
    if kind == "activity":
        return _activity_clause(op, values, ctx)
    if kind == "channel":
        return _scalar_clause(func.coalesce(inboxes.c.channel_id, "default"), op,
                              [str(v) for v in values])
    if kind == "contact_type":
        return _scalar_clause(contacts.c.contact_type, op, [str(v) for v in values])
    if kind == "agent":
        return _agent_clause(op, values)
    if kind == "ai":
        return _ai_clause(op, values)
    if kind == "starter":
        return _starter_clause(op, values)
    if kind == "has_mention":
        return _has_mention_clause(values, ctx)
    if kind == "labels":
        return _labels_clause(values)
    if kind == "conv_labels":
        return _conv_labels_clause(values)
    if kind == "q":
        return _q_clause(values[0] if values else "")
    raise FilterError(f"Tipo de dimensão não suportado: {kind!r}.")  # pragma: no cover


def _scalar_clause(col, op: str, values: list):
    if op == "is_present":
        return col.isnot(None)
    if op == "is_not_present":
        return col.is_(None)
    if op == "in":
        if not values:
            raise FilterError("Operador 'in' requer ao menos um valor.")
        return col.in_(values)
    if op == "between":
        if len(values) < 2:
            raise FilterError("Operador 'between' requer dois valores.")
        return col.between(values[0], values[1])
    # remaining scalar operators index values[0]
    if not values:
        raise FilterError(f"Operador {op!r} requer um valor.")
    if op == "equal_to":
        return col == values[0]
    if op == "not_equal_to":
        return col != values[0]
    if op == "greater_than":
        return col > values[0]
    if op == "less_than":
        return col < values[0]
    if op == "contains":
        return col.like(f"%{values[0]}%")
    if op == "does_not_contain":
        return not_(col.like(f"%{values[0]}%"))
    raise FilterError(f"Operador não implementado: {op!r}.")  # pragma: no cover


def _activity_clause(op: str, values: list, ctx: FilterContext):
    if not values:
        raise FilterError("Filtro de atividade requer um valor.")
    if op == "between":
        if len(values) < 2:
            raise FilterError("Operador 'between' requer dois valores.")
        return conversations.c.last_activity_at.between(
            _activity_threshold(values[1], ctx),
            _activity_threshold(values[0], ctx),
        )
    threshold = _activity_threshold(values[0], ctx)
    if op == "less_than":
        return conversations.c.last_activity_at > threshold
    if op == "greater_than":
        return conversations.c.last_activity_at < threshold
    raise FilterError(f"Operador {op!r} não permitido para activity.")  # pragma: no cover


def _activity_threshold(v, ctx: FilterContext) -> float:
    try:
        days = float(v)
    except (TypeError, ValueError):
        raise FilterError(f"Valor de atividade inválido: {v!r}.")
    return ctx.now - days * 86400


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
    if not resolved:
        raise FilterError("Filtro de responsável requer um valor.")
    if op == "equal_to":
        return col == resolved[0]
    if op == "in":
        return col.in_(resolved)
    raise FilterError(f"Operador {op!r} não permitido para assignee.")  # pragma: no cover


def _agent_clause(op: str, values: list):
    vals = [str(v) for v in values if str(v).strip()]
    if not vals:
        raise FilterError("Filtro de agente requer um valor.")
    clauses = []
    for value in vals:
        if value == "none":
            clauses.append(and_(conversations.c.assignee_user_id.is_(None),
                                conversations.c.active_agent_key.is_(None)))
        elif value.startswith("user:"):
            clauses.append(conversations.c.assignee_user_id == _to_int(value[5:], "agent"))
        elif value.startswith("ai:"):
            clauses.append(conversations.c.active_agent_key == value[3:])
        else:
            raise FilterError(f"Valor inválido para 'agent': {value!r}.")
    hit = or_(*clauses)
    return not_(hit) if op == "not_equal_to" else hit


def _ai_clause(op: str, values: list):
    value = str(values[0]).lower() if values else ""
    if value not in ("on", "off"):
        raise FilterError(f"Valor inválido para 'ai': {value!r}.")
    hit = conversations.c.ai_active != 0 if value == "on" else conversations.c.ai_active == 0
    return not_(hit) if op == "not_equal_to" else hit


def _starter_clause(op: str, values: list):
    value = str(values[0]).lower() if values else ""
    if value not in ("customer", "operator"):
        raise FilterError(f"Valor inválido para 'starter': {value!r}.")
    by_customer = conversations.c.origin == "inbound"
    by_operator = or_(conversations.c.origin != "inbound", conversations.c.origin.is_(None))
    hit = by_customer if value == "customer" else by_operator
    return not_(hit) if op == "not_equal_to" else hit


def _has_mention_clause(values: list, ctx: FilterContext):
    """Unread @mention of the logged-in user (feeds the sidebar 'Menções' tab).

    Mirrors the ``mentions`` sub-count in ``conversation_repo.count_tab_counts`` so the
    filtered list and the tab counter agree by construction. Without a user, nobody has
    an unread mention → constant-false (same as the count's ``mentions=0``)."""
    value = str(values[0]).lower() if values else "true"
    want = value in ("1", "true", "yes", "sim", "on")
    if ctx.user_id is None:
        hit = literal(False)
    else:
        hit = (exists()
               .where(mentions.c.conversation_id == conversations.c.id)
               .where(mentions.c.mentioned_user_id == ctx.user_id)
               .where(mentions.c.read_at.is_(None)))
    return hit if want else not_(hit)


def _labels_clause(values: list):
    names = [str(v) for v in values if str(v).strip()]
    if not names:
        raise FilterError("Filtro de etiquetas requer ao menos um valor.")
    sub = (select(contact_tags.c.contact_id)
           .join(tags, tags.c.id == contact_tags.c.tag_id)
           .where(tags.c.name.in_(names)))
    return conversations.c.contact_id.in_(sub)


def _conv_labels_clause(values: list):
    """Filter by CONVERSATION labels (separate from the contact-tag `labels` dim)."""
    names = [str(v) for v in values if str(v).strip()]
    if not names:
        raise FilterError("Filtro de etiquetas da conversa requer ao menos um valor.")
    sub = (select(conversation_label_links.c.conversation_id)
           .join(conversation_labels,
                 conversation_labels.c.id == conversation_label_links.c.label_id)
           .where(conversation_labels.c.name.in_(names)))
    return conversations.c.id.in_(sub)


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


def _contact_cattr_clause(raw_key: str, op: str, values: list, ctx: FilterContext):
    if not _CATTR_RE.match(raw_key):
        raise FilterError(f"Chave de atributo inválida: {raw_key!r}.")
    if raw_key not in ctx.contact_cattr_keys:
        raise FilterError(f"Atributo {raw_key!r} não é filtrável.")
    allowed = frozenset({"equal_to", "not_equal_to", "is_present", "is_not_present",
                         "contains", "does_not_contain", "in"})
    _check_op(f"cattr:contact:{raw_key}", allowed, op)
    expr = contacts.c.custom_attributes[raw_key].as_string()
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
        if d.key in INTERNAL_DIMS:
            continue   # server-expressable but not a user-pickable chip (e.g. has_mention)
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
