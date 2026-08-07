"""Plano 75 F3 — safety net: the core never persists a mute bubble.

A provider (any provider, present or future) may hand the funnel a message whose
``text`` is empty, that carries no file and whose ``media_type`` the panel cannot
draw — the real case being the WhatsApp Cloud ``type: "contacts"`` card, which
used to be saved as ``content=''`` and render as a completely blank bubble.

This suite pins the two halves of the fix:

* the pure helper :func:`server.transcription.placeholder_for_unrenderable` —
  including the R5 regression guards (a caption-less image/audio/video/sticker/
  document/location is LEGITIMATE and must never get a placeholder);
* the end-to-end funnel — an ``InboundEvent`` with an invented ``media_type``
  goes through the real ingest + batch pipeline and comes out of the DB with the
  PT-BR placeholder, and the optimistic t=0 broadcast already carries the very
  same text (so the bubble never flashes empty before a reload).

The integration test drives ``deps.ingest_event`` directly with a synthetic
event instead of a provider webhook on purpose: F3 is the defense that must work
for ANY provider *and* with an outdated channel plugin installed, so the test
must not depend on any plugin's parser.
"""

from __future__ import annotations

import asyncio

import pytest

from server.transcription import (
    RENDERABLE_MEDIA_TYPES,
    placeholder_for_unrenderable,
)


# ── Pure helper ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("media_type", ["contacts", "order", "system",
                                        "unsupported", "xyz_inventado"])
def test_unrenderable_type_without_body_gets_placeholder(media_type):
    """Empty text + no file + type the panel cannot draw ⇒ readable placeholder."""
    assert (placeholder_for_unrenderable("", media_type, None)
            == f'[Mensagem do tipo "{media_type}" não suportada]')
    # empty-string media_path is as absent as None
    assert (placeholder_for_unrenderable("", media_type, "")
            == f'[Mensagem do tipo "{media_type}" não suportada]')
    # a None text behaves like an empty one
    assert (placeholder_for_unrenderable(None, media_type, None)
            == f'[Mensagem do tipo "{media_type}" não suportada]')


@pytest.mark.parametrize("media_type", sorted(RENDERABLE_MEDIA_TYPES))
def test_renderable_media_without_caption_is_untouched(media_type):
    """R5 — real media with no caption keeps its empty text: the bubble already
    shows the image/player/link, a placeholder there would be a regression."""
    assert placeholder_for_unrenderable("", media_type, "statics/media/x.bin") == ""


def test_renderable_media_list_matches_the_panel():
    """The list must mirror the branches of MediaContent.js (45-121)."""
    assert RENDERABLE_MEDIA_TYPES == frozenset({
        "image", "audio", "video", "sticker", "document",
        "location", "live_location",
    })


def test_existing_text_is_never_replaced():
    assert placeholder_for_unrenderable("olá", "contacts", None) == "olá"
    assert placeholder_for_unrenderable("[Áudio recebido]", "audio", None) == "[Áudio recebido]"
    assert placeholder_for_unrenderable("legenda", "image", "statics/media/a.jpg") == "legenda"


def test_unknown_type_with_a_file_is_untouched():
    """A file exists ⇒ something renders (download link at worst) ⇒ no placeholder."""
    assert placeholder_for_unrenderable("", "xyz_inventado", "statics/media/a.bin") == ""


def test_plain_text_message_without_media_is_untouched():
    assert placeholder_for_unrenderable("", None, None) == ""
    assert placeholder_for_unrenderable("oi", None, None) == "oi"


# ── Integration: ingest → batch → DB ─────────────────────────────────────────

def _reset_ai_cache() -> None:
    from channels import ai_settings
    ai_settings.reset_cache()


def _drain_orchestrator(built, channel_id: str, phone: str) -> None:
    """Flush the background batch cycle on the TestClient's portal loop
    (same helper shape as
    tests/integration/api/test_inbound_unread_and_contact_events.py)."""
    async def _drain():
        idle = 0
        for _ in range(40):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                idle += 1
                if idle >= 2:
                    return
                await asyncio.sleep(0.05)
                continue
            idle = 0
            try:
                await asyncio.wait_for(task, timeout=8.0)
            except Exception:
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_drain)


