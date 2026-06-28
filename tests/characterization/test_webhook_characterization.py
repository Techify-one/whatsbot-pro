"""Golden-master characterization of the GOWA inbound → ingest → batch → reply
pipeline (Plano 23 · Fase A2 — BLOCKING).

This locks the CURRENT behavior (bugs included) of
``POST /api/webhook/gowa/{channel_id}`` → ``parse_gowa_inbound`` → ``ingest_event``
→ batch orchestrator → reply, BEFORE Wave 2 extracts
``messaging_service`` / ``message_ingest_service`` / ``event_actions_service``.

Each test drives a real webhook POST and snapshots the NORMALIZED outcome via
``golden_assert``. Every golden captures the THREE facets:

  (a) ``messages`` — the rows persisted for that contact (role / content /
      media_type / media_path / status; ts/msg_id normalized away),
  (b) ``sent``     — the ordered ``FakeGowaClient.sent`` outbound args,
  (c) ``events``   — the bus event SET (``recorder.name_set``, fan-out order is
      non-deterministic) AND the ordered SEQUENCE (``recorder.names``) only where
      the pipeline is deterministic.

MANDATORY branch matrix (see the module docstring of each test for its cell):

  classify_jid : person · person_lid · group · newsletter(discard) · broadcast(discard)
  media types  : text · image(str) · image(dict) · audio · voice_note · video ·
                 sticker · document · location · poll · interactive · contact
  transcription: image(on) · audio(on)  (off variants folded into the media tests)
  echo         : is_from_me=true  (+ echo-audio transcription characterization)
  group        : group_no_mention (trigger_ai=False)
  split        : split_messages on (JSON array → N sends) vs off (1 send)

Discipline: ADDITIVE only. We do NOT change app/source and do NOT "fix" behavior.
Likely bugs are CAPTURED and flagged with ``# CHARACTERIZED BUG:`` comments so
Wave 2 sees them.

Distinct phone numbers per test (the suite shares one process DB).
"""

from __future__ import annotations

import asyncio

import pytest

from tests.fakes import FakeGowaClient, fake_agent_reply
from tests.characterization.golden import (
    EventRecorder, normalize, golden_assert, sort_events,
)


# ── Shared helpers ───────────────────────────────────────────────────────────

# Roles that are panel-only lifecycle/event chatter — present or absent depending
# on global toggles unrelated to the pipeline branch under test. We keep them out
# of the message projection so a golden characterizes the PIPELINE, not the
# system-notice config. (We DO assert ai_takeover indirectly via the event bus.)
_NOISE_ROLES = {"conversation_event"}


def _drain_orchestrator(built, channel_id: str, phone: str) -> None:
    """Flush the background batch orchestrator on the TestClient's ASGI loop.

    ``ingest_event`` schedules the batch cycle as a task on the portal loop; we
    wait for it via the portal so persistence/events have completed before we
    assert. ``message_batch_delay`` is forced to 0 so the cycle runs immediately.
    Loops a few times because the orchestrator can spawn a follow-up task.
    """
    async def _drain():
        for _ in range(6):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_drain)


def _project_messages(phone: str) -> list[dict]:
    """The persisted message rows for ``phone``, reduced to the load-bearing
    columns and stripped of panel-only event chatter, ordered by ts."""
    from db.repositories import contact_repo, message_repo

    contact = contact_repo.get_by_phone(phone)
    if contact is None:
        return []
    rows = message_repo.get_all(contact["id"])
    out = []
    for r in rows:
        if r.get("role") in _NOISE_ROLES:
            continue
        out.append({
            "role": r.get("role"),
            "content": r.get("content"),
            "media_type": r.get("media_type"),
            "media_path": r.get("media_path"),
            "status": r.get("status"),
            "msg_id": r.get("msg_id"),
        })
    return out


def _capture(phone: str, recorder: EventRecorder, gowa: FakeGowaClient) -> dict:
    """Assemble the three-facet golden value for ``phone`` (already normalized)."""
    return normalize({
        "messages": _project_messages(phone),
        "sent": [{"kind": k, **args} for k, args in gowa.sent],
        "events": {
            "set": sort_events(list(recorder.name_set)),
            "sequence": recorder.names,
        },
    })


def _text_payload(phone: str, msg_id: str, body: str, name: str = "Cliente") -> dict:
    return {"event": "message", "payload": {
        "from": f"{phone}@s.whatsapp.net", "id": msg_id,
        "body": body, "from_name": name}}


