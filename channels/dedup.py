"""Generic channel-account dedup engine (plano 32 F2).

The core half of the account-identity contract: given a provider's
:class:`~channels.base.AccountIdentity`, decide whether another channel of the
SAME provider already owns that account. Knows nothing about any specific
provider — it only compares two identities for equality and reads the persisted
``account_identity`` / ``account_identity_kind`` columns. No ``if provider ==``
lives here (nor anywhere in the core dedup path); each provider declares its own
identity via the hooks on :class:`~channels.base.Channel`.

Pure of network — the only I/O is the channel repository (DB).
"""

from __future__ import annotations

from typing import Optional

from channels.base import AccountIdentity


def same(a: Optional[AccountIdentity], b: Optional[AccountIdentity]) -> bool:
    """Whether two identities denote the same account.

    Equality is exact on both ``kind`` and (canonical) ``value``. A ``None`` or an
    empty ``value`` on either side never matches — an unknown/blank identity is
    never treated as a duplicate (avoids collapsing all not-yet-logged-in channels
    together). Different ``kind`` never matches (a ``phone`` is never an ``@lid``
    nor a ``bot_token``).
    """
    if a is None or b is None:
        return False
    if not a.value or not b.value:
        return False
    return a.kind == b.kind and a.value == b.value


def find_conflict(provider: str, identity: Optional[AccountIdentity], *,
                  exclude_channel_id: Optional[str] = None) -> Optional[str]:
    """The id of an existing channel already bound to ``identity``, or ``None``.

    Scans enabled, non-archived channels of ``provider`` (plano 32 P3: archived
    and disabled channels never count) and returns the first whose persisted
    identity :func:`same` as ``identity``. ``exclude_channel_id`` skips a row (the
    channel being created/updated/swept, so it never conflicts with itself).
    Returns ``None`` when ``identity`` is ``None``/blank or no other channel
    matches.
    """
    if identity is None or not identity.value:
        return None
    # Imported lazily to keep this module import-light (and avoid any import cycle
    # via db during early contract loading).
    from db.repositories import channel_repo

    for row in channel_repo.list_all():  # excludes archived by default
        if row.get("id") == exclude_channel_id:
            continue
        if row.get("provider") != provider:
            continue
        if not row.get("enabled"):
            continue
        value = (row.get("account_identity") or "").strip()
        kind = (row.get("account_identity_kind") or "").strip()
        if not value or not kind:
            continue
        if same(identity, AccountIdentity(kind, value)):
            return row.get("id")
    return None
