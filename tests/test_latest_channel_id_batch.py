"""Unit tests for ``conversation_repo.latest_channel_id_by_contact`` (plano 62 F7).

The batch resolver replaces the per-contact ``channel_id_for_contact`` calls in
the background avatar sweep (server/background.py). These tests assert:

  * a contact with 2 conversations (different inboxes/channels) resolves to the
    channel of the MOST RECENT one (``last_activity_at DESC``);
  * a contact with no conversation is ABSENT from the result;
  * empty input → ``{}``;
  * the batch result is IDENTICAL to ``channel_id_for_contact`` called one-by-one
    on the same dataset (semantic equivalence proof).

Dataset: seeded once per module with a phone prefix unique to this file
(``77900620…``) and channel ids prefixed ``f7batch_`` so it never collides with
the conftest seed or other suites on the shared process DB.
"""

from __future__ import annotations

import time

import pytest

from sqlalchemy import update as sa_update


PREFIX = "77900620"
P_TWO_CONVS = PREFIX + "01"   # 2 conversations — newest wins
P_ONE_CONV = PREFIX + "02"    # 1 conversation
P_NO_CONV = PREFIX + "03"     # contact only, no conversation
CH_A = "f7batch_a"
CH_B = "f7batch_b"


def _set_last_activity(conv_id: int, ts: float) -> None:
    from db.engine import get_engine
    from db.tables import conversations
    with get_engine().begin() as conn:
        conn.execute(sa_update(conversations)
                     .where(conversations.c.id == conv_id)
                     .values(last_activity_at=ts))


@pytest.fixture(scope="module")
def dataset(_engine_ready) -> dict:
    """Seed 2 channels (each with its inbox) + 3 contacts + 3 conversations."""
    from db.repositories import (channel_repo, contact_repo, conversation_repo,
                                 inbox_repo)

    for ch in (CH_A, CH_B):
        if channel_repo.get(ch) is None:
            channel_repo.create(id=ch, provider="test", display_name=ch)
    inbox_a = inbox_repo.get_or_create_for_channel(CH_A, name="F7 A")
    inbox_b = inbox_repo.get_or_create_for_channel(CH_B, name="F7 B")

    base = time.time() - 1000.0
    data: dict = {}

    two = contact_repo.get_or_create(P_TWO_CONVS)["id"]
    one = contact_repo.get_or_create(P_ONE_CONV)["id"]
    none = contact_repo.get_or_create(P_NO_CONV)["id"]
    data.update(two=two, one=one, none=none)

    # P_TWO_CONVS: conversation on channel A (older) + channel B (newer).
    conv_a, _ = conversation_repo.resolve_for_contact_ex(
        two, f"{P_TWO_CONVS}@s.whatsapp.net", inbox_id=inbox_a["id"])
    conv_b, _ = conversation_repo.resolve_for_contact_ex(
        two, f"{P_TWO_CONVS}@s.whatsapp.net", inbox_id=inbox_b["id"])
    _set_last_activity(conv_a["id"], base + 10)   # older
    _set_last_activity(conv_b["id"], base + 500)  # NEWEST → channel B must win

    # P_ONE_CONV: single conversation on channel A.
    conv_one, _ = conversation_repo.resolve_for_contact_ex(
        one, f"{P_ONE_CONV}@s.whatsapp.net", inbox_id=inbox_a["id"])
    _set_last_activity(conv_one["id"], base + 20)

    return data


def test_two_conversations_resolves_most_recent_channel(dataset):
    from db.repositories import conversation_repo
    result = conversation_repo.latest_channel_id_by_contact([dataset["two"]])
    assert result == {dataset["two"]: CH_B}


def test_single_conversation_resolves_its_channel(dataset):
    from db.repositories import conversation_repo
    result = conversation_repo.latest_channel_id_by_contact([dataset["one"]])
    assert result == {dataset["one"]: CH_A}


def test_contact_without_conversation_is_absent(dataset):
    from db.repositories import conversation_repo
    result = conversation_repo.latest_channel_id_by_contact(
        [dataset["two"], dataset["none"]])
    assert dataset["none"] not in result
    assert result == {dataset["two"]: CH_B}


def test_empty_input_returns_empty_dict(_engine_ready):
    from db.repositories import conversation_repo
    assert conversation_repo.latest_channel_id_by_contact([]) == {}


def test_batch_matches_one_by_one_semantics(dataset):
    """The batch map must equal channel_id_for_contact called per contact —
    including the no-conversation case (one-by-one → None, batch → absent)."""
    from db.repositories import conversation_repo
    ids = [dataset["two"], dataset["one"], dataset["none"]]
    batch = conversation_repo.latest_channel_id_by_contact(ids)
    for cid in ids:
        assert batch.get(cid) == conversation_repo.channel_id_for_contact(cid)
