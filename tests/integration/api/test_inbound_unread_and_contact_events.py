"""Plano 25 — characterization for the two receive-pipeline bugs.

Bug #1 (Fase 1) — the badge that vanishes on its own.
    The batch's AI auto-mark-read (clear the unread badge + send a REAL read-receipt
    to the client) was gated only on the channel layer (``_channel_ai_enabled``),
    missing the per-conversation ``ai_active`` flag. So an **IA-OFF** conversation
    (global ON + channel ON, but this conversation paused) still had its badge wiped
    and the client still saw "lida"/blue-tick. The fix aligns the gate with the AI
    reply (``_channel_ai_enabled and _conversation_ai_active``): only a conversation
    the AI is actually taking over auto-marks-read.

Bug #2 (Fase 2) — the tab notifies before the list.
    A brand-new conversation was only materialized when the batch SAVED the message
    (t≈``message_batch_delay``), so its sidebar row appeared ~3-4 s after the tab
    badge. The fix materializes the conversation at INGEST (t=0): the conversation
    exists the moment the webhook returns, the ingest's ``new_message`` carries its
    ``conversation_id``, and ``conversation_created`` is announced — exactly once
    end-to-end (the later batch re-resolves the same thread silently).

Pytest-collected (``tests/endpoints``). Drives the REAL webhook → ingest → batch
pipeline through the hermetic ``build_app``, spying at the ``deps`` seam:
``ws_manager.broadcast`` (messages_read / new_message) and
``outbound_router.mark_read`` (the read-receipt). ``conversation_created`` rides
``plugins.context.broadcast``, which the hermetic build does not wire to a
ws_manager — so we patch that function (the listener re-imports it at call time).
"""

from __future__ import annotations

import asyncio


def _reset_ai_cache():
    """Drop the process-global 30s per-channel AI-settings cache. Tests share ONE
    session DB, so a prior test can leave a stale ``default`` channel entry cached
    (``ai_enabled`` / ``message_batch_delay`` / ``ai_sequential_*``); clearing it
    forces a fresh read of THIS test's config and removes the cross-test flake."""
    from channels import ai_settings
    ai_settings.reset_cache()


# ── helpers (inlined to avoid importing a sibling test module) ──────────────────

def _text_payload(phone: str, msg_id: str, body: str, name: str = "Cliente") -> dict:
    return {"event": "message", "payload": {
        "from": f"{phone}@s.whatsapp.net", "id": msg_id,
        "body": body, "from_name": name}}


def _drain_orchestrator(built, channel_id: str, phone: str) -> None:
    """Flush the background batch orchestrator on the TestClient's portal loop.

    Robust against the orchestrator's ~2s ``ai_sequential_delay`` sleep and its
    re-scheduled follow-up task (``_orchestrate`` can spawn another cycle if a new
    message arrived during the SEND phase): wait for the current task, then keep
    polling until ``processing_tasks`` has been EMPTY for two consecutive checks
    (a single empty poll can race the gap between one cycle finishing and the
    follow-up being registered). Generous total budget so a loaded full-suite run
    never asserts on a half-finished batch (the source of the P25 flake)."""
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
    """Spy on ``deps.ws_manager.broadcast`` (the SAME object the ingest + batch use).
    Returns a growing list of ``(event_name, payload)``; still calls through."""
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


def _install_mark_read_capture(built) -> list[tuple]:
    """Spy on ``deps.outbound_router.mark_read`` (the real read-receipt seam)."""
    deps = built.app.state.deps
    calls: list[tuple] = []
    orig = deps.outbound_router.mark_read

    def _cap(channel_id, chat_id, msg_id):
        calls.append((channel_id, chat_id, msg_id))
        return orig(channel_id, chat_id, msg_id)

    deps.outbound_router.mark_read = _cap
    return calls


def _unread_count(phone: str) -> int:
    from db.repositories import contact_repo
    c = contact_repo.get_by_phone(phone)
    return int(c["unread_count"]) if c else -1


def _conversation_count(contact_id: int) -> int:
    from sqlalchemy import select, func
    from db.engine import get_engine
    from db.tables import conversations
    with get_engine().connect() as conn:
        return int(conn.execute(
            select(func.count()).select_from(conversations)
            .where(conversations.c.contact_id == contact_id)).scalar() or 0)


# ── Bug #1 — IA-OFF keeps the badge, IA-ON still clears it ──────────────────────

