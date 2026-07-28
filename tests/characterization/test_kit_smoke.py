"""Smoke test for the characterization kit (Plano 23 · G1-min foundation).

Keeps only the kit-level checks that aren't asserted elsewhere:

* ``build_test_app(["gowa"])`` boots a hermetic app with ONLY the gowa plugin
  loaded + enabled (registry isolation);
* the golden helpers (``normalize`` / ``golden_assert``) strip non-determinism
  and round-trip a golden file.

The end-to-end gowa webhook flow (inbound persist + bus events, fake-agent
outbound) used to live here too, but it is a strict subset of
``test_webhook_characterization.py`` (``test_classify_person`` /
``test_reply_plain_single``), so those two flow tests were removed.
"""

from __future__ import annotations

from tests.characterization.golden import normalize, golden_assert


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
