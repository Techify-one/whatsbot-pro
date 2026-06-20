"""WhatsApp JID classification (GOWA channel inbound filtering).

The *type* of a WhatsApp chat is decided by the JID **suffix** (the part after
``@``), never by the number itself: the numeric prefix ``120363…`` is shared by
groups, channels and communities, so only the suffix disambiguates.

Used by the GOWA webhook to decide which chat types surface as conversations in
the panel (configurable per GOWA channel via ``config.allowed_jid_types``). The
default drops ``newsletter``/``broadcast``/``bot`` so a channel post never
materializes as a phantom "person" contact.
"""

from __future__ import annotations

# Logical chat types (stable identifiers — persisted in channel config and the
# frontend; do NOT rename).
PERSON = "person"            # @s.whatsapp.net — a saved/normal contact
PERSON_LID = "person_lid"    # @lid — a person in privacy mode
GROUP = "group"              # @g.us — group OR community (suffix can't separate)
NEWSLETTER = "newsletter"    # @newsletter — a WhatsApp Channel
BROADCAST = "broadcast"      # @broadcast — Status / broadcast list
BOT = "bot"                  # @bot — Meta AI and other bots
UNKNOWN = "unknown"          # anything else / no suffix

# All configurable types, in display order. ``unknown`` is intentionally absent:
# it is never gated (treated as allowed) so unexpected suffixes keep their
# previous "person-like" behaviour instead of being silently dropped.
ALL_JID_TYPES = [PERSON, PERSON_LID, GROUP, NEWSLETTER, BROADCAST, BOT]

# Default: surface people and groups; drop channels/status/bots (fixes the
# phantom-contact bug where every non-@g.us JID fell into the "person" branch).
DEFAULT_ALLOWED_JID_TYPES = [PERSON, PERSON_LID, GROUP]

# PT-BR labels for the configuration UI / API.
JID_TYPE_LABELS = {
    PERSON: "Pessoa (contato)",
    PERSON_LID: "Pessoa (modo privacidade)",
    GROUP: "Grupo / Comunidade",
    NEWSLETTER: "Canal",
    BROADCAST: "Status / Transmissão",
    BOT: "Bot (Meta AI etc.)",
}

# Suffix (lowercase, sans ``@``) -> logical type. ``c.us`` is a legacy alias for
# a person JID that some payloads still use.
_SUFFIX_TO_TYPE = {
    "s.whatsapp.net": PERSON,
    "c.us": PERSON,
    "lid": PERSON_LID,
    "g.us": GROUP,
    "newsletter": NEWSLETTER,
    "broadcast": BROADCAST,
    "bot": BOT,
}


def classify_jid(jid: str) -> str:
    """Return the logical chat type for a WhatsApp JID by its suffix.

    Unknown/empty/suffix-less inputs return :data:`UNKNOWN`.
    """
    if not isinstance(jid, str) or "@" not in jid:
        return UNKNOWN
    suffix = jid.rsplit("@", 1)[1].strip().lower()
    return _SUFFIX_TO_TYPE.get(suffix, UNKNOWN)


def normalize_allowed_types(value) -> list[str]:
    """Coerce a raw config value into a clean list of known JID types.

    Keeps only recognized types (preserving :data:`ALL_JID_TYPES` order),
    drops duplicates/unknown entries, and falls back to
    :data:`DEFAULT_ALLOWED_JID_TYPES` when the input isn't a usable list.
    """
    if not isinstance(value, (list, tuple, set)):
        return list(DEFAULT_ALLOWED_JID_TYPES)
    wanted = {str(v) for v in value}
    cleaned = [t for t in ALL_JID_TYPES if t in wanted]
    return cleaned or list(DEFAULT_ALLOWED_JID_TYPES)


def is_allowed(jid_type: str, allowed: list[str]) -> bool:
    """Whether a classified type should be surfaced given the allowed set.

    Only *known* types are gated; :data:`UNKNOWN` (and any type not in
    :data:`ALL_JID_TYPES`) always passes, preserving legacy behaviour for
    unexpected suffixes.
    """
    if jid_type not in ALL_JID_TYPES:
        return True
    return jid_type in allowed
