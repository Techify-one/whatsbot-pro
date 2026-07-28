"""Plano 27 — status de conexão do canal GOWA + reconnect/logout por canal.

Cobre os dois bugs do card de Canais (GOWA):
  - Bug #2 (status): ``GOWAChannel.status()`` deve refletir o **device**
    (``connection_state()``), não o subprocesso (``manager.is_running``). Com
    um ``gowa_manager`` MagicMock (``is_running`` truthy), o status ainda tem de
    devolver ``connected=False`` quando o device está offline — prova de que não
    depende mais do subprocesso.
  - Bug #1 (QR): ``GOWAClient.get_qr_code()`` só pula o QR quando o **socket**
    está vivo (``connection_state().connected``), não quando há apenas sessão
    salva (degate — D3).

E os endpoints novos ``POST /api/channels/{id}/reconnect|logout`` agindo no
device certo, com ``not_gowa``→400 / canal inexistente→404.

O canal ``default`` é seed (migration 0011, provider gowa, device ``whatsbot``)
e reusa o client singleton injetado — então injetamos um stub controlável via
``build_app(..., gowa_client=stub)`` e o registramos "live" batendo primeiro num
endpoint que faz ``register_live`` on-demand.
"""

from __future__ import annotations

from tests.fakes import FakeGowaClient


# ── Stub controlável do client GOWA ──────────────────────────────────────────

class _StatefulGowaClient(FakeGowaClient):
    """FakeGowaClient + ``connection_state``/``reconnect``/``logout`` reais e
    inspecionáveis. ``state`` é mutável entre chamadas."""

    def __init__(self, connected=False, logged_in=False):
        super().__init__()
        self.state = {"connected": connected, "logged_in": logged_in}
        self.reconnect_calls = 0
        self.logout_calls = 0

    def connection_state(self) -> dict:
        return dict(self.state)

    def get_own_number(self) -> str:
        return "5544999990001" if self.state.get("logged_in") else ""

    def reconnect(self):
        self.reconnect_calls += 1
        return {"results": "ok"}

    def logout(self):
        self.logout_calls += 1
        return {"results": "ok"}


def _register_default(built):
    """Force the ``default`` GOWA channel live (register_live on-demand) by
    hitting an endpoint, so ``status()`` reads the live instance, not row flags."""
    r = built.client.post("/api/channels/default/reconnect")
    assert r.status_code == 200, r.text


# ── Bug #2: status vem do device, não do subprocesso ─────────────────────────

def test_status_connected_reflects_device(build_app):
    stub = _StatefulGowaClient(connected=True, logged_in=True)
    built = build_app(["gowa"], gowa_client=stub)
    _register_default(built)
    r = built.client.get("/api/channels/default/status")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["connected"] is True
    assert data["logged_in"] is True
    assert data["needs_qr"] is False
    assert data["own_phone"] == "5544999990001"


def test_status_paired_but_offline(build_app):
    """Caso ``teste_gowa``: autenticado mas socket caído → connected=False,
    logged_in=True, needs_qr=False. O gowa_manager é MagicMock (is_running
    truthy), provando que status() ignora o subprocesso."""
    stub = _StatefulGowaClient(connected=False, logged_in=True)
    built = build_app(["gowa"], gowa_client=stub)
    _register_default(built)
    r = built.client.get("/api/channels/default/status")
    data = r.json()["data"]
    assert data["connected"] is False
    assert data["logged_in"] is True
    assert data["needs_qr"] is False


def test_status_logged_out_needs_qr(build_app):
    stub = _StatefulGowaClient(connected=False, logged_in=False)
    built = build_app(["gowa"], gowa_client=stub)
    _register_default(built)
    r = built.client.get("/api/channels/default/status")
    data = r.json()["data"]
    assert data["connected"] is False
    assert data["logged_in"] is False
    assert data["needs_qr"] is True


# ── reconnect / logout por canal (device certo) ──────────────────────────────

def test_reconnect_calls_client(build_app):
    stub = _StatefulGowaClient(connected=False, logged_in=True)
    built = build_app(["gowa"], gowa_client=stub)
    r = built.client.post("/api/channels/default/reconnect")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert stub.reconnect_calls >= 1


def test_logout_calls_client(build_app):
    stub = _StatefulGowaClient(connected=True, logged_in=True)
    built = build_app(["gowa"], gowa_client=stub)
    r = built.client.post("/api/channels/default/logout")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert stub.logout_calls >= 1


def test_reconnect_channel_missing(build_app):
    built = build_app(["gowa"])
    r = built.client.post("/api/channels/nope_inexistente/reconnect")
    assert r.status_code == 404, r.text


def test_reconnect_not_gowa(build_app):
    from db.repositories import channel_repo
    if channel_repo.get("p27_cloud") is None:
        channel_repo.create(id="p27_cloud", provider="whatsapp_cloud",
                            display_name="cloud", enabled=1)
    built = build_app(["gowa"])
    r = built.client.post("/api/channels/p27_cloud/reconnect")
    assert r.status_code == 400, r.text


# ── Degate do QR (D3): só pula QR quando o socket está vivo ───────────────────

def test_get_qr_degate_paired_offline_still_requests():
    """connection_state={connected:False, logged_in:True} NÃO curto-circuita —
    o fluxo chega a GET /app/login (não pula por sessão salva)."""
    from gowa.client import GOWAClient
    c = GOWAClient(port=3000)
    c._device_ready = True
    c.connection_state = lambda: {"connected": False, "logged_in": True}
    calls = []

    def fake_request(method, path, *a, **k):
        calls.append((method, path))
        return None  # /app/login → no data; método devolve None

    c._request = fake_request
    assert c.get_qr_code() is None
    assert ("GET", "/app/login") in calls


def test_get_qr_degate_connected_skips():
    """connection_state={connected:True} pula o QR sem bater em /app/login."""
    from gowa.client import GOWAClient
    c = GOWAClient(port=3000)
    c._device_ready = True
    c.connection_state = lambda: {"connected": True, "logged_in": True}
    calls = []
    c._request = lambda method, path, *a, **k: calls.append((method, path))
    assert c.get_qr_code() is None
    assert calls == []
