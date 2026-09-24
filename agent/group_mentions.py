"""Group @mention resolution service.

Central place that knows a group's participants and turns mentions back and
forth between the WhatsApp wire format (``@<number>``) and human names:

- Incoming: replace ``@<digits>`` in a received group message with ``@<Name>``
  so the panel/LLM see names instead of raw numbers.
- Outgoing: turn ``@Name`` / ``@todos`` (written by the operator or the AI)
  into a real mention — inline ``@<phone>`` in the text plus the ``mentions``
  list that GOWA's ``/send/message`` accepts.

Names are not provided by GOWA (``DisplayName`` comes back empty), so they are
resolved from saved contacts (``contact_repo``) with a best-effort in-memory
fallback of pushNames captured from incoming group messages. Groups can use
``lid`` addressing, so every participant is indexed by BOTH its phone digits
and its lid digits.
"""

from __future__ import annotations

import logging
import re
import time

from db.repositories import contact_repo

logger = logging.getLogger(__name__)

# Injected at server startup via init().
_client = None

# Bot identity (injected from AppState once GOWA is logged in / on config change),
# so a mention of the bot itself resolves to its configured panel name.
_bot_phone = ""
_bot_name = ""

# group_jid -> (fetched_at, list[{phone, lid, name, is_admin}])
_members_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 300.0

# Device contact store (GOWA /user/my/contacts): digits -> saved name. Fetched
# in one bulk call and cached, so a participant saved in the phone's address book
# is named even if they never messaged in the group. Keyed by client identity —
# see _store_map().
_store_cache: dict[int, tuple[float, dict[str, str]]] = {}
_STORE_TTL = 300.0

# digits (phone or lid) -> pushName captured from incoming group messages
# or fetched on-demand from GOWA's /user/info (the WhatsApp "default name").
_pushname_cache: dict[str, str] = {}

# digit keys already queried via /user/info that came back empty — avoids
# re-hitting GOWA for participants that simply have no public push name.
_pushname_attempted: set[str] = set()

# lid digits -> phone digits, learned from every roster we normalize. A ``leave``
# notice names the participant AFTER apply_participants_change already dropped them
# from the roster, and a lid-addressed group reports them by lid — this is the only
# place their phone survives, so the notice can still link to "Novo contato".
_lid_phone: dict[str, str] = {}

# Max /user/info lookups per get_members(resolve_names=True) call, so opening a
# large group doesn't block on hundreds of sequential HTTP calls. Remaining
# nameless members are resolved on subsequent calls / cache refreshes.
_NAME_FETCH_CAP = 20

# Keywords that mean "mention everyone" (mapped to GOWA's "@everyone").
_ALL_RE = re.compile(r"(?<![\w@])@(todos|todes|all|everyone|geral)\b", re.IGNORECASE)
# A raw numeric mention in message text, e.g. "@5511999999999".
_NUM_RE = re.compile(r"@(\d{5,})")


def init(client) -> None:
    """Wire the DEFAULT GOWA client and discard device-scoped caches on replacement.

    ``client`` is the fallback used by every call that doesn't pass its own
    ``client=`` (plano multi-canal) — i.e. every caller that predates
    per-channel resolution keeps working exactly as before, against this one.
    """
    global _client, _bot_phone, _bot_name
    if client is not _client:
        _members_cache.clear()
        _store_cache.clear()
        _pushname_cache.clear()
        _pushname_attempted.clear()
        _lid_phone.clear()
        _bot_phone = ""
        _bot_name = ""
    _client = client


def clear_cache() -> None:
    """Drop the members cache (call when bot name/phone or contacts change)."""
    _members_cache.clear()
    _store_cache.clear()


def invalidate(group_jid: str) -> None:
    """Drop one group's cached members so the next lookup re-fetches from GOWA.

    Called when GOWA reports a participant change (join/leave/promote) so a
    just-joined member becomes mentionable immediately instead of after the TTL.
    """
    if group_jid:
        _members_cache.pop(group_jid, None)


