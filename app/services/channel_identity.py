"""Post-connection account-identity persistence + duplicate refusal (plano 32 F4).

The live half of the account-identity contract. For providers whose identity only
appears once the session is connected (GOWA's own number after the QR pairing),
there is no create-time credential to check — so a background sweep reads each
live channel's ``status()`` + ``account_identity()`` and:

  * persists ``own_phone`` / ``connected`` / ``logged_in`` / ``account_identity`` /
    ``account_identity_kind`` — but ONLY the fields that changed (no write
    amplification on every tick), and
  * if the freshly-seen identity collides with another enabled channel of the same
    provider, REFUSES it: calls ``reject_duplicate()`` (default logout — severs the
    just-paired session), records ``last_error``, flips ``logged_in`` off, and does
    NOT persist the identity (so ``by_phone`` routing never sees the collision).

Everything here is generic — no ``if provider ==``. It is also fully defensive:
a failure sweeping one channel is logged and swallowed so it never kills the loop
nor other channels (D5/D7). The partial unique index (F3) is the backstop for a
race between two simultaneous pairings — an ``IntegrityError`` takes the same
refuse path.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.exc import IntegrityError

from channels import dedup
from db.repositories import channel_repo

logger = logging.getLogger(__name__)


def sweep_channel(channel_id: str, inst: object) -> None:
    """Persist changed status/identity for one live channel; refuse a duplicate.

    Never raises — a per-channel failure must not break the sweep loop.
    """
    try:
        _sweep_channel(channel_id, inst)
    except Exception:  # noqa: BLE001
        logger.debug("identity sweep failed for channel %s", channel_id, exc_info=True)


def _sweep_channel(channel_id: str, inst: object) -> None:
    row = channel_repo.get(channel_id)
    if row is None:
        return
    provider = row.get("provider")

    try:
        st = inst.status() or {}
    except Exception:  # noqa: BLE001
        st = {}

    changes: dict = {}
    if "connected" in st:
        val = 1 if st.get("connected") else 0
        if val != (row.get("connected") or 0):
            changes["connected"] = val
    if "logged_in" in st:
        val = 1 if st.get("logged_in") else 0
        if val != (row.get("logged_in") or 0):
            changes["logged_in"] = val
    own = str(st.get("own_phone") or "").strip()
    if own and own != (row.get("own_phone") or ""):
        changes["own_phone"] = own

    try:
        identity = inst.account_identity()
    except Exception:  # noqa: BLE001
        identity = None

    if identity is not None and identity.value:
        conflict = dedup.find_conflict(provider, identity, exclude_channel_id=channel_id)
        if conflict:
            _refuse(channel_id, inst, conflict)
            return
        if (identity.value != (row.get("account_identity") or "")
                or identity.kind != (row.get("account_identity_kind") or "")):
            changes["account_identity"] = identity.value
            changes["account_identity_kind"] = identity.kind

    if not changes:
        return
    try:
        channel_repo.set_status(channel_id, **changes)
    except IntegrityError:
        # A racing pairing already claimed this identity (partial unique index).
        conflict = (identity and dedup.find_conflict(
            provider, identity, exclude_channel_id=channel_id)) or ""
        _refuse(channel_id, inst, conflict)


def _refuse(channel_id: str, inst: object, conflict_id: str) -> None:
    """Sever a channel that just paired to an already-owned account (plano 32 D7).

    Does NOT archive/disable (non-destructive — the user decides) and does NOT
    persist the duplicate identity or ``own_phone`` (keeps routing clean).
    """
    msg = (f"Esta conta já está conectada no canal {conflict_id}."
           if conflict_id else "Esta conta já está conectada em outro canal.")
    try:
        inst.reject_duplicate()
    except Exception:  # noqa: BLE001
        logger.debug("reject_duplicate failed for channel %s", channel_id, exc_info=True)
    try:
        channel_repo.set_status(channel_id, last_error=msg, logged_in=0)
    except Exception:  # noqa: BLE001
        logger.debug("refuse set_status failed for channel %s", channel_id, exc_info=True)
    logger.info("Canal %s recusado: conta duplicada (conflito com %s).",
                channel_id, conflict_id or "?")
    # Trilha de auditoria: o canal foi desconectado por decisão do SISTEMA (roda
    # no sweep de fundo, sem usuário na request) — sem esta linha o operador vê o
    # número cair sem explicação. Best-effort, nunca quebra o sweep.
    try:
        from plugins.events import emit_with_filter_sync
        emit_with_filter_sync("channel.duplicate_refused", {
            "channel_id": channel_id,
            "conflict_channel_id": conflict_id or None,
            "ts": time.time(),
            "_audit_after": {"reason": msg, "conflict_channel_id": conflict_id or None,
                             "logged_in": 0},
        })
    except Exception:  # noqa: BLE001
        logger.debug("emit channel.duplicate_refused failed", exc_info=True)
