"""Seed built-in (system) custom-attribute definitions (plano 19).

System attributes share the SAME structure as user-defined custom attributes
(plano 05) but carry ``is_system=1``: they ship with the app, are seeded
idempotently at boot, and are protected from deletion/rename in the UI/CRUD.

Starting set: ``cpf`` (contact scope). Extend ``SYSTEM_ATTRIBUTES`` to add more.
The CPF seed ships WITHOUT a strict ``regex_pattern`` on purpose — a strict regex
would reject a value the AI tries to store and silently "save without saving"
(the very bug plano 19 fixes). Format/checksum validation is a future improvement.
"""

from __future__ import annotations

import logging

from db.repositories import custom_attribute_repo as ca_repo

logger = logging.getLogger(__name__)

# Extensible registry of built-in attributes. Each is ensured idempotently.
SYSTEM_ATTRIBUTES: list[dict] = [
    {
        "attribute_key": "cpf",
        "display_name": "CPF",
        "type": "text",
        "applies_to": "contact",
        "description": "CPF do contato.",
        "position": 0,
    },
]


def seed_system_attributes() -> None:
    """Ensure the built-in system attributes exist (idempotent; never clobbers)."""
    for spec in SYSTEM_ATTRIBUTES:
        try:
            created = ca_repo.ensure_system_definition(**spec)
            if created:
                logger.info("System attribute seeded: %s", spec.get("attribute_key"))
        except Exception as e:  # pragma: no cover - defensive, never blocks boot
            logger.warning("system attribute seed failed for %s: %s",
                           spec.get("attribute_key"), e)