def _install_ws_capture(built) -> list[tuple]:
    deps = built.app.state.deps
    events: list[tuple] = []
    orig = deps.ws_manager.broadcast

    async def _cap(*a, **k):
        name = a[0] if a else k.get("event")
        payload = a[1] if len(a) > 1 else (k.get("data") if "data" in k else k.get("payload"))
        events.append((name, payload))
        return await orig(*a, **k)

    deps.ws_manager.broadcast = _cap
    return events


def _ingest(built, event) -> None:
    deps = built.app.state.deps
    built.client.portal.call(deps.ingest_event, event)


def _saved(phone: str, msg_id: str) -> dict | None:
    from db.repositories import contact_repo, message_repo
    contact = contact_repo.get_by_phone(phone)
    if not contact:
        return None
    for m in message_repo.get_all(contact["id"]):
        if m.get("msg_id") == msg_id:
            return m
    return None


def _build(build_app):
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False,
        "message_batch_delay": 0,
        "image_transcription_enabled": False,
        "document_transcription_enabled": False,
    })
    _reset_ai_cache()
    return built


def test_invented_media_type_is_saved_with_placeholder(build_app):
    """The whole point of F3: a synthetic payload with an invented type produces a
    readable bubble even though NO plugin knows how to format it."""
    from channels.events import InboundEvent

    phone = "5511975750001"
    msg_id = "p75_f3_unknown_1"
    built = _build(build_app)
    ws_events = _install_ws_capture(built)

    _ingest(built, InboundEvent(
        channel_id="default", provider="test", kind="message",
        external_msg_id=msg_id, chat_id=phone, sender_id=phone,
        sender_name="Cliente 75", text="", media_type="contacts",
    ))
    _drain_orchestrator(built, "default", phone)

    expected = '[Mensagem do tipo "contacts" não suportada]'
    row = _saved(phone, msg_id)
    assert row is not None, "a mensagem com media_type desconhecido deveria ter sido salva"
    assert row["content"] == expected, row
    assert row["media_type"] == "contacts"

    # t=0 optimistic broadcast already carries the same text (no empty flash).
    first = next((p for n, p in ws_events
                  if n == "new_message" and (p or {}).get("phone") == phone), None)
    assert first is not None, "esperava um new_message no t=0"
    assert first["message"]["content"] == expected, first


def test_legit_media_without_caption_keeps_empty_text(build_app):
    """R5 regression, end-to-end: an image with no caption must NOT be rewritten —
    the bubble renders the image itself."""
    from channels.events import InboundEvent

    phone = "5511975750002"
    msg_id = "p75_f3_image_1"
    built = _build(build_app)

    _ingest(built, InboundEvent(
        channel_id="default", provider="test", kind="message",
        external_msg_id=msg_id, chat_id=phone, sender_id=phone,
        sender_name="Cliente 75", text="", media_type="image",
        media_path="statics/media/p75_fake.jpg",
    ))
    _drain_orchestrator(built, "default", phone)

    row = _saved(phone, msg_id)
    assert row is not None
    assert row["content"] == "", f"imagem sem legenda não pode ganhar placeholder: {row}"
    assert row["media_type"] == "image"


def test_group_without_mention_also_gets_the_placeholder(build_app):
    """Item 4 of F3 — the group-no-mention branch saves through the same helper."""
    from channels.events import InboundEvent

    group_jid = "120363975750003@g.us"
    msg_id = "p75_f3_group_1"
    built = _build(build_app)

    _ingest(built, InboundEvent(
        channel_id="default", provider="test", kind="message",
        external_msg_id=msg_id, chat_id=group_jid, sender_id="5511975750003",
        is_group=True, text="", media_type="order", trigger_ai=False,
    ))

    row = _saved(group_jid, msg_id)
    assert row is not None, "a mensagem de grupo sem menção deveria ter sido salva"
    assert row["content"] == '[Mensagem do tipo "order" não suportada]', row