def test_bug1_ia_off_keeps_badge_and_sends_no_read_receipt(build_app):
    """auto_reply ON + channel ai_enabled ON, but this conversation is ai_active OFF
    (seeded via ``default_ai_enabled=False``). The batch must NOT auto-mark-read:
    the badge persists, no ``messages_read`` broadcast, no real read-receipt.

    Pre-Fase-1 this FAILS — the gate checked only ``_channel_ai_enabled``, so the
    batch wiped the badge and sent a bogus "lida" to the client."""
    phone = "5511955550001"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0, "default_ai_enabled": False})
    _reset_ai_cache()
    ws_events = _install_ws_capture(built)
    mark_reads = _install_mark_read_capture(built)

    r = built.client.post("/api/webhook/gowa/default",
                          json=_text_payload(phone, "p25_off_1", "oi, tem alguém aí?"))
    assert r.status_code == 200, r.text
    _drain_orchestrator(built, "default", phone)

    # (i) badge persists — mark_user_messages_as_read never ran
    assert _unread_count(phone) > 0, "IA-OFF conversation must keep its unread badge"
    # (ii) no messages_read broadcast
    assert not [e for e in ws_events if e[0] == "messages_read"], \
        f"unexpected messages_read in {[e[0] for e in ws_events]}"
    # (iii) no real read-receipt to the client
    assert mark_reads == [], f"unexpected read-receipt(s) in IA-OFF conversation: {mark_reads}"


def test_bug1_ia_on_still_clears_badge_and_sends_receipt(build_app):
    """Mirror / regression guard: with all three AI layers ON (global + channel +
    conversation ai_active), the AI takes over → the batch STILL auto-marks-read.
    The Fase 1 fix must not regress the takeover case."""
    from tests.fakes import fake_agent_reply

    phone = "5511955550002"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True, "message_batch_delay": 0, "default_ai_enabled": True})
    _reset_ai_cache()
    ws_events = _install_ws_capture(built)
    mark_reads = _install_mark_read_capture(built)

    with fake_agent_reply("ok, já te respondo!", handler=built.agent_handler):
        r = built.client.post("/api/webhook/gowa/default",
                              json=_text_payload(phone, "p25_on_1", "oi, tem alguém aí?"))
        assert r.status_code == 200, r.text
        _drain_orchestrator(built, "default", phone)

    assert _unread_count(phone) == 0, "IA-ON conversation (AI takeover) should clear the badge"
    assert [e for e in ws_events if e[0] == "messages_read"], \
        "expected a messages_read broadcast when the AI takes over"
    assert mark_reads, "expected a real read-receipt for the inbound message on AI takeover"


# ── Bug #2 — conversation materialized at ingest, new_message carries the id ─────

def test_bug2_conversation_materialized_at_ingest(build_app, monkeypatch):
    """First message from a BRAND-NEW contact: the conversation is created at INGEST
    (the moment the webhook returns), the ingest's ``new_message`` carries its
    ``conversation_id`` and ``conversation_created`` is announced. Draining the batch
    must NOT create a second conversation nor a second ``conversation_created``.

    Pre-Fase-2 this FAILS — the conversation (and ``conversation_created``) only
    appeared when the batch saved the message, and ``new_message`` had no
    ``conversation_id``."""
    # conversation_created rides plugins.context.broadcast; the listener re-imports
    # that symbol at call time, so patching the module attribute captures it even
    # though the hermetic build never wired the plugin runtime to a ws_manager.
    import plugins.context as plugin_ctx
    created: list[tuple] = []
    orig_pb = plugin_ctx.broadcast

    def _pb(event, payload=None):
        created.append((event, payload))
        try:
            return orig_pb(event, payload)
        except Exception:
            return None

    monkeypatch.setattr(plugin_ctx, "broadcast", _pb)

    # Namespace exclusivo deste arquivo; a suíte compartilha o mesmo banco por
    # processo e um telefone reutilizado por outro contrato cria falso duplicado.
    phone = "5511955560003"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    _reset_ai_cache()
    ws_events = _install_ws_capture(built)

    from db.repositories import contact_repo, conversation_repo, message_repo

    r = built.client.post("/api/webhook/gowa/default",
                          json=_text_payload(phone, "p25_new_1", "primeira mensagem"))
    assert r.status_code == 200, r.text

    # ── The POST awaited the INGEST; the conversation must already exist (t=0). ──
    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "contact should be materialized at ingest"
    conv = conversation_repo.get_latest_for_contact(contact["id"])
    assert conv is not None, "conversation must be materialized at INGEST (bug #2)"
    conv_id = conv["id"]

    # the ingest's new_message carries the conversation_id (frontend matches the row by it)
    new_msgs = [p for (n, p) in ws_events if n == "new_message"]
    assert new_msgs, "ingest must broadcast new_message"
    assert new_msgs[0]["message"].get("conversation_id") == conv_id, \
        ("ingest new_message must carry conversation_id; got "
         f"{new_msgs[0]['message'].get('conversation_id')!r} vs conv {conv_id}")

    # conversation_created announced at ingest
    assert any(n == "conversation_created" for (n, _p) in created), \
        f"conversation_created must fire at ingest; saw {[n for n, _ in created]}"

    # ── Drain the BATCH: exactly-once — no duplicate conversation / announcement. ──
    _drain_orchestrator(built, "default", phone)
    assert _conversation_count(contact["id"]) == 1, "batch must reuse the ingest conversation"
    assert sum(1 for (n, _p) in created if n == "conversation_created") == 1, \
        "conversation_created must fire exactly once across ingest + batch"

    # the combined message is saved by the batch, linked to the same conversation
    user_rows = [m for m in message_repo.get_all(contact["id"]) if m.get("role") == "user"]
    assert len(user_rows) == 1, f"expected one saved user message, got {len(user_rows)}"
    assert user_rows[0].get("conversation_id") == conv_id


