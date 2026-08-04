"""Unit contract for the DB-free synthetic channel-provider kit."""

from __future__ import annotations

import pytest

from channels.base import AccountIdentity, ChannelCapabilities, MediaLimits, SendResult
from tests.fake_provider import FakeChannel, FakeGowaClient
from tests.fakes import FakeGowaClient as OriginalFakeGowaClient


def test_configured_provider_descriptor_contact_type_and_source_id_are_isolated():
    Provider = FakeChannel.configured(
        name="ConfiguredProvider",
        provider="synthetic_cloud",
        descriptor={
            "label": "Synthetic Cloud",
            "color": "blue",
            "credential_fields": [{
                "key": "account", "label": "Account", "type": "text",
            }],
            "capabilities": {"custom_flag": True},
        },
        contact_type="whatsapp",
        source_id_builder=lambda phone, is_group: (
            f"group:{phone}" if is_group else f"user:{phone}"
        ),
        capabilities=ChannelCapabilities(qr=True, templates=True),
    )

    descriptor = Provider.provider_descriptor()
    assert Provider.__name__ == "ConfiguredProvider"
    assert descriptor == {
        "provider": "synthetic_cloud",
        "label": "Synthetic Cloud",
        "color": "blue",
        "credential_fields": [{
            "key": "account", "label": "Account", "type": "text",
        }],
        "config_fields": [],
        "capabilities": {
            "needs_qr": True, "templates": True, "custom_flag": True,
        },
        "ai_sequential_default": False,
        "contact_type": "whatsapp",
        "post_create": None,
        "form_component": None,
    }
    assert Provider.contact_type() == "whatsapp"
    assert Provider.source_id_for("123", False) == "user:123"
    assert Provider.source_id_for("456", True) == "group:456"

    # Callers may freely mutate descriptors/capabilities returned by a probe.
    descriptor["credential_fields"][0]["key"] = "mutated"
    descriptor["capabilities"]["custom_flag"] = False
    fresh = Provider.provider_descriptor()
    assert fresh["credential_fields"][0]["key"] == "account"
    assert fresh["capabilities"]["custom_flag"] is True


def test_create_time_identity_is_configurable_normalized_and_can_opt_out():
    Provider = FakeChannel.configured(
        provider="synthetic_identity",
        credential_identity_key="account",
        identity_kind="account_id",
        identity_normalizer=lambda value: str(value).strip().upper(),
    )

    assert Provider.identity_from_credentials({"account": "  abc-1  "}) == (
        AccountIdentity("account_id", "ABC-1")
    )
    assert Provider.identity_from_credentials({"account": "  "}) is None
    assert Provider.identity_from_credentials({}) is None

    NoDedup = Provider.configured(
        provider="synthetic_no_dedup",
        credential_identity_key=None,
    )
    assert NoDedup.identity_from_credentials({"account": "ABC-1"}) is None


def test_live_identity_and_duplicate_rejection_are_observable_and_disconnect():
    Provider = FakeChannel.configured(
        provider="synthetic_live",
        identity_kind="bot_id",
        identity_normalizer=lambda value: str(value).strip(),
    )
    channel = Provider("live-1", live_identity=" 9001 ")

    assert channel.account_identity() == AccountIdentity("bot_id", "9001")
    channel.reject_duplicate()

    assert channel.duplicate_rejections == 1
    assert [name for name, _ in channel.calls] == ["reject_duplicate", "logout"]
    assert channel.status() == {
        "connected": False,
        "logged_in": False,
        "needs_qr": False,
        "error": None,
    }


def test_status_and_lifecycle_are_recorded_and_return_defensive_copies():
    channel = FakeChannel(
        "status-1",
        status={"connected": False, "logged_in": False, "error": "offline"},
    )

    first = channel.status()
    first["connected"] = True
    assert channel.status()["connected"] is False
    assert channel.status_calls == 2

    assert channel.reconnect() == {"ok": True}
    assert channel.status()["connected"] is True
    channel.stop()
    assert channel.status()["logged_in"] is False
    channel.start()
    assert channel.status()["error"] is None
    assert [name for name, _ in channel.calls] == ["reconnect", "stop", "start"]