def set_bot_identity(phone: str, name: str) -> None:
    """Register the bot's own phone + configured panel name (mentions of the bot)."""
    global _bot_phone, _bot_name
    _bot_phone = _digits(phone or "")
    _bot_name = (name or "").strip()
    _members_cache.clear()


def _digits(value: str) -> str:
    """Extract the bare numeric id from a JID-ish string ('551199@s.w...' -> '551199')."""
    if not value:
        return ""
    head = value.split("@")[0].split(":")[0]
    return "".join(ch for ch in head if ch.isdigit())


def _store_map(client=None) -> dict[str, str]:
    """Cached digits->name map from the device's WhatsApp contact store.

    Keyed by client identity (``id(eff)``), not a single global slot — a
    multi-channel install has ONE address book PER GOWA NUMBER, and a shared
    cache would leak channel A's contacts into channel B's name resolution.
    """
    eff = client if client is not None else _client
    if eff is None:
        return {}
    key = id(eff)
    now = time.time()
    cached = _store_cache.get(key)
    if cached and (now - cached[0]) < _STORE_TTL:
        return cached[1]
    mapping: dict[str, str] = {}
    try:
        for it in eff.get_wa_contacts():
            d = _digits(it.get("jid", "") or "")
            nm = (it.get("name") or "").strip()
            if d and nm:
                mapping[d] = nm
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[mentions] contact store fetch failed: %s", e)
        return cached[1] if cached else {}
    _store_cache[key] = (now, mapping)
    return mapping


def _resolve_name(phone: str, lid: str = "", client=None) -> str:
    """Best known name for a participant: saved contact > captured pushName >
    device address book (FullName). Empty if none is known (caller falls back
    to the number or a /user/info lookup).

    ``client``: o GOWAClient do CANAL da conversa (plano multi-canal) — sem
    ele, cai no cliente global ``_client`` (comportamento de sempre)."""
    return (_saved_name(phone)
            or _pushname_cache.get(phone) or _pushname_cache.get(lid)
            or _store_map(client).get(phone) or _store_map(client).get(lid)
            or "")


def _saved_name(phone: str) -> str:
    """Return the saved contact name for a phone, or '' if none."""
    if not phone:
        return ""
    try:
        row = contact_repo.get_by_phone(phone)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[mentions] contact lookup failed for %s: %s", phone, e)
        return ""
    if row and row.get("name"):
        return row["name"].lstrip("~").strip()
    return ""


def record_pushname(keys: list[str], name: str) -> None:
    """Remember a pushName for the given digit keys (phone and/or lid).

    Invalidates the members cache so the next resolution picks the name up.
    """
    name = (name or "").strip()
    if not name:
        return
    changed = False
    for key in keys:
        d = _digits(key)
        if d and _pushname_cache.get(d) != name:
            _pushname_cache[d] = name
            changed = True
    if changed:
        _members_cache.clear()


def _fetch_pushname(jid: str, client=None) -> str:
    """Best-effort WhatsApp push name ("default name") for a JID via /user/info.

    Caches hits in _pushname_cache and records misses in _pushname_attempted so
    each participant is queried at most once. Blocking (HTTP). ``client``: ver
    ``_resolve_name``.
    """
    eff = client if client is not None else _client
    if eff is None or not jid:
        return ""
    d = _digits(jid)
    if not d:
        return ""
    if _pushname_cache.get(d):
        return _pushname_cache[d]
    if d in _pushname_attempted:
        return ""
    _pushname_attempted.add(d)
    try:
        info = eff._get_user_info(jid if "@" in jid else f"{d}@s.whatsapp.net")
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[mentions] user info lookup failed for %s: %s", d, e)
        return ""
    name = ((info or {}).get("name") or "").strip()
    if name:
        _pushname_cache[d] = name
    return name