# ── Plano 28 — Event-Carried State Transfer for the sidebar list ────────────────

def _capture_plugin_broadcast(monkeypatch) -> list[tuple]:
    """Spy on ``plugins.context.broadcast`` (where conversation_upsert /
    conversation_created ride). Returns a growing list of ``(event, payload)``."""
    import plugins.context as plugin_ctx
    seen: list[tuple] = []
    orig = plugin_ctx.broadcast

    def _pb(event, payload=None):
        seen.append((event, payload))
        try:
            return orig(event, payload)
        except Exception:
            return None

    monkeypatch.setattr(plugin_ctx, "broadcast", _pb)
    return seen


def test_conversation_upsert_is_emitted_at_ingest_and_after_batch(build_app, monkeypatch):
    """Plano 28: a brand-new inbound conversation emits ``conversation_upsert`` at
    INGEST (t=0) — fully formed (name/channel/status, ``origin='inbound'``) with
    ``last_message_ts=0`` — so the sidebar can insert the row immediately (gate keys
    on origin, not on a message existing). After the batch persists the message a
    second ``conversation_upsert`` carries the real preview (``last_message_ts>0``).
    The payload shape mirrors a ``/api/atendimentos`` list item (carries ``labels``)."""
    seen = _capture_plugin_broadcast(monkeypatch)
    phone = "5511955550028"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    _reset_ai_cache()

    from db.repositories import contact_repo, conversation_repo

    r = built.client.post("/api/webhook/gowa/default",
                          json=_text_payload(phone, "p28_new_1", "olá primeira"))
    assert r.status_code == 200, r.text

    contact = contact_repo.get_by_phone(phone)
    conv = conversation_repo.get_latest_for_contact(contact["id"])
    conv_id = conv["id"]

    # origin was stamped 'inbound' by the resolve (customer-initiated thread).
    assert conv.get("origin") == "inbound", f"origin should be inbound, got {conv.get('origin')!r}"

    upserts = [p for (n, p) in seen if n == "conversation_upsert"]
    assert upserts, "conversation_upsert must fire at ingest (t=0)"
    t0 = upserts[0]
    # t=0 row: fully formed, no message yet.
    assert t0.get("id") == conv_id, f"upsert.id must be the conversation id ({conv_id}), got {t0.get('id')}"
    assert t0.get("contact_id") == contact["id"]
    assert t0.get("contact_phone") == phone
    assert t0.get("origin") == "inbound"
    assert t0.get("status") == "open"
    assert (t0.get("last_message_ts") or 0) == 0, "t=0 upsert must carry last_message_ts=0"
    # list-item shape: channel + labels + contact_tags present (get_row_for_broadcast
    # attaches both — plano 72 F0). contact_tags parity makes the client's live
    # insert/drop-gate trust the `tag` dimension.
    assert "channel_id" in t0 and "labels" in t0, f"upsert must be a full list row; keys={sorted(t0)[:8]}"
    assert isinstance(t0.get("labels"), list)
    assert "contact_tags" in t0, f"upsert must carry contact_tags (plano 72 F0); keys={sorted(t0)}"
    assert isinstance(t0.get("contact_tags"), list)

    # ── Drain the batch → a later upsert carries the REAL preview (ts>0). ──
    _drain_orchestrator(built, "default", phone)
    upserts2 = [p for (n, p) in seen if n == "conversation_upsert"]
    assert any((p.get("last_message_ts") or 0) > 0 and p.get("id") == conv_id for p in upserts2), \
        "batch add_message must emit a conversation_upsert with the real preview (last_message_ts>0)"