def test_capabilities_media_limits_and_session_windows_are_per_instance():
    image_limits = MediaLimits(
        max_bytes=5 * 1024 * 1024,
        extensions=(".jpg", ".png"),
    )
    defaults = ChannelCapabilities(
        templates=True,
        media=True,
        session_window_hours=24,
        human_window_hours=168,
        media_limits={"image": image_limits},
    )
    Provider = FakeChannel.configured(
        provider="synthetic_windowed",
        capabilities=defaults,
    )

    inherited = Provider("window-1")
    overridden = Provider(
        "window-2",
        media_limits={"document": MediaLimits(max_bytes=100)},
        session_window_hours=12,
        human_window_hours=48,
    )

    assert inherited.capabilities.session_window_hours == 24
    assert inherited.capabilities.human_window_hours == 168
    assert inherited.capabilities.media_limits == {"image": image_limits}
    assert overridden.capabilities.session_window_hours == 12
    assert overridden.capabilities.human_window_hours == 48
    assert overridden.capabilities.media_limits == {
        "document": MediaLimits(max_bytes=100),
    }

    # Neither an instance override nor later mutation leaks to another channel.
    overridden.capabilities.templates = False
    assert inherited.capabilities.templates is True
    assert Provider("window-3").capabilities.templates is True


def test_text_media_edit_and_optional_outbound_calls_are_recorded():
    channel = FakeChannel(
        "send-1",
        send_outcomes={
            "media": SendResult(ok=False, error="media_refused"),
            "edit": SendResult(ok=True, external_msg_id="edited-7"),
        },
        external_msg_id_prefix="msg",
    )

    text_result = channel.send_text(
        "chat-1", "hello", reply_to="quoted-1", mentions=["123"],
    )
    media_result = channel.send_media(
        "chat-1", "image", "/tmp/image.jpg", caption="caption", filename="x.jpg",
    )
    edit_result = channel.edit_text("chat-1", "msg-1", "changed")
    channel.mark_read("chat-1", "msg-1")
    channel.send_presence("chat-1", "composing")
    channel.react("chat-1", "msg-1", "👍")
    channel.revoke("chat-1", "msg-1")

    assert text_result == SendResult(ok=True, external_msg_id="msg_1")
    assert media_result == SendResult(ok=False, error="media_refused")
    assert edit_result == SendResult(ok=True, external_msg_id="edited-7")
    assert channel.sent == [
        ("text", {
            "chat_id": "chat-1", "text": "hello", "reply_to": "quoted-1",
            "mentions": ["123"],
        }),
        ("media", {
            "chat_id": "chat-1", "kind": "image",
            "path_or_url": "/tmp/image.jpg", "caption": "caption",
            "filename": "x.jpg",
        }),
        ("edit", {"chat_id": "chat-1", "msg_id": "msg-1", "text": "changed"}),
    ]
    assert channel.calls == [
        ("mark_read", {"chat_id": "chat-1", "msg_id": "msg-1"}),
        ("presence", {"chat_id": "chat-1", "state": "composing"}),
        ("reaction", {"chat_id": "chat-1", "msg_id": "msg-1", "emoji": "👍"}),
        ("revoke", {"chat_id": "chat-1", "msg_id": "msg-1"}),
    ]