# ── classify_jid matrix ──────────────────────────────────────────────────────

def test_classify_person(build_app):
    """classify_jid → person (@s.whatsapp.net): processed, contact materialized,
    inbound persisted, message.received + message.saved fire. AI gated OFF."""
    phone = "5511970000001"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    with EventRecorder() as rec:
        r = built.client.post("/api/webhook/gowa/default",
                              json=_text_payload(phone, "person_1", "olá pessoa"))
        assert r.status_code == 200, r.text
        _drain_orchestrator(built, "default", phone)
        rec.drain()

    assert "message.received" in rec.name_set
    assert "message.saved" in rec.name_set
    golden_assert("jid_person", _capture(phone, rec, built.gowa_client))


def test_classify_person_lid(build_app):
    """classify_jid → person_lid (@lid): a privacy-mode person is also processed
    (in the default allowed set), so it persists like a normal person.

    The GOWA inbound path treats only ``@g.us`` as a group; ``@lid`` falls into
    the person branch where ``phone = jid.split('@')[0]`` (the LID digits)."""
    lid = "188887776665554"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    with EventRecorder() as rec:
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{lid}@lid", "id": "lid_1",
                "body": "olá modo privacidade", "from_name": "LID User"}})
        assert r.status_code == 200, r.text
        _drain_orchestrator(built, "default", lid)
        rec.drain()

    assert "message.received" in rec.name_set
    assert "message.saved" in rec.name_set
    golden_assert("jid_person_lid", _capture(lid, rec, built.gowa_client))


def test_classify_group_no_mention(build_app):
    """classify_jid → group (@g.us) with NO @mention (group_reply_mode default
    = mention_only): persisted + message.saved(source=group_no_mention), but
    trigger_ai=False so the agent never runs and nothing is sent.

    This is ALSO the dedicated group_no_mention branch of the matrix. The contact
    is keyed by the FULL group JID (``phone == chat_jid``)."""
    group_jid = "120363111110001@g.us"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True,                 # even with AI on, no-mention won't trigger
        "message_batch_delay": 0,
        "group_reply_mode": "mention_only",
    })
    with EventRecorder() as rec, fake_agent_reply("NÃO DEVE SER ENVIADO") as agent_rec:
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "chat_id": group_jid, "from": "5511970000050@s.whatsapp.net",
                "id": "grp_1", "body": "mensagem no grupo sem mencionar o bot",
                "from_name": "Membro do Grupo"}})
        assert r.status_code == 200, r.text
        _drain_orchestrator(built, "default", group_jid)
        rec.drain()

    # trigger_ai=False → the stubbed agent must NOT have been invoked.
    assert not agent_rec.calls, "group-no-mention must not run the agent"
    assert "message.received" in rec.name_set
    assert "message.saved" in rec.name_set
    assert built.gowa_client.sent == []
    golden_assert("jid_group_no_mention", _capture(group_jid, rec, built.gowa_client))
    built.settings.set("auto_reply", False)


def test_classify_newsletter_discarded(build_app):
    """classify_jid → newsletter (@newsletter): DISCARDED.

    CHARACTERIZED BEHAVIOR: GOWA inbound only treats ``@g.us`` as a group; every
    other suffix (newsletter/broadcast/lid) falls into the person branch where
    ``phone = jid.split('@')[0]``. The ``allowed_jid_types`` filter lives in the
    LEGACY ``/api/webhook`` handler, NOT in ``parse_gowa_inbound`` / the generic
    ``/api/webhook/gowa/{channel_id}`` route this test drives. So a newsletter
    post on the live (generic) path is NOT discarded by the JID filter — it is
    materialized as a phantom contact keyed by the newsletter id.

    # CHARACTERIZED BUG: the CLAUDE.md-documented allowed_jid_types discard
    # (newsletter/broadcast dropped before materializing a contact) is NOT applied
    # on the generic /api/webhook/gowa/{channel_id} path — only on the legacy
    # /api/webhook handler (see classify in server/routes/webhook.py). The
    # newsletter therefore persists as a phantom person here. Wave 2 must move the
    # classify/discard into the shared ingest path.
    """
    nid = "120363222220002"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    with EventRecorder() as rec:
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{nid}@newsletter", "id": "news_1",
                "body": "post de canal", "from_name": "Canal X"}})
        assert r.status_code == 200, r.text
        _drain_orchestrator(built, "default", nid)
        rec.drain()
    golden_assert("jid_newsletter", _capture(nid, rec, built.gowa_client))


