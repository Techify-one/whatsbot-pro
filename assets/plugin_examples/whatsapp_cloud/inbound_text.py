"""Pure PT-BR formatters for WhatsApp Cloud API inbound message types (plano 75 F1).

Why this module exists
----------------------
``_parse_message`` in :mod:`channels` used to leave ``text=""`` for every
``type`` it did not know (``contacts``, ``order``, ``system``,
``unsupported``, …) and dumped the real payload into ``media_extras`` — a dict
the core never persists. The result was a **silent, empty bubble** in the panel
(and an empty ``llm_text`` for the agent). A real production case: a customer
sent a contact card holding the very phone number the agent had just asked for,
and the bubble rendered blank.

This module turns any ``messages[]`` item into a readable PT-BR string. Filling
``text`` is enough: ``MediaContent.js`` already falls back to plain text for any
unknown ``media_type``, so no frontend change is needed (plano 75 D1).

Hard constraints (do not relax)
-------------------------------
* **Pure stdlib only.** No import from the WhatsBot core (``channels.*``,
  ``db.*``, ``server.*``): the plugin zip may be installed on an older core, so
  any core import would break loading — same care as the defensive
  ``MediaLimits`` import in ``channels.py``.
* **Never raises.** Every public function wraps its body in ``try/except`` and
  degrades to an empty string. Callers feed it webhook data straight from the
  wire, including shapes the Meta docs do not describe.
* **Empty string means "nothing to say"** — the caller then applies its own
  generic fallback (``⚠️ Mensagem do tipo "x" não suportada``).
* **Everything is optional.** The official docs only guarantee
  ``name.formatted_name`` and ``phones`` in the minimal contacts example.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# Cap for the raw fallback of an unparseable Flows (``nfm_reply``) payload.
_RAW_JSON_CAP = 200


# ── small helpers (all total: never raise, never assume a type) ─────────────

def _text(value: Any) -> str:
    """Best-effort, whitespace-trimmed string. ``""`` for None/empty/garbage."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _digits(value: Any) -> str:
    return "".join(ch for ch in _text(value) if ch.isdigit())


