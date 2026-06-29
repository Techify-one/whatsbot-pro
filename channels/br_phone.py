"""Brazilian phone-number variant helper (plano 23 Fase E2).

Extracted from ``db.repositories.contact_repo`` so the digit-normalization rule
lives at the channel layer (it is a WhatsApp/BR number concern, not a data-access
one). Pure Python — no ``db`` import — so importing it from ``contact_repo``
introduces no cycle.
"""

from __future__ import annotations


def br_phone_variants(phone: str) -> list[str]:
    """Return phone number variants for Brazilian numbers.

    BR mobile numbers can have 8 or 9 local digits:
    - 13 digits: 55 + 2-digit DDD + 9 + 8 digits (user-typed format)
    - 12 digits: 55 + 2-digit DDD + 8 digits (WhatsApp canonical format)
    """
    if not phone or not phone.startswith("55"):
        return [phone]
    if len(phone) == 13 and phone[4] == "9":
        alt = phone[:4] + phone[5:]
        return [phone, alt]
    if len(phone) == 12:
        alt = phone[:4] + "9" + phone[4:]
        return [phone, alt]
    return [phone]