def test_parse_message_supports_common_aliases_and_enrichment_fields():
    Provider = FakeChannel.configured(provider="synthetic_inbound")
    channel = Provider("inbound-1")

    [event] = channel.parse_inbound({
        "id": "in-1",
        "from": "chat-9",
        "from_name": "Customer",
        "body": "hello",
        "timestamp": 123.5,
        "is_group": True,
        "media_type": "image",
        "media_path": "/media/a.jpg",
        "media_extras": {"caption": "photo"},
        "display_text": "[Customer]: hello",
        "trigger_ai": False,
        "reply_to_msg_id": "quoted-9",
        "mentioned": True,
        "group_name": "Group",
        "can_send": True,
        "is_archived": False,
    })

    assert event.channel_id == "inbound-1"
    assert event.provider == "synthetic_inbound"
    assert event.kind == "message"
    assert event.external_msg_id == "in-1"
    assert event.chat_id == event.sender_id == "chat-9"
    assert event.sender_name == "Customer"
    assert event.text == "hello"
    assert event.ts == 123.5
    assert event.media_type == "image"
    assert event.media_path == "/media/a.jpg"
    assert event.media_extras == {"caption": "photo"}
    assert event.display_text == "[Customer]: hello"
    assert event.trigger_ai is False
    assert event.reply_to_msg_id == "quoted-9"
    assert event.mentioned is True
    assert event.group_name == "Group"
    assert event.can_send is True
    assert event.is_archived is False


@pytest.mark.parametrize(
    ("payload", "expected_extras"),
    [
        (
            {
                "kind": "system", "chat_id": "c1", "body": "number changed",
                "system_type": "user_changed_number", "wa_id": "55110000",
            },
            {
                "system_type": "user_changed_number", "wa_id": "55110000",
                "body": "number changed",
            },
        ),
        (
            {
                "kind": "receipt", "chat_id": "c1", "msg_id": "m1",
                "status": "failed", "errors": [{"code": "131047"}],
                "msg_ids": ["m1"],
            },
            {
                "status": "failed", "errors": [{"code": "131047"}],
                "msg_ids": ["m1"],
            },
        ),
        (
            {
                "kind": "reaction", "chat_id": "c1", "id": "r1",
                "emoji": "🔥", "reacted_message_id": "m1", "is_from_me": True,
            },
            {"emoji": "🔥", "reacted_message_id": "m1", "is_from_me": True},
        ),
        (
            {
                "kind": "edited", "chat_id": "c1", "id": "e1", "text": "new",
                "original_message_id": "m1", "is_from_me": False,
            },
            {"original_message_id": "m1", "is_from_me": False},
        ),
    ],
)
def test_parse_non_message_kinds_promotes_dispatch_extras(payload, expected_extras):
    [event] = FakeChannel("inbound-2").parse_inbound(payload)
    assert event.kind == payload["kind"]
    assert event.media_extras == expected_extras


def test_parse_inbound_supports_batches_and_rejects_invalid_synthetic_wire():
    channel = FakeChannel("inbound-3")
    events = channel.parse_inbound({
        "events": [
            {"kind": "message", "id": "m1", "from": "c1", "text": "one"},
            {"kind": "edited", "id": "e1", "from": "c1", "text": "two"},
        ],
    })
    assert [(event.kind, event.external_msg_id) for event in events] == [
        ("message", "m1"), ("edited", "e1"),
    ]

    with pytest.raises(ValueError, match="unsupported fake inbound kind 'status'"):
        channel.parse_inbound({"kind": "status"})
    with pytest.raises(TypeError, match="payload must be a mapping"):
        channel.parse_inbound([])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="events.*list or tuple"):
        channel.parse_inbound({"events": {"kind": "message"}})


def test_fake_gowa_client_is_reexported_without_becoming_a_channel():
    assert FakeGowaClient is OriginalFakeGowaClient
    client = FakeGowaClient()
    response = client.send_message("55110000", "hello")

    assert not isinstance(client, FakeChannel)
    assert response == {"results": {"message_id": "FAKE_SENT"}}
    assert client.sent == [("message", {
        "phone": "55110000",
        "text": "hello",
        "mentions": None,
        "reply_message_id": None,
    })]