def _number(value: Any) -> Optional[float]:
    """Parse a numeric field. The docs type ``item_price`` as Integer but the
    official example shows ``7.99`` — so always treat it as decimal."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except (ValueError, AttributeError):
            return None
    return None


def _money(value: float) -> str:
    return f"{value:.2f}"


def _truncate(value: str, cap: int) -> str:
    return value if len(value) <= cap else value[:cap] + "…"


def _join_lines(lines: list) -> str:
    return "\n".join(line for line in lines if line)


# ── contacts ───────────────────────────────────────────────────────────────

def _contact_name(contact: dict) -> str:
    name = _dict(contact.get("name"))
    formatted = _text(name.get("formatted_name"))
    if formatted:
        return formatted
    parts = [_text(name.get("first_name")), _text(name.get("last_name"))]
    return " ".join(p for p in parts if p)


def _contact_phone_line(entry: Any) -> str:
    """One ``📞`` line. Prefers ``phone`` (the formatted form the customer sees)
    and appends ``wa_id`` only when it carries different digits."""
    if isinstance(entry, str):
        phone = _text(entry)
        return f"📞 {phone}" if phone else ""
    item = _dict(entry)
    phone = _text(item.get("phone"))
    wa_id = _text(item.get("wa_id"))
    if phone and wa_id and _digits(wa_id) != _digits(phone):
        return f"📞 {phone} (wa_id: {wa_id})"
    if phone:
        return f"📞 {phone}"
    if wa_id:
        return f"📞 {wa_id}"
    return ""


def _address_line(entry: Any) -> str:
    if isinstance(entry, str):
        addr = _text(entry)
        return f"📍 {addr}" if addr else ""
    item = _dict(entry)
    parts = [
        _text(item.get("street")),
        _text(item.get("city")),
        _text(item.get("state")),
        _text(item.get("zip")),
        _text(item.get("country")),
    ]
    joined = ", ".join(p for p in parts if p)
    return f"📍 {joined}" if joined else ""


def _describe_one_contact(contact: dict) -> str:
    lines: list = []
    name = _contact_name(contact)
    lines.append(f"👤 Contato: {name}" if name else "👤 Contato")

    for entry in _list(contact.get("phones")):
        lines.append(_contact_phone_line(entry))

    for entry in _list(contact.get("emails")):
        email = _text(entry) if isinstance(entry, str) else _text(_dict(entry).get("email"))
        if email:
            lines.append(f"✉️ {email}")

    org = _dict(contact.get("org"))
    company = _text(org.get("company"))
    if company:
        lines.append(f"🏢 {company}")
    title = _text(org.get("title"))
    if title:
        lines.append(f"💼 {title}")

    for entry in _list(contact.get("addresses")):
        lines.append(_address_line(entry))

    for entry in _list(contact.get("urls")):
        url = _text(entry) if isinstance(entry, str) else _text(_dict(entry).get("url"))
        if url:
            lines.append(f"🔗 {url}")

    birthday = _text(contact.get("birthday"))
    if birthday:
        lines.append(f"🎂 {birthday}")

    return _join_lines(lines)


def describe_contacts(contacts: Any) -> str:
    """Render ``messages[].contacts[]`` — one block per contact, blank line between.

    The phone is the load-bearing field (it is what was lost in the production
    case): it always comes from ``phones[].phone`` and only falls back to
    ``wa_id`` when no ``phone`` is present.
    """
    try:
        items = _list(contacts)
        if not items and isinstance(contacts, dict):
            items = [contacts]
        blocks = []
        for entry in items:
            block = _describe_one_contact(_dict(entry))
            if block:
                blocks.append(block)
        return "\n\n".join(blocks)
    except Exception:  # noqa: BLE001 — never raise on webhook data
        return ""


# ── order ──────────────────────────────────────────────────────────────────

def _order_item_line(entry: Any) -> str:
    item = _dict(entry)
    label = _text(item.get("product_retailer_id")) or "item"
    quantity = _number(item.get("quantity"))
    price = _number(item.get("item_price"))
    currency = _text(item.get("currency"))

    detail = []
    if quantity is not None:
        qty_txt = str(int(quantity)) if float(quantity).is_integer() else _money(quantity)
        detail.append(f"{qty_txt}×")
    if price is not None:
        detail.append(f"{_money(price)} {currency}".strip())
    if detail:
        return f"• {label} — " + " ".join(detail)
    return f"• {label}"


def describe_order(order: Any) -> str:
    """Render ``messages[].order`` (catalog checkout) as a readable block."""
    try:
        data = _dict(order)
        items = _list(data.get("product_items"))

        totals: dict = {}
        for entry in items:
            item = _dict(entry)
            quantity = _number(item.get("quantity"))
            price = _number(item.get("item_price"))
            if quantity is None or price is None:
                continue
            currency = _text(item.get("currency"))
            totals[currency] = totals.get(currency, 0.0) + quantity * price

        header = "🛒 Pedido do catálogo"
        if items:
            count = len(items)
            header += f": {count} item" if count == 1 else f": {count} itens"
            if totals:
                total_txt = " + ".join(
                    f"{_money(value)} {currency}".strip()
                    for currency, value in totals.items()
                )
                header += f" — total {total_txt}"

        lines = [header]
        for entry in items:
            lines.append(_order_item_line(entry))

        note = _text(data.get("text"))
        if note:
            lines.append(f"💬 {note}")

        if not data and not items:
            return ""
        return _join_lines(lines)
    except Exception:  # noqa: BLE001
        return ""


# ── system ─────────────────────────────────────────────────────────────────

def describe_system(system: Any) -> str:
    """Render ``messages[].system``. Meta already ships a ready sentence in
    ``body`` — use it verbatim; only synthesize one when ``body`` is missing."""
    try:
        data = _dict(system)
        body = _text(data.get("body"))
        if body:
            return f"ℹ️ {body}"
        stype = _text(data.get("type"))
        wa_id = _text(data.get("wa_id"))
        if stype and wa_id:
            return f"ℹ️ Evento do sistema: {stype} ({wa_id})"
        if stype:
            return f"ℹ️ Evento do sistema: {stype}"
        if wa_id:
            return f"ℹ️ Evento do sistema ({wa_id})"
        return ""
    except Exception:  # noqa: BLE001
        return ""


# ── unsupported ────────────────────────────────────────────────────────────

def describe_unsupported(msg: Any) -> str:
    """Render an ``unsupported`` message (``unsupported.type`` + ``errors[]``).

    Note this is a NORMAL path, not an exception path: ``order``, ``location``,
    ``reaction`` and ``interactive`` can all arrive as ``unsupported`` depending
    on how the customer's number was onboarded.
    """
    try:
        data = _dict(msg)
        base = "⚠️ Mensagem não suportada pelo WhatsApp Business"
        utype = _text(_dict(data.get("unsupported")).get("type"))
        if utype:
            base += f" ({utype})"
        errors = _list(data.get("errors"))
        first = _dict(errors[0]) if errors else {}
        detail = (
            _text(first.get("title"))
            or _text(first.get("message"))
            or _text(_dict(first.get("error_data")).get("details"))
        )
        if detail:
            base += f": {detail}"
        return base
    except Exception:  # noqa: BLE001
        return ""


# ── interactive / button ───────────────────────────────────────────────────

_INTERACTIVE_SUBTYPES = ("button_reply", "list_reply", "nfm_reply")


def _describe_nfm_reply(reply: dict) -> str:
    """Flows reply. ``response_json`` is a STRING holding JSON.

    ⚠️ This shape is NOT confirmed by the official webhook docs (only by
    third-party sources), so the parse is deliberately tolerant: an unparseable
    payload degrades to the truncated raw string instead of raising.
    """
    raw = reply.get("response_json")
    raw_txt = raw if isinstance(raw, str) else ""
    parsed: Any = None
    if isinstance(raw, (dict, list)):
        parsed = raw
    elif raw_txt:
        try:
            parsed = json.loads(raw_txt)
        except (ValueError, TypeError):
            parsed = None

    if isinstance(parsed, dict) and parsed:
        pairs = []
        for key, value in parsed.items():
            if isinstance(value, (dict, list)):
                try:
                    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                except (TypeError, ValueError):
                    rendered = str(value)
            elif isinstance(value, bool):
                rendered = "sim" if value else "não"
            elif value is None:
                rendered = ""
            else:
                rendered = str(value)
            pairs.append(f"{key}={rendered}")
        if pairs:
            return "📋 Formulário respondido: " + " · ".join(pairs)

    if raw_txt:
        return "📋 Formulário respondido: " + _truncate(raw_txt.strip(), _RAW_JSON_CAP)

    fallback = _text(reply.get("body")) or _text(reply.get("name"))
    if fallback:
        return f"📋 Formulário respondido: {fallback}"
    return "📋 Formulário respondido"


def describe_interactive(inter: Any) -> str:
    """Render ``messages[].interactive`` OR ``messages[].button``.

    ⚠️ The previous implementation read ``inter.get("text")``, a key that does
    NOT exist for ``button_reply``/``list_reply`` — the latent bug this fixes.
    The ``button`` (template quick-reply) shape DOES carry ``text`` and its
    output must stay byte-identical (40 rows in production).
    """
    try:
        data = _dict(inter)
        subtype = _text(data.get("type"))
        if subtype not in _INTERACTIVE_SUBTYPES:
            subtype = ""
            for candidate in _INTERACTIVE_SUBTYPES:
                if isinstance(data.get(candidate), dict):
                    subtype = candidate
                    break

        if subtype == "button_reply":
            reply = _dict(data.get("button_reply"))
            return _text(reply.get("title")) or _text(reply.get("id"))

        if subtype == "list_reply":
            reply = _dict(data.get("list_reply"))
            title = _text(reply.get("title")) or _text(reply.get("id"))
            description = _text(reply.get("description"))
            if title and description:
                return f"{title} — {description}"
            return title or description

        if subtype == "nfm_reply":
            return _describe_nfm_reply(_dict(data.get("nfm_reply")))

        # Template quick-reply (``button``): {payload, text}. Keep verbatim.
        return _text(data.get("text"))
    except Exception:  # noqa: BLE001
        return ""


# ── dispatcher ─────────────────────────────────────────────────────────────

def describe_message(msg: Any) -> str:
    """Turn one ``messages[]`` item into readable PT-BR text.

    Returns ``""`` when this module has nothing to add (plain ``text``, media
    with its own rendering, or a brand-new Meta type) — the caller then applies
    its generic fallback.
    """
    try:
        data = _dict(msg)
        msg_type = _text(data.get("type"))

        if msg_type == "contacts":
            return describe_contacts(data.get("contacts"))
        if msg_type == "order":
            return describe_order(data.get("order"))
        if msg_type == "system":
            return describe_system(data.get("system"))
        if msg_type == "unsupported":
            return describe_unsupported(data)
        if msg_type in ("interactive", "button"):
            return describe_interactive(data.get(msg_type))
        return ""
    except Exception:  # noqa: BLE001
        return ""
