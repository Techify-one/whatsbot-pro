"""Reusable, injection-safe filter engine for conversations (plano 08).

Design: an ALLOWLIST is the only path. ``registry.DIMENSIONS`` + ``registry.OPS``
are the single source of truth — any attribute_key or operator not present raises
FilterError (→ HTTP 400). Values always flow through SQLAlchemy Core bind params;
nothing user-supplied is ever interpolated into SQL text. cattr:<key> keys are
additionally validated against active filterable conversation definitions.
"""

from db.filters.registry import FilterError, DIMENSIONS, OPS, CATTR_PREFIX
from db.filters.spec import FilterSpec, Clause
from db.filters.translate import build_where, build_contact_where, available_dimensions

__all__ = [
    "FilterError", "DIMENSIONS", "OPS", "CATTR_PREFIX",
    "FilterSpec", "Clause", "build_where", "build_contact_where",
    "available_dimensions",
]