def test_classify_broadcast_discarded(build_app):
    """classify_jid → broadcast (@broadcast): see newsletter note.

    # CHARACTERIZED BUG: same as jid_newsletter — the generic gowa route does not
    # apply the allowed_jid_types discard, so a Status/broadcast post materializes
    # a phantom contact instead of being dropped.
    """
    bid = "status"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    with EventRecorder() as rec:
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{bid}@broadcast", "id": "bcast_1",
                "body": "status de transmissão", "from_name": "Transmissão"}})
        assert r.status_code == 200, r.text
        _drain_orchestrator(built, "default", bid)
        rec.drain()
    golden_assert("jid_broadcast", _capture(bid, rec, built.gowa_client))


# ── media-type matrix (person inbound, AI gated OFF → persistence snapshot) ───

def _media_case(build_app, *, golden, phone, payload_extra, body="",
                msg_id="media_1"):
    """Drive one media payload with AI OFF and snapshot persistence + events."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        # Keep transcription OFF here so the media golden is purely persistence.
        "image_transcription_enabled": False,
        "document_transcription_enabled": False,
        "audio_transcription_mode": "never",
    })
    payload = {"from": f"{phone}@s.whatsapp.net", "id": msg_id, "from_name": "Cliente"}
    if body:
        payload["body"] = body
    payload.update(payload_extra)
    with EventRecorder() as rec:
        r = built.client.post("/api/webhook/gowa/default",
                              json={"event": "message", "payload": payload})
        assert r.status_code == 200, r.text
        _drain_orchestrator(built, "default", phone)
        rec.drain()
    golden_assert(golden, _capture(phone, rec, built.gowa_client))
    return built


def test_media_text(build_app):
    """media type: text — the baseline, single user row, no media columns."""
    _media_case(build_app, golden="media_text", phone="5511970001001",
                payload_extra={"body": "mensagem de texto simples"})


def test_media_image_str(build_app):
    """media type: image (str form) — payload ``image`` is a bare path string."""
    _media_case(build_app, golden="media_image_str", phone="5511970001002",
                payload_extra={"image": "statics/media/img_str.jpg"})


def test_media_image_dict(build_app):
    """media type: image (dict form) — ``{path, caption, mimetype}``; caption
    becomes the text when there's no body, extras carry caption+mimetype."""
    _media_case(build_app, golden="media_image_dict", phone="5511970001003",
                payload_extra={"image": {
                    "path": "statics/media/img_dict.jpg",
                    "caption": "olha essa foto", "mimetype": "image/jpeg"}})


def test_media_audio(build_app):
    """media type: audio (dict form) — voice clip; placeholder text, voice extras."""
    _media_case(build_app, golden="media_audio", phone="5511970001004",
                payload_extra={"audio": {
                    "path": "statics/media/clip.ogg",
                    "duration": 4200, "mimetype": "audio/ogg"}})


def test_media_voice_note(build_app):
    """media type: video_note → treated as audio (is_voice_note extras)."""
    _media_case(build_app, golden="media_voice_note", phone="5511970001005",
                payload_extra={"video_note": {"path": "statics/media/vn.ogg"}})


def test_media_video(build_app):
    """media type: video — placeholder/caption + duration extras."""
    _media_case(build_app, golden="media_video", phone="5511970001006",
                payload_extra={"video": {
                    "path": "statics/media/clip.mp4",
                    "caption": "veja o vídeo", "duration": 9000,
                    "mimetype": "video/mp4"}})


def test_media_sticker(build_app):
    """media type: sticker — ``[Sticker]`` placeholder + animated/mimetype extras."""
    _media_case(build_app, golden="media_sticker", phone="5511970001007",
                payload_extra={"sticker": {
                    "path": "statics/media/s.webp",
                    "is_animated": True, "mimetype": "image/webp"}})


def test_media_document(build_app):
    """media type: document — filename label in text; file_name/mimetype extras.

    The webhook layer would normally re-resolve the real filename via GOWA's
    chat-storage API; the FakeGowaClient returns ``""`` for ``get_message_filename``
    so we characterize the fallback label path."""
    _media_case(build_app, golden="media_document", phone="5511970001008",
                payload_extra={"document": {
                    "path": "statics/media/doc.pdf",
                    "file_name": "contrato.pdf", "mimetype": "application/pdf",
                    "caption": "segue o contrato"}})


