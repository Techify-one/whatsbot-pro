"""Unit tests for the plano 57 authoritative post-save broadcast builder.

Pure — no DB, no network. Run individually:
    venv/bin/python -m pytest tests/core/test_realtime_broadcast.py -q
"""

from app.services.realtime_broadcast import build_inbound_saved_message


def _saved(**over):
    row = {
        "id": 1093,
        "role": "user",
        "content": "Oii",
        "ts": 1784309931.27,
        "msg_id": "3A52F253A44308C472B0",
        "conversation_id": 139,
        "media_type": None,
        "media_path": None,
        "reply_to_msg_id": None,
    }
    row.update(over)
    return row


def test_carries_persisted_identity():
    msg = build_inbound_saved_message(_saved())
    assert msg["role"] == "user"
    assert msg["content"] == "Oii"
    assert msg["ts"] == 1784309931.27
    assert msg["msg_id"] == "3A52F253A44308C472B0"
    assert msg["_id"] == 1093            # DB id → _id (frontend reconciles by it)
    assert msg["conversation_id"] == 139
    assert msg["authoritative"] is True
    assert "supersedes" not in msg       # single message → no supersedes


def test_supersedes_excludes_own_msg_id():
    # A batch joined 3 messages into ONE row under the LAST msg_id ("C").
    msg = build_inbound_saved_message(
        _saved(msg_id="C", content="a\nb\nc"),
        supersedes=["A", "B", "C"],
    )
    # Only the EARLIER msg_ids are superseded; the row's own id reconciles in place.
    assert msg["supersedes"] == ["A", "B"]


def test_supersedes_single_message_omitted():
    # supersedes == [own] → nothing extra → key omitted (byte-clean payload).
    msg = build_inbound_saved_message(_saved(msg_id="A"), supersedes=["A"])
    assert "supersedes" not in msg


def test_supersedes_filters_empty_values():
    msg = build_inbound_saved_message(_saved(msg_id="C"), supersedes=[None, "", "A"])
    assert msg["supersedes"] == ["A"]


def test_media_fields_included():
    msg = build_inbound_saved_message(
        _saved(media_type="image", media_path="statics/x.jpg", content=""))
    assert msg["media_type"] == "image"
    assert msg["media_path"] == "statics/x.jpg"


def test_no_conversation_id_omitted():
    msg = build_inbound_saved_message(_saved(conversation_id=None))
    assert "conversation_id" not in msg


def test_reply_to_carried_when_present():
    msg = build_inbound_saved_message(_saved(reply_to_msg_id="QUOTED"))
    assert msg["reply_to_msg_id"] == "QUOTED"
    # absent by default
    assert "reply_to_msg_id" not in build_inbound_saved_message(_saved())
