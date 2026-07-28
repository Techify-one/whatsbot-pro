"""Seed built-in custom-attribute definitions (plano 19).

These share the SAME structure as user-defined custom attributes (plano 05) and
ship with the app, seeded idempotently at boot. Two flavours:

- ``is_system=1`` (locked): protected from deletion/rename, shows a "Sistema"
  badge.
- ``is_system=0`` (default attribute): ships pre-defined but is fully editable
  AND deletable — the user may rename or remove it. The contact fields
  CPF/Email/Profissão/Empresa/Endereço are all seeded this way (they used to be
  fixed columns / locked system attrs; now they're managed like any other custom
  attribute, and the contact filter picks them up dynamically). Idempotent seeding
  respects a later deletion (a removed default is NOT re-seeded on the next boot).

The seeds ship WITHOUT a strict ``regex_pattern`` on purpose — a strict regex
would reject a value the AI tries to store and silently "save without saving"
(the very bug plano 19 fixes). Format/checksum validation is a future improvement.
"""

from __future__ import annotations

import logging

from db.repositories import custom_attribute_repo as ca_repo

logger = logging.getLogger(__name__)

# Extensible registry of built-in attributes. Each is ensured idempotently.
# ``is_system`` omitted ⇒ defaults to 1 (locked). Set 0 for editable/deletable
# defaults.
SYSTEM_ATTRIBUTES: list[dict] = [
    {
        "attribute_key": "cpf",
        "display_name": "CPF",
        "type": "text",
        "applies_to": "contact",
        "description": "CPF do contato.",
        "position": 0,
        "is_system": 0,
    },
    # Default contact attributes (editable + deletable). Seeded once; a user
    # deletion sticks across boots.
    {
        "attribute_key": "email",
        "display_name": "Email",
        "type": "text",
        "applies_to": "contact",
        "description": "Email do contato.",
        "position": 1,
        "is_system": 0,
    },
    {
        "attribute_key": "profession",
        "display_name": "Profissão",
        "type": "text",
        "applies_to": "contact",
        "description": "Profissão ou cargo do contato.",
        "position": 2,
        "is_system": 0,
    },
    {
        "attribute_key": "company",
        "display_name": "Empresa",
        "type": "text",
        "applies_to": "contact",
        "description": "Empresa onde o contato trabalha.",
        "position": 3,
        "is_system": 0,
    },
    {
        "attribute_key": "address",
        "display_name": "Endereço",
        "type": "text",
        "applies_to": "contact",
        "description": "Endereço do contato.",
        "position": 4,
        "is_system": 0,
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