def _resolve_missing_names(members: list[dict], client=None) -> None:
    """Fill in push names for nameless members (mutates in place, bounded).

    A just-joined member has no saved contact and no captured pushName yet, so
    without this they'd render as a bare phone number in the @mention menu.
    ``client``: ver ``_resolve_name``.
    """
    budget = _NAME_FETCH_CAP
    for m in members:
        if m["name"] or budget <= 0:
            continue
        # Cheap sources first (saved contact / pushName cache / device store).
        name = _resolve_name(m["phone"], m["lid"], client)
        if name:
            m["name"] = name
            continue
        key = m["phone"] or m["lid"]
        if not key or key in _pushname_attempted:
            continue
        # Last resort: /user/info (only business accounts return a name here).
        jid = f"{m['phone']}@s.whatsapp.net" if m["phone"] else f"{m['lid']}@lid"
        name = _fetch_pushname(jid, client)
        budget -= 1
        if name:
            m["name"] = name


def _display_name(jid: str) -> str:
    """Best display name for a single participant JID (saved > pushName > number)."""
    d = _digits(jid)
    if not d:
        return ""
    name = _resolve_name(d)
    if not name and "@lid" in jid:
        # A lid-addressed participant may have a saved contact under their REAL
        # phone, which only a roster can tell us (``_lid_phone``).
        phone = _lid_phone.get(d, "")
        if phone:
            name = _resolve_name(phone, d)
    return name or _fetch_pushname(jid) or f"+{d}"


def _member_phone(jid: str) -> str:
    """Real phone digits of a participant JID, or '' when there isn't one.

    A ``@lid`` JID carries an opaque id, NOT a phone — it is only trusted once a
    roster told us the phone behind it (``_lid_phone``). Anything outside E.164
    length (10-15 digits) is rejected: "Novo contato" would prepend a country code
    to it and validate a number that doesn't exist.
    """
    d = _digits(jid)
    if not d:
        return ""
    phone = _lid_phone.get(d, "") if "@lid" in (jid or "") else d
    return phone if 10 <= len(phone) <= 15 else ""


def _member_mark(jid: str) -> str:
    """One participant as it appears inside a roster notice.

    ``[[member:<phone>|<name>]]`` when the phone is known — the panel turns it into
    a link to "Novo contato" (web/static/js/services/systemMemberLinks.js parses the
    SAME token; keep both in sync). Plain name otherwise. ``[``/``]`` are stripped
    from the name so it can never close the token early.
    """
    name = _display_name(jid)
    if not name:
        return ""
    phone = _member_phone(jid)
    if not phone:
        return name
    if name == f"+{_digits(jid)}":
        # Nameless: _display_name fell back to the JID digits, which for a lid JID is
        # the opaque id — show the phone the roster resolved instead.
        name = f"+{phone}"
    return f"[[member:{phone}|{name.replace('[', '').replace(']', '')}]]"


def describe_change(change_type: str, jids: list[str]) -> str:
    """Human-readable PT-BR notice for a roster change (for the chat timeline).

    e.g. 'João entrou no grupo' / 'Maria saiu do grupo'. Returns '' for changes
    we don't surface or when no participant could be named.

    Each participant whose phone is known is emitted as a ``[[member:<phone>|<name>]]``
    token (see ``_member_mark``) — the notice is panel-only (``system_notice``), so
    the token never reaches WhatsApp, the LLM context, the preview or the search.
    """
    change = (change_type or "").lower()
    names = [n for n in (_member_mark(j) for j in (jids or [])) if n]
    if not names:
        return ""
    who = ", ".join(names)
    plural = len(names) > 1
    if change == "join":
        return f"{who} {'entraram' if plural else 'entrou'} no grupo"
    if change == "leave":
        return f"{who} {'saíram' if plural else 'saiu'} do grupo"
    if change == "promote":
        return f"{who} {'agora são administradores' if plural else 'agora é administrador(a)'}"
    if change == "demote":
        return f"{who} {'não são mais administradores' if plural else 'não é mais administrador(a)'}"
    return ""


