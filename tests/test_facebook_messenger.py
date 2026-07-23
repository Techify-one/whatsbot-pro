"""Canal Facebook Messenger (plano 46 · sub-plano 02).

Unidade (sem rede/DB) do contrato do provider — descriptor, identidade de dedup,
corpo da Send API, janela de 24h + tag HUMAN_AGENT — e integração (app real +
Postgres de teste) da costura de assinatura 01-A no webhook do core:

* POST com ``X-Hub-Signature-256`` inválido ⇒ ``bad_signature``, nada ingerido;
* POST com assinatura válida ⇒ processado normalmente;
* caracterização: o POST do GOWA (sem ``app_secret``) segue 200 e ingerindo.

    venv/bin/python -m pytest tests/test_facebook_messenger.py -q
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "assets" / "plugin_examples" / "facebook_messenger"
APP_SECRET = "app-secret-fb"


def _load(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, PLUGIN / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


channels_mod = _load("fb_messenger_channels_ut", "channels.py")
FacebookMessengerChannel = channels_mod.FacebookMessengerChannel


def _channel(**creds):
    creds.setdefault("page_id", "PAGE1")
    creds.setdefault("page_access_token", "PAGE_TOKEN")
    return FacebookMessengerChannel("fb_ch", credentials=creds)


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── Descriptor / capabilities / identidade ─────────────────────────────────

def test_descriptor_shape():
    d = FacebookMessengerChannel.provider_descriptor()
    assert d["provider"] == "facebook_messenger"
    assert d["label"] == "Facebook Messenger"
    assert d["capabilities"] == {"needs_qr": False, "templates": False}
    assert d["contact_type"] == "facebook"
    creds = {f["key"]: f for f in d["credential_fields"]}
    assert creds["page_access_token"]["type"] == "secret"
    assert creds["app_secret"]["type"] == "secret"
    assert creds["verify_token"]["type"] == "token_suggest"
    assert creds["page_id"]["required"] is True
    cfg = {f["key"]: f for f in d["config_fields"]}
    assert cfg["human_agent_tag"]["type"] == "bool"
    # O webhook é registrado na Meta pelo próprio plugin ao criar o canal
    # (Graph API), com fallback "cole a URL à mão" quando não dá.
    assert d["post_create"]["kind"] == "autoconfigure"
    assert d["post_create"]["endpoint"] == "/api/plugins/facebook_messenger/autoconfigure"
    assert d["post_create"]["webhook_path"] == "/api/webhook/facebook_messenger/{channel_id}"


def test_capabilities():
    caps = _channel().capabilities
    assert caps.session_window_hours == 24
    assert caps.human_window_hours == 24 * 7    # tag HUMAN_AGENT (humano)
    assert caps.templates is False and caps.qr is False
    assert caps.inbound_route == "path"
    assert set(caps.required_credentials) == {"page_id", "page_access_token",
                                              "verify_token"}


def test_identity_is_page_id():
    ident = FacebookMessengerChannel.identity_from_credentials({"page_id": " PAGE1 "})
    assert (ident.kind, ident.value) == ("page_id", "PAGE1")
    assert FacebookMessengerChannel.identity_from_credentials({"page_id": ""}) is None


def test_contact_type():
    assert FacebookMessengerChannel.contact_type() == "facebook"


# ── Send API (mockando o POST) ─────────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.content = b"x"
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _Client:
    """httpx.Client dublê: grava as chamadas e devolve respostas enfileiradas."""

    calls: list = []
    responses: list = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, params=None, json=None, **kw):
        type(self).calls.append({"url": url, "params": params or {}, "json": json or {}})
        return type(self).responses.pop(0)


@pytest.fixture
def fake_http(monkeypatch):
    _Client.calls = []
    _Client.responses = []
    monkeypatch.setattr(channels_mod.httpx, "Client", _Client)
    monkeypatch.setattr(
        "channels.providers.meta_graph.httpx.Client", _Client)
    return _Client


def test_send_text_body_and_appsecret_proof(fake_http):
    fake_http.responses.append(_Resp(200, {"message_id": "mid_out_1",
                                           "recipient_id": "PSID1"}))
    ch = _channel(app_secret=APP_SECRET)
    res = ch.send_text("PSID1", "olá")
    assert res.ok and res.external_msg_id == "mid_out_1"
    call = fake_http.calls[0]
    assert call["url"].endswith("/me/messages")
    assert call["json"] == {"recipient": {"id": "PSID1"},
                            "messaging_type": "RESPONSE",
                            "message": {"text": "olá"}}
    assert call["params"]["access_token"] == "PAGE_TOKEN"
    assert call["params"]["appsecret_proof"] == hmac.new(
        APP_SECRET.encode(), b"PAGE_TOKEN", hashlib.sha256).hexdigest()


def test_send_media_uses_public_url(fake_http, monkeypatch):
    monkeypatch.setattr("channels.media_urls.public_base_url",
                        lambda: "https://bot.example.com")
    fake_http.responses.append(_Resp(200, {"message_id": "mid_out_2"}))
    res = _channel().send_media("PSID1", "image", "statics/outbox/foto.jpg")
    assert res.ok
    attachment = fake_http.calls[0]["json"]["message"]["attachment"]
    assert attachment["type"] == "image"
    assert attachment["payload"]["url"] == "https://bot.example.com/statics/outbox/foto.jpg"
    assert attachment["payload"]["is_reusable"] is True


def test_send_document_maps_to_file(fake_http, monkeypatch):
    monkeypatch.setattr("channels.media_urls.public_base_url",
                        lambda: "https://bot.example.com")
    fake_http.responses.append(_Resp(200, {"message_id": "mid_out_3"}))
    _channel().send_media("PSID1", "document", "statics/outbox/nota.pdf")
    assert fake_http.calls[0]["json"]["message"]["attachment"]["type"] == "file"


# ── Janela de 24h + tag HUMAN_AGENT (02.2) ─────────────────────────────────

_WINDOW_ERROR = {"error": {
    "message": "This message is sent outside of allowed window.",
    "code": 10, "error_subcode": 2018278}}


def test_outside_window_without_toggle_fails_with_clear_message(fake_http):
    fake_http.responses.append(_Resp(400, _WINDOW_ERROR))
    res = _channel(app_secret=APP_SECRET).send_text("PSID1", "oi")
    assert res.ok is False
    assert "24h" in res.error
    assert len(fake_http.calls) == 1              # não tentou a tag


def test_outside_window_with_toggle_but_ai_conversation_does_not_use_tag(fake_http,
                                                                        monkeypatch):
    """A IA NUNCA pode usar HUMAN_AGENT — sem handoff humano, não há retry."""
    fake_http.responses.append(_Resp(400, _WINDOW_ERROR))
    ch = _channel(app_secret=APP_SECRET, human_agent_tag=True)
    monkeypatch.setattr(ch, "_conversation_with_human", lambda _chat: False)
    res = ch.send_text("PSID1", "oi")
    assert res.ok is False
    assert len(fake_http.calls) == 1


def test_outside_window_with_human_handoff_retries_with_human_agent_tag(fake_http,
                                                                       monkeypatch):
    fake_http.responses.append(_Resp(400, _WINDOW_ERROR))
    fake_http.responses.append(_Resp(200, {"message_id": "mid_tag"}))
    ch = _channel(app_secret=APP_SECRET, human_agent_tag=True)
    monkeypatch.setattr(ch, "_conversation_with_human", lambda _chat: True)
    res = ch.send_text("PSID1", "seguimos por aqui")
    assert res.ok and res.external_msg_id == "mid_tag"
    assert len(fake_http.calls) == 2
    retry = fake_http.calls[1]["json"]
    assert retry["messaging_type"] == "MESSAGE_TAG"
    assert retry["tag"] == "HUMAN_AGENT"


def test_non_window_error_is_not_retried(fake_http, monkeypatch):
    fake_http.responses.append(_Resp(400, {"error": {"message": "Invalid OAuth token",
                                                     "code": 190}}))
    ch = _channel(app_secret=APP_SECRET, human_agent_tag=True)
    monkeypatch.setattr(ch, "_conversation_with_human", lambda _chat: True)
    res = ch.send_text("PSID1", "oi")
    assert res.ok is False and "OAuth" in res.error
    assert len(fake_http.calls) == 1


def test_session_open_human_window(monkeypatch):
    """A janela estendida vale só para o humano — o caminho da IA não a vê."""
    import time as _time

    from channels.outbound import OutboundRouter

    class _Reg:
        def get(self, _cid):
            return _channel()

    router = OutboundRouter(_Reg())
    two_days_ago = _time.time() - 48 * 3600
    assert router.session_open("fb_ch", two_days_ago) is False
    assert router.session_open("fb_ch", two_days_ago, by_human=True) is True
    ten_days_ago = _time.time() - 10 * 24 * 3600
    assert router.session_open("fb_ch", ten_days_ago, by_human=True) is False


# ── Assinatura no webhook do core (01-A, ponta a ponta) ────────────────────

@pytest.fixture
def messenger_app(build_app):
    """App real com um canal facebook_messenger vivo (provider + row + creds)."""
    from db.repositories import channel_credential_repo, channel_repo

    built = build_app(["gowa"])
    registry = built.app.state.deps.channel_registry
    registry.register_provider(FacebookMessengerChannel)
    if channel_repo.get("fb_ch") is None:
        channel_repo.create(id="fb_ch", provider="facebook_messenger",
                            display_name="Página de teste")
    channel_credential_repo.set("fb_ch", "page_id", "PAGE1")
    channel_credential_repo.set("fb_ch", "page_access_token", "PAGE_TOKEN")
    channel_credential_repo.set("fb_ch", "app_secret", APP_SECRET)
    channel_credential_repo.set("fb_ch", "verify_token", "vt-123")
    registry.add_channel("fb_ch", FacebookMessengerChannel("fb_ch", registry=registry))
    return built


def _read_receipt_body() -> bytes:
    # Um "read" não cria contato nem chama LLM — exercita a rota sem efeitos.
    return json.dumps({"object": "page", "entry": [{
        "id": "PAGE1", "time": 1, "messaging": [{
            "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
            "read": {"watermark": 42}}]}]}).encode()


def test_webhook_rejects_bad_signature(messenger_app):
    body = _read_receipt_body()
    r = messenger_app.client.post(
        "/api/webhook/facebook_messenger/fb_ch", content=body,
        headers={"Content-Type": "application/json",
                 "X-Hub-Signature-256": _sign(body, "segredo-errado")})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "bad_signature"


def test_webhook_rejects_missing_signature(messenger_app):
    body = _read_receipt_body()
    r = messenger_app.client.post(
        "/api/webhook/facebook_messenger/fb_ch", content=body,
        headers={"Content-Type": "application/json"})
    assert r.json()["data"]["status"] == "bad_signature"


def test_webhook_accepts_valid_signature(messenger_app):
    body = _read_receipt_body()
    r = messenger_app.client.post(
        "/api/webhook/facebook_messenger/fb_ch", content=body,
        headers={"Content-Type": "application/json",
                 "X-Hub-Signature-256": _sign(body)})
    data = r.json()["data"]
    assert data["status"] == "received"
    assert data["events"] == 1


def test_webhook_handshake_uses_verify_token(messenger_app):
    r = messenger_app.client.get(
        "/api/webhook/facebook_messenger/fb_ch",
        params={"hub.mode": "subscribe", "hub.verify_token": "vt-123",
                "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    r = messenger_app.client.get(
        "/api/webhook/facebook_messenger/fb_ch",
        params={"hub.mode": "subscribe", "hub.verify_token": "errado",
                "hub.challenge": "42"})
    assert r.status_code == 403


def test_gowa_webhook_unchanged_by_signature_seam(messenger_app):
    """Caracterização: canal sem app_secret (GOWA) segue aceitando o POST."""
    r = messenger_app.client.post("/api/webhook/gowa/default", json={
        "event": "message", "payload": {"id": "sig_char_1",
                                        "from": "5511999990001@s.whatsapp.net",
                                        "message": {"text": ""}}})
    assert r.status_code == 200
    assert r.json()["data"]["status"] != "bad_signature"


def test_media_limits_declared():
    """Anexo acima de 25 MB é bloqueado no compositor (plano 65 · provider declara)."""
    from channels.media_limits import validate_upload

    caps = _channel().capabilities
    assert validate_upload("foto.jpg", 1024, caps, "image").reason == "ok"
    assert validate_upload("filme.mp4", 30 * 1024 * 1024, caps,
                           "video").reason == "too_big"


def test_mp4_sent_as_document_goes_out_as_video_attachment(monkeypatch):
    """Regressão: a Meta recusa (#100 / 2018007) um .mp4 declarado como ``file``.

    O painel manda vídeo pelo botão "documento" (kind=document); o tipo do anexo
    tem que sair do MIME real do arquivo, senão o envio falha.
    """
    import channels.media_urls as media_urls

    monkeypatch.setattr(media_urls, "public_base_url", lambda: "https://bot.example.com")
    ch = _channel()
    payload = ch._media_payload("PSID1", "document", "statics/outbox/clip.mp4")
    assert payload["message"]["attachment"]["type"] == "video"
    assert (ch._media_payload("PSID1", "document", "statics/outbox/a.pdf")
            ["message"]["attachment"]["type"] == "file")


def test_window_detector_ignores_unrelated_code_100():
    """``code 100`` (genérico da Graph) não é "fora das 24h" — o casamento por
    substring "code 10" mascarava a causa real do erro na mensagem ao operador."""
    assert channels_mod._is_window_error("(#100) Falha ao carregar (code 100/2018007)") is False
    assert channels_mod._is_window_error("(#100) erro qualquer (code 100)") is False
    assert channels_mod._is_window_error("outside of allowed window (code 10)") is True
    assert channels_mod._is_window_error("erro (code 10/2018278)") is True