def test_media_location(build_app):
    """media type: location — media_path ``geo:lat,lng``, lat/lng/name extras."""
    _media_case(build_app, golden="media_location", phone="5511970001009",
                payload_extra={"location": {
                    "latitude": -23.5, "longitude": -46.6,
                    "name": "Av. Paulista", "address": "São Paulo"}})


def test_media_poll(build_app):
    """media type: poll — name + option titles in extras; no media_path."""
    _media_case(build_app, golden="media_poll", phone="5511970001010",
                payload_extra={"poll": {
                    "name": "Qual cor?",
                    "options": [{"name": "Azul"}, {"name": "Verde"}]}})


def test_media_interactive(build_app):
    """media type: interactive — button response (button_id + title extras)."""
    _media_case(build_app, golden="media_interactive", phone="5511970001011",
                payload_extra={"buttons_response": {
                    "button_id": "btn_sim", "title": "Sim"}})


def test_media_contact(build_app):
    """media type: contact — single shared vCard (name + phone in extras)."""
    _media_case(build_app, golden="media_contact", phone="5511970001012",
                payload_extra={"contact": {
                    "displayName": "João Silva", "phone_number": "5511988887777"}})


# ── transcription ON (image + audio) ─────────────────────────────────────────

# Deterministic canned transcripts so the ``[Descrição]``/``[Transcrição]``
# content is byte-stable.
_CANNED_IMAGE_DESC = "uma foto de um gato laranja"
_CANNED_AUDIO_TEXT = "olá, gostaria de saber o preço"


def test_media_image_transcription_on(build_app):
    """transcription ON for image: ``describe_image`` stubbed → the last user
    message content becomes ``[Descrição da imagem]: ...`` and a panel-only
    ``transcription`` row is added. AI still gated OFF (no reply)."""
    phone = "5511970002001"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "image_transcription_enabled": True,
    })
    from unittest.mock import patch
    with patch.object(built.agent_handler, "describe_image",
                      return_value=_CANNED_IMAGE_DESC):
        with EventRecorder() as rec:
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": "imgtr_1",
                    "from_name": "Cliente",
                    "image": {"path": "statics/media/cat.jpg"}}})
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
            rec.drain()
    golden_assert("media_image_transcription_on",
                  _capture(phone, rec, built.gowa_client))


def test_media_audio_transcription_on(build_app):
    """transcription ON for audio: ``transcribe_audio`` stubbed → last user msg
    content becomes ``[Transcrição do áudio]: ...`` and the transcription is
    delivered (default target=private → a ``transcription`` panel row)."""
    phone = "5511970002002"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "audio_transcription_mode": "received",
        "audio_transcription_target": "private",
    })
    from unittest.mock import patch
    with patch.object(built.agent_handler, "transcribe_audio",
                      return_value=_CANNED_AUDIO_TEXT):
        with EventRecorder() as rec:
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": "audtr_1",
                    "from_name": "Cliente",
                    "audio": {"path": "statics/media/voice.ogg"}}})
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
            rec.drain()
    golden_assert("media_audio_transcription_on",
                  _capture(phone, rec, built.gowa_client))


# ── echo (is_from_me=true) ───────────────────────────────────────────────────

def test_echo_text(build_app):
    """echo: a text message sent from the user's OWN device (is_from_me=true).

    Routed through ``_ingest_echo``: saved as an ``assistant`` row with
    status=operator, broadcast, and ``message.sent`` source=echo fires. No agent
    run, no outbound send (the bot didn't author it)."""
    phone = "5511970003001"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    with EventRecorder() as rec:
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{phone}@s.whatsapp.net", "id": "echo_1",
                "body": "mensagem que eu mesmo enviei do celular",
                "is_from_me": True, "from_name": "Eu"}})
        assert r.status_code == 200, r.text
        _drain_orchestrator(built, "default", phone)
        rec.drain()

    assert "message.sent" in rec.name_set
    assert built.gowa_client.sent == [], "echo must not produce an outbound send"
    golden_assert("echo_text", _capture(phone, rec, built.gowa_client))


