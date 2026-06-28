"""Smoke test for the characterization kit (Plano 23 · G1-min foundation).

Proves the three new pieces actually compose and exercise the REAL gowa webhook
flow — WITHOUT yet being the full A2 golden:

* ``build_test_app(["gowa"])`` boots a hermetic app with only the gowa plugin
  loaded + enabled, so ``POST /api/webhook/gowa/default`` reaches the live
  ``GOWAChannel.parse_inbound → parse_gowa_inbound → ingest`` path;
* :class:`EventRecorder` captures the bus events that fire;
* :class:`FakeGowaClient` records outbound sends (here: none, AI is gated off).

AI is gated OFF by the default config (``auto_reply=False``), so the inbound
message is PERSISTED but no LLM call / outbound send happens — which is exactly
what makes this a safe, network-free smoke test.
"""

from __future__ import annotations

from tests.fakes import FakeGowaClient, fake_agent_reply
from tests.characterization.golden import EventRecorder, normalize, golden_assert


# A phone unique to this module so it never collides with the shared seed data.
PHONE = "5511955550042"
GOWA_JID = f"{PHONE}@s.whatsapp.net"


def _await_orchestrator(built, channel_id: str, phone: str) -> None:
    """Flush the background batch orchestrator on the TestClient's ASGI loop.

    ``ingest_event`` schedules the batch cycle as a task on the portal loop; we
    wait for it via the portal so persistence/events have completed before we
    assert. ``message_batch_delay`` is forced to 0 so the cycle runs immediately.
    """
    async def _drain():
        import asyncio
        task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:
                pass

    built.client.portal.call(_drain)


def test_build_test_app_loads_only_gowa(build_app):
    """build_test_app enables exactly the requested plugins, hermetically."""
    built = build_app(["gowa"])
    deps = built.app.state.deps
    assert "gowa" in deps.plugins_registry.loaded, "gowa plugin must be loaded"
    # The hermetic plugins dir holds ONLY gowa (no surprise bundled plugins).
    assert set(deps.plugins_registry.loaded.keys()) == {"gowa"}
    # The gowa provider is registered on the channel registry.
    assert deps.channel_registry.get_provider("gowa") is not None
    # The seeded `default` channel resolves to a live gowa instance.
    assert deps.channel_registry.get("default") is not None


def test_gowa_inbound_persists_and_fires_events(build_app):
    """End-to-end: POST a text msg → it persists + the inbound bus events fire."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False,           # AI gated off → no LLM call, no outbound
        "message_batch_delay": 0,      # run the batch cycle immediately
    })
    from db.repositories import contact_repo, message_repo

    with EventRecorder() as rec:
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message",
            "payload": {
                "from": GOWA_JID,
                "id": "smoke_msg_1",
                "body": "olá kit de caracterização",
                "from_name": "Smoke Tester",
            },
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"]["events"] == 1
        assert body["data"]["handled"] >= 1
        _await_orchestrator(built, "default", PHONE)
        rec.drain()

    # (a) the inbound bus events fired (set view — fan-out order isn't asserted).
    assert "message.received" in rec.name_set, \
        f"message.received missing; got {sorted(rec.name_set)}"
    assert "message.saved" in rec.name_set, \
        f"message.saved missing; got {sorted(rec.name_set)}"

    # (b) the message was persisted (AI off → no reply, but inbound is saved).
    contact = contact_repo.get_by_phone(PHONE)
    assert contact is not None, "contact must be materialized from the inbound"
    msgs = message_repo.get_all(contact["id"])
    assert any(m["content"] == "olá kit de caracterização" and m["role"] == "user"
               for m in msgs), f"inbound not persisted; got {[m['content'] for m in msgs]}"

    # (c) FakeGowaClient: no outbound send happened (AI gated off → no crash).
    assert built.gowa_client.sent == [], \
        f"expected no outbound; got {built.gowa_client.sent}"


def test_fake_agent_reply_drives_outbound(build_app):
    """With AI on + a stubbed agent, the gowa flow sends a deterministic reply.

    Exercises FakeGowaClient.sent (outbound capture) + fake_agent_reply (LLM
    stub) together — the seam A2's golden will lean on for reply assertions.
    """
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": True,
        "message_batch_delay": 0,
        "response_delay_min": 0,
        "response_delay_max": 0,
        "split_message_delay": 0,
    })
    phone = "5511955550099"
    jid = f"{phone}@s.whatsapp.net"

    with fake_agent_reply("resposta determinística do kit") as agent_rec:
        built.client.post("/api/webhook/gowa/default", json={
            "event": "message",
            "payload": {"from": jid, "id": "smoke_msg_2",
                        "body": "preciso de ajuda", "from_name": "Cliente"},
        })
        _await_orchestrator(built, "default", phone)

    # The stubbed agent was invoked exactly for this turn.
    assert agent_rec.calls, "fake_agent_reply should have been called"
    # The reply was routed out through the gowa channel → recorded on the fake.
    assert any("resposta determinística do kit" in rec.get("text", "")
               for rec in built.gowa_client.messages), \
        f"reply not sent; outbound={built.gowa_client.sent}"

    # Reset the global auto_reply so we don't leak state to other tests on the
    # shared DB (build_test_app config lives in the process-global DB).
    built.settings.set("auto_reply", False)


def test_normalizer_and_golden_helpers(tmp_path):
    """normalize() strips non-determinism; golden_assert round-trips a file."""
    raw = {
        "phone": "5511955550042",
        "msg_id": "abc-123",
        "reply_to_msg_id": "abc-123",   # same id → same placeholder
        "external_msg_id": "zzz-999",   # different id → different placeholder
        "ts": 1717000000.123,
        "latency_ms": 42,
        "nested": [{"created_at": 1717000001.0, "text": "oi"}],
    }
    norm = normalize(raw)
    assert norm["ts"] == "<TS>"
    assert norm["latency_ms"] == "<MS>"
    assert norm["msg_id"] == norm["reply_to_msg_id"]      # identity preserved
    assert norm["msg_id"] != norm["external_msg_id"]      # distinct ids distinct
    assert norm["nested"][0]["created_at"] == "<TS>"
    assert norm["nested"][0]["text"] == "oi"              # real data untouched

    # golden_assert writes when update=True, then matches on re-read.
    import tests.characterization.golden as g
    orig = g.GOLDENS_DIR
    g.GOLDENS_DIR = tmp_path
    try:
        golden_assert("smoke_norm", norm, update=True)     # write
        golden_assert("smoke_norm", norm, update=False)    # match → no raise
        # A real mismatch must raise.
        bad = dict(norm)
        bad["phone"] = "different"
        raised = False
        try:
            golden_assert("smoke_norm", bad, update=False)
        except AssertionError:
            raised = True
        assert raised, "golden_assert must raise on mismatch"
    finally:
        g.GOLDENS_DIR = orig
