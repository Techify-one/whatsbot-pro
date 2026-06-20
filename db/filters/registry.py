"""Allowlist of filterable dimensions + operators (plano 08).

THE security boundary. Adding a dimension here is the ONLY way to make a column
filterable; everything else is rejected. Keep this minimal and explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class FilterError(ValueError):
    """Raised on any disallowed attribute_key / operator / value (→ HTTP 400)."""


# Operator vocabulary (names only). Each dimension declares which subset it allows;
# translate.py maps the name to a Core clause. Anything outside this set is rejected.
OPS = frozenset({
    "equal_to", "not_equal_to", "in", "is_present", "is_not_present",
    "greater_than", "less_than", "between", "contains", "does_not_contain",
})

CATTR_PREFIX = "cattr:"
# cattr keys are snake_case identities (enforced at definition creation). Re-validated
# here as defense-in-depth before ever touching a JSON path.
CATTR_KEY_RE = r"^[a-z][a-z0-9_]{0,63}$"


@dataclass(frozen=True)
class Dim:
    key: str
    kind: str                       # enum|bool|int|text|reltime|assignee|labels|q
    ops: frozenset
    label: str = ""
    enum: frozenset = field(default_factory=frozenset)


DIMENSIONS: dict[str, Dim] = {
    "status": Dim("status", "enum",
                  frozenset({"equal_to", "not_equal_to", "in"}),
                  "Status", frozenset({"open", "closed"})),
    "archived": Dim("archived", "bool", frozenset({"equal_to"}), "Arquivada"),
    "assignee": Dim("assignee", "assignee",
                    frozenset({"equal_to", "in", "is_present", "is_not_present"}),
                    "Responsável"),
    "inbox_id": Dim("inbox_id", "int", frozenset({"equal_to", "in"}), "Caixa de entrada"),
    "priority": Dim("priority", "text",
                    frozenset({"equal_to", "in", "is_present", "is_not_present"}),
                    "Prioridade"),
    "since": Dim("since", "reltime", frozenset({"greater_than"}), "Atividade desde"),
    "display_id": Dim("display_id", "int", frozenset({"equal_to"}), "Número"),
    "labels": Dim("labels", "labels", frozenset({"in"}), "Etiquetas"),
    "q": Dim("q", "q", frozenset({"contains"}), "Busca"),
}
