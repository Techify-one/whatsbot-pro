"""Unit tests for GOWAClient.get_own_number device-scoping (plano 32 F1).

No network / no DB: ``get_status`` and ``list_devices`` are monkeypatched. Proves
that a per-channel client bound to device A never returns device B's number, that
``@s.whatsapp.net`` wins over ``@lid``, and that no match yields "".
"""
from __future__ import annotations

from gowa.client import GOWAClient


def _client(device_id: str) -> GOWAClient:
    c = GOWAClient(port=3000)
    c.device_id = device_id
    c.strict_device = True
    c._device_ready = True  # avoid ensure_device() network calls in get_status
    return c


def test_devices_fallback_is_device_scoped(monkeypatch):
    """/app/status empty → /devices fallback must pick THIS device, not the first."""
    devices = [
        {"id": "chanB", "device": "5511888880002@s.whatsapp.net"},
        {"id": "chanA", "device": "5511999990001@s.whatsapp.net"},
    ]
    ca = _client("chanA")
    monkeypatch.setattr(ca, "get_status", lambda: None)
    monkeypatch.setattr(ca, "list_devices", lambda: devices)
    assert ca.get_own_number() == "5511999990001"

    cb = _client("chanB")
    monkeypatch.setattr(cb, "get_status", lambda: None)
    monkeypatch.setattr(cb, "list_devices", lambda: devices)
    assert cb.get_own_number() == "5511888880002"


def test_no_matching_device_returns_empty(monkeypatch):
    """A device_id absent from /devices → "" (never adopt another's number)."""
    devices = [{"id": "chanX", "device": "5511777770003@s.whatsapp.net"}]
    c = _client("chanA")
    monkeypatch.setattr(c, "get_status", lambda: None)
    monkeypatch.setattr(c, "list_devices", lambda: devices)
    assert c.get_own_number() == ""


def test_prefers_swhatsapp_over_lid(monkeypatch):
    """When an entry carries both @lid and @s.whatsapp.net, the number wins."""
    devices = [{
        "id": "chanA",
        "jid": "111122223333@lid",
        "device": "5511999990001@s.whatsapp.net",
    }]
    c = _client("chanA")
    monkeypatch.setattr(c, "get_status", lambda: None)
    monkeypatch.setattr(c, "list_devices", lambda: devices)
    assert c.get_own_number() == "5511999990001"


def test_status_probe_prefers_swhatsapp_over_lid(monkeypatch):
    """/app/status is device-scoped; @s.whatsapp.net still preferred over @lid."""
    status = {"results": {"jid": "111122223333@lid",
                          "device": "5511999990001@s.whatsapp.net"}}
    c = _client("chanA")
    monkeypatch.setattr(c, "get_status", lambda: status)
    monkeypatch.setattr(c, "list_devices", lambda: [])
    assert c.get_own_number() == "5511999990001"


def test_status_probe_short_circuits_devices(monkeypatch):
    """If /app/status resolves, the global /devices list is never consulted."""
    c = _client("chanA")
    monkeypatch.setattr(c, "get_status",
                        lambda: {"results": {"jid": "5511999990001@s.whatsapp.net"}})

    def _boom():
        raise AssertionError("list_devices must not be called when /app/status resolves")

    monkeypatch.setattr(c, "list_devices", _boom)
    assert c.get_own_number() == "5511999990001"


def test_strips_device_suffix(monkeypatch):
    """JID user-part with a :device suffix (5511...:12@...) → digits only."""
    c = _client("chanA")
    monkeypatch.setattr(c, "get_status",
                        lambda: {"results": {"jid": "5511999990001:7@s.whatsapp.net"}})
    monkeypatch.setattr(c, "list_devices", lambda: [])
    assert c.get_own_number() == "5511999990001"