def get_members(group_jid: str, force: bool = False,
                resolve_names: bool = False, *, client=None) -> list[dict]:
    """Return normalized participants: [{phone, lid, name, is_admin}].

    Blocking (HTTP + DB) — callers in async context should use asyncio.to_thread.
    Cached per group for _CACHE_TTL seconds. ``resolve_names`` additionally hits
    GOWA's /user/info to fill push names for members with no saved contact — used
    by the @mention autocomplete, NOT by the hot message-send path (kept cheap).

    ``client`` (plano multi-canal): o GOWAClient do CANAL dono do grupo. Um
    install com mais de um número GOWA conectado tem um ``_client`` GLOBAL
    (o wired em ``init()`` no boot, "o padrão") que não é necessariamente o
    número que está de fato NAQUELE grupo — perguntar ao cliente errado dá
    ``get_group_info`` vazio/401 em silêncio (capturado abaixo) e o roster
    volta `[]`, sem erro nenhum pro chamador perceber. Passe o cliente do
    canal da conversa sempre que ele for conhecido; omitido, cai no global
    (mesmo comportamento de sempre — retrocompatível para quem ainda não
    tem o ``channel_id`` à mão).
    """
    eff = client if client is not None else _client
    if not group_jid or eff is None:
        return []
    now = time.time()
    cached = _members_cache.get(group_jid)
    if cached and not force and (now - cached[0]) < _CACHE_TTL:
        if resolve_names:
            _resolve_missing_names(cached[1], client)
        return cached[1]

    info = None
    try:
        info = eff.get_group_info(group_jid)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[mentions] get_group_info failed for %s: %s", group_jid, e)
    if not info or not isinstance(info, dict):
        return cached[1] if cached else []

    members: list[dict] = []
    for p in info.get("Participants", []) or []:
        phone = _digits(p.get("PhoneNumber", "") or "")
        lid = _digits(p.get("LID", "") or p.get("JID", "") or "")
        if phone and lid and phone != lid:
            _lid_phone[lid] = phone
        name = _resolve_name(phone, lid, client)
        # The bot is a participant too; resolve its mention via the configured
        # panel name (GOWA gives no DisplayName, and the bot has no saved contact).
        if not name and _bot_name and _bot_phone and phone == _bot_phone:
            name = _bot_name
        members.append({
            "phone": phone,
            "lid": lid,
            "name": name,
            "is_admin": bool(p.get("IsAdmin") or p.get("IsSuperAdmin")),
        })
    if resolve_names:
        _resolve_missing_names(members, client)
    _members_cache[group_jid] = (now, members)
    return members


def apply_participants_change(group_jid: str, change_type: str,
                             jids: list[str]) -> list[dict]:
    """Apply a roster delta (GOWA ``group.participants`` webhook) and return the
    updated member list.

    Done locally rather than via an immediate /group/info refetch because GOWA's
    group store often hasn't caught up the instant the webhook fires — refetching
    then would re-add a just-removed member (cached for the full TTL). ``type`` is
    one of join/leave/promote/demote; ``jids`` are the affected participants.
    """
    if not group_jid or _client is None:
        return []
    change = (change_type or "").lower()
    keys = {_digits(j) for j in (jids or []) if _digits(j)}
    members = list(get_members(group_jid, resolve_names=True))

    if change == "leave" and keys:
        members = [m for m in members
                   if m["phone"] not in keys and m["lid"] not in keys]
    elif change == "join" and keys:
        present = {m["phone"] for m in members if m["phone"]} \
            | {m["lid"] for m in members if m["lid"]}
        for j in (jids or []):
            d = _digits(j)
            if not d or d in present:
                continue
            is_lid = "@lid" in j
            phone = "" if is_lid else d
            lid = d if is_lid else ""
            name = _resolve_name(phone, lid) or _fetch_pushname(j)
            members.append({"phone": phone, "lid": lid, "name": name,
                            "is_admin": False})
    else:
        # promote/demote (or no usable jids): re-fetch so admin flags refresh.
        invalidate(group_jid)
        return get_members(group_jid, force=True, resolve_names=True)

    _members_cache[group_jid] = (time.time(), members)
    return members


