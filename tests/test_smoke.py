"""Smoke tests proving the conftest fixtures wire up the app under pytest.

These are NATIVELY-collected pytest tests (unlike the legacy subprocess suites).
If these pass, the ``client`` fixture (and the engine/seed/app chain behind it)
is correctly bootstrapped for later phases to build on.
"""

from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True


def test_config_endpoint(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert "data" in body


def test_seed_contact_visible(client, seed):
    r = client.get("/api/contacts")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    # Alice (seeded, not archived) should appear in the default listing.
    phones = {c.get("phone") for c in body.get("data", [])}
    assert "5511999990001" in phones