def test_broadcast_row_carries_contact_tags(build_app):
    """Plano 72 F0: ``get_row_for_broadcast`` must attach the CONTACT TAGS (not only
    the conversation labels), so the ``conversation_upsert`` payload is byte-for-byte
    what ``list_filtered``/``list_conversations`` return on the `tag` dimension. Without
    this the client's live insert-gate (F3) sees ``contact_tags: []`` on every push and
    would wrongly drop a tagged conversation whenever a tag funnel is active (A1)."""
    built = build_app(["gowa"], settings_overrides={"auto_reply": False, "message_batch_delay": 0})
    _reset_ai_cache()

    from db.repositories import contact_repo, conversation_repo, tag_repo

    phone = "5511955550072"
    r = built.client.post("/api/webhook/gowa/default",
                          json=_text_payload(phone, "p72_tags_1", "olá tags"))
    assert r.status_code == 200, r.text

    contact = contact_repo.get_by_phone(phone)
    conv = conversation_repo.get_latest_for_contact(contact["id"])
    conv_id = conv["id"]

    # A row for a contact with NO tags → empty list (not missing key).
    row0 = conversation_repo.get_row_for_broadcast(conv_id)
    assert row0 is not None
    assert row0.get("contact_tags") == [], f"untagged contact → []; got {row0.get('contact_tags')!r}"

    # Tag the CONTACT → the broadcast row must carry it (parity with list_filtered).
    tag_name = "vip_p72_broadcast"
    assert tag_repo.create(tag_name, "#f00")
    tag_repo.set_contact_tags(contact["id"], [tag_name])
    row1 = conversation_repo.get_row_for_broadcast(conv_id)
    assert row1 is not None
    assert row1.get("contact_tags") == [tag_name], (
        f"broadcast row must reflect the contact's tags (plano 72 F0); "
        f"got {row1.get('contact_tags')!r}")
    # Conversation labels stay a SEPARATE field (not fused with contact tags).
    assert isinstance(row1.get("labels"), list)


def test_panel_only_role_does_not_emit_conversation_upsert(build_app, monkeypatch):
    """A panel-only save (``private_note``) must NOT emit ``conversation_upsert`` — it
    never becomes a conversation's last message, so it must not push a list update. A
    visible role (``user``) on the same contact DOES emit."""
    seen = _capture_plugin_broadcast(monkeypatch)
    build_app(["gowa"], settings_overrides={"auto_reply": False})
    _reset_ai_cache()

    from agent.memory import ContactMemory
    cm = ContactMemory("5511955550029")

    cm.add_message("private_note", "nota interna do operador")
    assert not [p for (n, p) in seen if n == "conversation_upsert"], \
        "panel-only role (private_note) must not emit conversation_upsert"

    cm.add_message("user", "mensagem visível do cliente")
    assert [p for (n, p) in seen if n == "conversation_upsert"], \
        "a visible role (user) must emit conversation_upsert"


def test_empty_inbound_conversation_finder_respects_age_and_origin(build_app):
    """The TTL-sweep finder returns ONLY inbound conversations that are empty (no
    message) and older than the cutoff — never one with a message, an outbound one,
    or a too-recent one."""
    import time as _t
    from sqlalchemy import update
    from db.engine import get_engine
    from db.tables import conversations as _conv_t
    from agent.memory import ContactMemory
    from db.repositories import conversation_repo

    build_app(["gowa"], settings_overrides={"auto_reply": False})
    _reset_ai_cache()

    old = _t.time() - 3600      # 1h ago
    cutoff = _t.time() - 1800   # 30 min ago

    def _backdate(conv_id):
        with get_engine().begin() as conn:
            conn.execute(update(_conv_t).where(_conv_t.c.id == conv_id)
                         .values(created_at=old))

    # (a) inbound, empty, old → GHOST.
    ghost_cm = ContactMemory("5511955550030")
    ghost_id = ghost_cm.ensure_conversation_live("user")   # origin='inbound', no message
    _backdate(ghost_id)

    # (b) inbound, WITH a message, old → not a ghost.
    msg_cm = ContactMemory("5511955550031")
    msg_cm.add_message("user", "tenho mensagem")
    with_msg_id = conversation_repo.get_latest_for_contact(msg_cm.id)["id"]
    _backdate(with_msg_id)

    # (c) inbound, empty, but RECENT → not swept yet.
    recent_cm = ContactMemory("5511955550032")
    recent_id = recent_cm.ensure_conversation_live("user")  # created_at = now

    found = {g["id"] for g in conversation_repo.find_empty_inbound_ghosts(cutoff)}
    assert ghost_id in found, "empty old inbound conversation must be a sweepable ghost"
    assert with_msg_id not in found, "a conversation WITH a message is never a ghost"
    assert recent_id not in found, "a too-recent empty conversation must not be swept"