def build_lookup(group_jid: str) -> dict[str, str]:
    """Map every participant's phone-digits AND lid-digits to a display name."""
    lookup: dict[str, str] = {}
    for m in get_members(group_jid):
        if not m["name"]:
            continue
        if m["phone"]:
            lookup[m["phone"]] = m["name"]
        if m["lid"]:
            lookup[m["lid"]] = m["name"]
    return lookup


def apply_incoming(lookup: dict[str, str], text: str) -> str:
    """Pure (no I/O) version of resolve_incoming given a prebuilt lookup.

    Safe to call inline in an async context.
    """
    if not text or "@" not in text or not lookup:
        return text

    def _repl(match: re.Match) -> str:
        name = lookup.get(match.group(1))
        return f"@{name}" if name else match.group(0)

    return _NUM_RE.sub(_repl, text)


def resolve_incoming(group_jid: str, text: str) -> str:
    """Replace raw ``@<digits>`` mentions in a received message with ``@<Name>``."""
    if not text or "@" not in text:
        return text
    return apply_incoming(build_lookup(group_jid), text)


def resolve_outgoing(group_jid: str, text: str) -> tuple[str, list[str]]:
    """Turn ``@Name`` / ``@todos`` / ``@<digits>`` into a real mention.

    Returns (text_to_send, mentions) where ``mentions`` is the list of phone
    numbers (and possibly ``"@everyone"``) for GOWA's ``/send/message``.
    Unmatched ``@`` tokens are left untouched.
    """
    if not text or "@" not in text:
        return text, []
    members = get_members(group_jid)
    mentions: list[str] = []

    # Mention-all keywords. A literal "@everyone" in the mentions list does NOT
    # tag/notify anyone: GOWA/WhatsApp only notify the participants whose phone
    # JIDs are in the mentions array (same mechanism the individual mentions below
    # rely on), and "@everyone" is not a JID. So expand it ourselves — every
    # participant's phone goes into the mentions list, while the visible text keeps
    # a single clean "@all" token. The bot itself is excluded (no self-ping).
    if _ALL_RE.search(text):
        everyone = [m["phone"] for m in members
                    if m["phone"] and m["phone"] != _bot_phone]
        if everyone:
            for p in everyone:
                if p not in mentions:
                    mentions.append(p)
        else:
            # Members unavailable (GOWA fetch failed) — fall back to the literal so
            # behaviour doesn't regress to silently sending no mention at all.
            mentions.append("@everyone")
            logger.warning("[mentions] @all for %s but no resolvable members; "
                           "sent literal @everyone as fallback", group_jid)
        text = _ALL_RE.sub("@all", text)

    # Name mentions: match the full known name (longest first to avoid a short
    # name shadowing a longer one), case-insensitive, rewrite to inline @<phone>.
    named = sorted((m for m in members if m["name"] and m["phone"]),
                   key=lambda m: len(m["name"]), reverse=True)
    for m in named:
        pattern = re.compile(re.escape(f"@{m['name']}"), re.IGNORECASE)
        if pattern.search(text):
            text = pattern.sub(f"@{m['phone']}", text)
            if m["phone"] not in mentions:
                mentions.append(m["phone"])

    # Raw numeric mentions: normalize lid/phone digits to the canonical phone.
    def _repl(match: re.Match) -> str:
        d = match.group(1)
        for m in members:
            if m["phone"] and d in (m["phone"], m["lid"]):
                if m["phone"] not in mentions:
                    mentions.append(m["phone"])
                return f"@{m['phone']}"
        return match.group(0)

    text = _NUM_RE.sub(_repl, text)
    return text, mentions
