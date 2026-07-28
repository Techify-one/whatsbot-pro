"""Single-open-conversation invariant + concurrent-create race (migration 0036).

The partial unique index ``uq_atend_open_contact_inbox`` allows at most ONE open
conversation per (contact, inbox) — plano 28's double-create fix, materialized in
migration 0036. ``_create_open_atomic``'s in-transaction re-check alone does NOT
close the race on Postgres READ COMMITTED (two transactions can both see "no open
conversation" and both insert); the index is the backstop and the loser must adopt
the winner's thread via the ``IntegrityError`` catch.

The race test is deterministic, not timing-lucky, and pins THIS index (not
``uq_conv_display_id``): the winner is inserted directly with an out-of-band
``display_id`` — bypassing the display_id counter, so the loser's counter-drawn
``display_id`` cannot collide. The winner's transaction is held open; the loser
(thread) passes its re-check under MVCC, then its INSERT blocks on the partial
index conflict itself; committing the winner releases the loser straight into the
``uq_atend_open_contact_inbox`` violation. Drop the index and the loser would
sail through with ``created=True`` — the test fails.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy.exc import IntegrityError

INBOX_ID = 1  # seeded by migration 0013

# Far above anything the display_id counter hands out in a test session, so the
# winner's direct insert can never collide with counter-drawn display_ids.
_OUT_OF_BAND_DISPLAY_ID = 990_036


def _make_contact_pair(phone: str, jid: str):
    from db.repositories import contact_repo, contact_inbox_repo

    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    return contact, ci


def test_second_open_conversation_same_pair_raises(_engine_ready):
    from db.repositories import conversation_repo

    contact, ci = _make_contact_pair("5511990000101", "5511990000101@s.whatsapp.net")
    conv1 = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])

    with pytest.raises(IntegrityError):
        conversation_repo.create(
            inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])

    # Closed conversations don't count against the partial index: close the open
    # one and a new open thread for the same pair is allowed (history preserved).
    conversation_repo.set_status(conv1["id"], "closed")
    conv2 = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    assert conv2["id"] != conv1["id"]
    assert conv2["status"] == "open"


def _insert_winner_direct(conn, *, contact_id: int, contact_inbox_id: int) -> int:
    """Insert an OPEN conversation on ``conn`` WITHOUT touching the display_id
    counter (out-of-band display_id), so the only unique constraint the racing
    loser can violate is ``uq_atend_open_contact_inbox``. Returns the row id."""
    from db.tables import conversations

    now = time.time()
    result = conn.execute(conversations.insert().values(
        display_id=_OUT_OF_BAND_DISPLAY_ID, inbox_id=INBOX_ID,
        contact_id=contact_id, contact_inbox_id=contact_inbox_id,
        status="open", is_archived=0, ai_active=1, active_agent_key="",
        origin="inbound", opened_at=now, last_activity_at=now,
        custom_attributes={}, created_at=now, updated_at=now,
    ))
    return result.inserted_primary_key[0]


def test_open_race_loser_adopts_winner(_engine_ready):
    from db.engine import get_engine
    from db.repositories.conversation_repo import _create_open_atomic

    contact, ci = _make_contact_pair("5511990000102", "5511990000102@s.whatsapp.net")

    engine = get_engine()
    outcome: dict = {}

    def loser():
        try:
            outcome["result"] = _create_open_atomic(
                inbox_id=INBOX_ID, contact_id=contact["id"],
                contact_inbox_id=ci["id"], opened_at=None, origin="inbound")
        except Exception as exc:  # surfaced by the assertions below
            outcome["error"] = exc

    # Winner: insert the open conversation but HOLD the transaction open.
    conn = engine.connect()
    txn = conn.begin()
    try:
        winner_id = _insert_winner_direct(
            conn, contact_id=contact["id"], contact_inbox_id=ci["id"])

        thread = threading.Thread(target=loser, daemon=True)
        thread.start()
        # Give the loser time to pass its re-check (MVCC: it doesn't see the
        # uncommitted winner) and block on the partial-index conflict. If the
        # index were missing, the loser would finish here with created=True.
        time.sleep(1.0)
        assert thread.is_alive(), (
            "loser finished before the winner committed — it never blocked on "
            "uq_atend_open_contact_inbox (index missing or race not entered)"
        )
        txn.commit()
    except BaseException:
        txn.rollback()
        raise
    finally:
        conn.close()

    thread.join(timeout=20)
    assert not thread.is_alive(), "loser deadlocked after the winner committed"
    assert "error" not in outcome, f"loser raised instead of adopting: {outcome.get('error')!r}"

    conv, created = outcome["result"]
    assert created is False, "loser must ADOPT the winner's thread, not create"
    assert conv["id"] == winner_id