def test_echo_audio_transcription(build_app):
    """echo + audio transcription: an audio sent from the user's own device.

    # CHARACTERIZED BUG: ``_ingest_echo`` (the live generic-path echo handler)
    # saves the echo and emits message.sent source=echo, but does NOT transcribe
    # outgoing audio — outgoing-audio transcription only exists in the LEGACY
    # /api/webhook handler. So even with audio_transcription_mode=both and
    # transcribe_audio stubbed, NO ``[Transcrição]`` content / transcription row is
    # produced on the live path. Plano 23 Fase F2 step 2 ("transcrição de áudio de
    # echo") must implement this on the live path before retiring the legacy one.
    """
    phone = "5511970003002"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "audio_transcription_mode": "both",   # would allow 'sent' if it were wired
    })
    from unittest.mock import patch
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return _CANNED_AUDIO_TEXT

    with patch.object(built.agent_handler, "transcribe_audio", side_effect=_spy):
        with EventRecorder() as rec:
            r = built.client.post("/api/webhook/gowa/default", json={
                "event": "message", "payload": {
                    "from": f"{phone}@s.whatsapp.net", "id": "echoaud_1",
                    "is_from_me": True, "from_name": "Eu",
                    "audio": {"path": "statics/media/myvoice.ogg"}}})
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
            rec.drain()

    # Locks the bug: echo audio is NOT transcribed on the live path.
    assert called["n"] == 0, \
        "characterization expected echo-audio NOT transcribed on live path"
    golden_assert("echo_audio_no_transcription",
                  _capture(phone, rec, built.gowa_client))


# ── split_messages on vs off (AI gated ON, stubbed reply) ────────────────────

# A JSON-array reply: under split_messages=True the pipeline splits it into N
# sends; under False it sends the raw array string as ONE message.
_SPLIT_REPLY = '["primeira parte", "segunda parte", "terceira parte"]'


def _ai_reply_case(build_app, *, golden, phone, split, reply=_SPLIT_REPLY,
                   msg_id="split_1"):
    """Drive an AI-gated-ON turn with a stubbed reply and snapshot outbound+msgs."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True,
        "message_batch_delay": 0,
        "split_messages": split,
        "split_message_delay": 0,
        "response_delay_min": 0,
        "response_delay_max": 0,
    })
    try:
        with EventRecorder() as rec, fake_agent_reply(reply) as agent_rec:
            r = built.client.post("/api/webhook/gowa/default",
                                  json=_text_payload(phone, msg_id,
                                                     "quero falar com a IA"))
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
            rec.drain()
        assert agent_rec.calls, "AI turn should have invoked the stubbed agent"
        golden_assert(golden, _capture(phone, rec, built.gowa_client))
    finally:
        # Don't leak the global auto_reply onto other tests (shared DB).
        built.settings.set("auto_reply", False)


def test_split_messages_on(build_app):
    """split_messages=True + JSON-array reply → THREE separate send_message calls,
    each persisted as its own assistant row (status=sent). message.sent fires per
    part."""
    _ai_reply_case(build_app, golden="reply_split_on",
                   phone="5511970004001", split=True)


def test_split_messages_off(build_app):
    """split_messages=False + same JSON-array reply → ONE send_message with the
    raw array string (no parsing), a single assistant row."""
    _ai_reply_case(build_app, golden="reply_split_off",
                   phone="5511970004002", split=False)


def test_reply_plain_single(build_app):
    """A plain (non-array) reply with split ON still yields ONE send (parse_split
    falls back to a single message). Characterizes the common happy path +
    confirms ``ai_takeover`` fires once on the bus for a first AI reply."""
    phone = "5511970004003"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0,
        "split_messages": True, "split_message_delay": 0,
        "response_delay_min": 0, "response_delay_max": 0,
    })
    try:
        with EventRecorder() as rec, fake_agent_reply("claro, posso ajudar!") as agent_rec:
            r = built.client.post("/api/webhook/gowa/default",
                                  json=_text_payload(phone, "plain_1",
                                                     "oi, tudo bem?"))
            assert r.status_code == 200, r.text
            _drain_orchestrator(built, "default", phone)
            rec.drain()
        assert agent_rec.calls
        # First AI reply in this conversation → ai_takeover on the bus, once.
        assert "conversation.ai_takeover" in rec.name_set
        assert rec.names.count("conversation.ai_takeover") == 1
        golden_assert("reply_plain_single", _capture(phone, rec, built.gowa_client))
    finally:
        built.settings.set("auto_reply", False)
