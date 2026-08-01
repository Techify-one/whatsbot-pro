"""Website widget channel (plano 46 · sub-plano 05).

Unit coverage (no DB) of the provider contract + the pure session/domain/HMAC
helpers + the WS hub isolation, plus integration coverage (``build_app`` → the
Postgres test engine) of the two core seams this plugin needs:

* ``/api/plugins/<id>/public/`` is auth-exempt (the visitor reaches config +
  widget without the operator bearer);
* the embeddable widget page sets its OWN ``Content-Security-Policy`` with a
  per-channel ``frame-ancestors`` (allowed_domains) and the core middleware
  RESPECTS it (no ``X-Frame-Options: DENY`` override).

    venv/bin/python -m pytest tests/test_website_widget.py -q
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from tests.plugin_test_utils import load_plugin_module, resolve_plugin_source

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = resolve_plugin_source("website")
_PACKAGE = "website_widget_ut"


def _load(mod_name: str, filename: str):
    del mod_name  # compatibility with the existing call sites below
    return load_plugin_module(
        "website", Path(filename).stem, package_name=_PACKAGE,
    )


channels_mod = _load("website_channels_ut", "channels.py")
sessions_mod = _load("website_sessions_ut", "sessions.py")
bridge_mod = _load("website_bridge_ut", "bridge.py")
WebsiteChannel = channels_mod.WebsiteChannel


# ── Provider descriptor + contract ─────────────────────────────────────────

def test_descriptor_shape():
    d = WebsiteChannel.provider_descriptor()
    assert d["provider"] == "website"
    assert d["color"] == "teal"
    assert d["post_create"]["kind"] == "embed_snippet"
    cfg_keys = {f["key"]: f for f in d["config_fields"]}
    assert cfg_keys["widget_token"]["type"] == "generated"
    assert cfg_keys["widget_token"]["prefix"] == "wgt_"
    assert "allowed_domains" in cfg_keys
    assert cfg_keys["rate_limit_enabled"]["type"] == "bool"
    cred_keys = {f["key"] for f in d["credential_fields"]}
    assert "hmac_token" in cred_keys


def test_capabilities():
    ch = WebsiteChannel("c1")
    assert ch.capabilities.presence is True    # inverse presence → visitor
    assert ch.capabilities.media is True
    assert ch.capabilities.required_credentials == ()  # never born dead
    assert ch.status()["connected"] is True


def test_parse_inbound_message():
    ch = WebsiteChannel("c1")
    evs = ch.parse_inbound({"chat_id": "wsess_abc", "text": "olá", "msg_id": "m1"})
    assert len(evs) == 1
    ev = evs[0]
    assert ev.kind == "message" and ev.direction == "in"
    assert ev.chat_id == "wsess_abc" and ev.sender_id == "wsess_abc"
    assert ev.text == "olá" and ev.external_msg_id == "m1"
    assert ev.provider == "website" and ev.channel_id == "c1"


def test_parse_inbound_empty_dropped():
    ch = WebsiteChannel("c1")
    assert ch.parse_inbound({"chat_id": "x"}) == []          # no text/media
    assert ch.parse_inbound({"text": "hi"}) == []            # no chat_id
    assert ch.parse_inbound("nope") == []


def test_send_text_delivers_to_hub_with_same_id():
    ch = WebsiteChannel("c1")
    calls = []

    class FakeHub:
        def deliver(self, token, payload):
            calls.append((token, payload)); return True

    orig = WebsiteChannel.__dict__["_hub"]
    WebsiteChannel._hub = staticmethod(lambda: FakeHub())
    try:
        r = ch.send_text("wsess_x", "resposta")
    finally:
        WebsiteChannel._hub = orig
    assert r.ok is True and r.external_msg_id.startswith("web_")
    token, payload = calls[0]
    assert token == "wsess_x"
    assert payload["role"] == "assistant" and payload["content"] == "resposta"
    assert payload["msg_id"] == r.external_msg_id   # WS id == the id the pipeline saves


def test_media_url_normalization():
    f = channels_mod._to_browser_url
    # operator media arrives as an ABSOLUTE fs path → root-relative /statics/... URL
    assert f("/app/statics/outbox/x.png") == "/statics/outbox/x.png"
    assert f("statics/media/y.jpg") == "/statics/media/y.jpg"
    assert f("https://cdn.example/z.png") == "https://cdn.example/z.png"
    assert f("") == ""


def test_send_presence_delivers_typing():
    ch = WebsiteChannel("c1")
    calls = []

    class FakeHub:
        def deliver(self, token, payload):
            calls.append((token, payload)); return True

    orig = WebsiteChannel.__dict__["_hub"]
    WebsiteChannel._hub = staticmethod(lambda: FakeHub())
    try:
        ch.send_presence("wsess_x", "composing")
        ch.send_presence("wsess_x", "paused")
    finally:
        WebsiteChannel._hub = orig
    assert calls[0][1] == {"type": "typing", "state": "composing", "ts": calls[0][1]["ts"]}
    assert calls[1][1]["state"] == "paused"


# ── Pure session/domain/HMAC helpers ───────────────────────────────────────

def test_parse_allowed_domains():
    assert sessions_mod.parse_allowed_domains("") == []
    assert sessions_mod.parse_allowed_domains(
        "http://localhost:5500, https://x.com/ ") == ["http://localhost:5500", "https://x.com"]
    assert sessions_mod.parse_allowed_domains(["a.com", "a.com"]) == ["a.com"]


def test_frame_ancestors_empty_is_wildcard():
    assert sessions_mod.frame_ancestors_value([]) == "*"


def test_frame_ancestors_set_includes_self_and_variants():
    fa = sessions_mod.frame_ancestors_value(["localhost:5500", "https://x.com"])
    assert fa.startswith("'self'")
    assert "http://localhost:5500" in fa and "https://localhost:5500" in fa
    assert "https://x.com" in fa


def test_csp_has_frame_ancestors_and_connect():
    csp = sessions_mod.content_security_policy(["x.com"])
    assert "frame-ancestors" in csp and "connect-src 'self' ws: wss:" in csp


def test_hmac_roundtrip():
    h = sessions_mod.compute_identifier_hash("secret", "user@x.com")
    assert sessions_mod.verify_identifier_hash("secret", "user@x.com", h) is True
    assert sessions_mod.verify_identifier_hash("secret", "user@x.com", "bad") is False
    assert sessions_mod.verify_identifier_hash("", "user@x.com", h) is False


def test_rate_limit_toggle_off_never_blocks():
    for _ in range(500):
        assert sessions_mod.allow_new_session("1.2.3.4", enabled=False) is True


# ── WS hub isolation (the security-critical property) ───────────────────────

class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, data):
        self.sent.append(data)

    async def close(self):
        pass


def test_hub_isolation_between_sessions():
    async def _run():
        hub = bridge_mod.WsHub()
        a, b = _FakeWS(), _FakeWS()
        hub.register("A", a)
        hub.register("B", b)
        await hub._push("A", {"x": 1})
        return a.sent, b.sent

    sent_a, sent_b = asyncio.run(_run())
    assert len(sent_a) == 1 and json.loads(sent_a[0]) == {"x": 1}
    assert sent_b == []            # session B never sees session A's message


def test_hub_deliver_without_socket_is_false():
    hub = bridge_mod.WsHub()
    assert hub.deliver("nobody", {"x": 1}) is False


def test_hub_drops_dead_socket():
    async def _run():
        hub = bridge_mod.WsHub()

        class Dead:
            async def send_text(self, data):
                raise RuntimeError("closed")

        hub.register("A", Dead())
        await hub._push("A", {"x": 1})
        return hub.has_session("A")

    assert asyncio.run(_run()) is False   # broken socket unregistered → session gone


# ── Core seam: /public/ auth-exempt regex (module-level, importable) ────────

def test_plugin_public_path_regex():
    from server.app import PLUGIN_PUBLIC_PATH_RE as RE
    assert RE.match("/api/plugins/website/public/config")
    assert RE.match("/api/plugins/website/public/ws")
    assert RE.match("/api/plugins/x/public/anything")
    assert not RE.match("/api/plugins/website/channels")     # operator route, protected
    assert not RE.match("/api/plugins/website/public")       # needs trailing slash
    assert not RE.match("/api/config")


# ── Integration (build_app → Postgres test engine) ─────────────────────────

def _create_website_channel(client, token: str):
    # A UNIQUE widget_token per test: the shared test DB accumulates channels
    # across test functions, and find_channel_by_widget_token returns the FIRST
    # enabled match — a reused token would resolve to an earlier test's channel.
    body = {
        "provider": "website",
        "display_name": f"Loja {token}",
        "config": {
            "widget_token": token,
            "allowed_domains": "http://localhost:5500, https://loja.example",
            "welcome_title": "Fale conosco",
            "welcome_tagline": "Já respondemos",
            "widget_color": "#123456",
            "hmac_mandatory": False,
            "rate_limit_enabled": True,
        },
    }
    r = client.post("/api/channels", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"], data
    return data["data"]["id"]


def test_public_config_mints_session(build_app):
    token = "wgt_ut_config"
    built = build_app(["gowa", "website"])
    _create_website_channel(built.client, token)
    r = built.client.get(f"/api/plugins/website/public/config?widget_token={token}")
    assert r.status_code == 200, r.text          # auth-exempt public route reached
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["session_token"].startswith("wsess_")
    assert body["data"]["messages"] == []
    assert body["data"]["config"]["title"] == "Fale conosco"


def test_public_config_unknown_token(build_app):
    built = build_app(["gowa", "website"])
    r = built.client.get("/api/plugins/website/public/config?widget_token=wgt_does_not_exist")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_widget_page_csp_allows_configured_domains(build_app):
    token = "wgt_ut_csp"
    built = build_app(["gowa", "website"])
    _create_website_channel(built.client, token)
    r = built.client.get(f"/api/plugins/website/public/widget?widget_token={token}")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "frame-ancestors" in csp
    assert "http://localhost:5500" in csp        # per-channel allow-list honored
    # The core middleware respected the route CSP → no blanket frame lock.
    assert r.headers.get("x-frame-options", "").upper() != "DENY"
    assert "widget.js" in r.text                 # the iframe app boots


def test_widget_page_unknown_token_is_locked(build_app):
    built = build_app(["gowa", "website"])
    r = built.client.get("/api/plugins/website/public/widget?widget_token=nope")
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")


def test_operator_channels_route_lists_widget(build_app):
    token = "wgt_ut_oper"
    built = build_app(["gowa", "website"])
    cid = _create_website_channel(built.client, token)
    r = built.client.get("/api/plugins/website/channels")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    ch = next((c for c in data["channels"] if c["id"] == cid), None)
    assert ch is not None
    assert ch["widget_token"] == token
    assert ch["hmac_set"] is False


def test_public_messages_ingests_into_funnel(build_app):
    token = "wgt_ut_msg"
    built = build_app(["gowa", "website"])
    cid = _create_website_channel(built.client, token)
    # Mint a session first (config), then post a message.
    cfg = built.client.get(
        f"/api/plugins/website/public/config?widget_token={token}").json()
    session = cfg["data"]["session_token"]

    # build_test_app runs a no-op lifespan, so set_channel_runtime never ran.
    # Wire a minimal runtime (registry with the live channel + a recording ingest)
    # so the route's parse_inbound → ingest glue is exercised. Restore afterwards.
    from channels.registry import ChannelRegistry
    from plugins import context as pctx

    reg = ChannelRegistry()
    reg.register_provider(WebsiteChannel)
    reg.add_channel(cid, WebsiteChannel(cid, reg))
    recorded = []

    async def _fake_ingest(ev):
        recorded.append(ev)

    prev = pctx.get_channel_runtime()
    pctx.set_channel_runtime(reg, None, _fake_ingest)
    try:
        r = built.client.post(
            "/api/plugins/website/public/messages",
            headers={"X-Session-Token": session},
            json={"text": "quero um orçamento"})
    finally:
        pctx.set_channel_runtime(*prev)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["data"]["msg_id"]
    assert len(recorded) == 1
    assert recorded[0].text == "quero um orçamento"
    assert recorded[0].chat_id == session
    assert recorded[0].channel_id == cid
