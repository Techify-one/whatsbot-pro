"""Canal Instagram Direct via login do Facebook (plano 46 · sub-plano 03).

Instagram messaging pela Messenger Platform (``graph.facebook.com`` + Page Access
Token), molde do Chatwoot ``Channel::FacebookPage`` — dedup por ``page_id``, send
com ``messaging_type=RESPONSE``, SEM token IG de 60 dias e SEM loop de refresh.

Unidade (sem rede/DB) do contrato do provider — descriptor, identidade de dedup
(``page_id``), corpo da Send API (COM ``messaging_type``), janela de 24h + tag
HUMAN_AGENT — e integração (app real + Postgres de teste) da costura de assinatura
01-A no webhook do core:

* POST com ``X-Hub-Signature-256`` inválido ⇒ ``bad_signature``, nada ingerido;
* POST com assinatura válida ⇒ processado normalmente.

    venv/bin/python -m pytest tests/test_instagram.py -q
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "assets" / "plugin_examples" / "instagram"
PKG = "whatsbot_plugins.instagram"
APP_SECRET = "app-secret-ig"


# Plano 76 · F9 / sub-plano 03 — a base ``meta_graph`` + ``media_urls`` VIVEM no
# plugin, e ``channels.py`` as importa RELATIVAMENTE. Registramos o plugin como
# PACOTE (molde do runtime e dos testes de messenger/protocolos) para os imports
# relativos resolverem fora do loader real.
def _ensure_pkg() -> None:
    if "whatsbot_plugins" not in sys.modules:
        parent = types.ModuleType("whatsbot_plugins")
        parent.__path__ = []  # namespace package
        sys.modules["whatsbot_plugins"] = parent
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(PLUGIN)]
        sys.modules[PKG] = pkg


def _load(modname: str, filename: str):
    _ensure_pkg()
    full = f"{PKG}.{modname}"
    spec = importlib.util.spec_from_file_location(full, PLUGIN / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod  # antes do exec: o pacote precisa se auto-resolver
    spec.loader.exec_module(mod)
    return mod


channels_mod = _load("channels", "channels.py")
InstagramChannel = channels_mod.InstagramChannel
media_urls_mod = sys.modules[f"{PKG}.media_urls"]
meta_graph_mod = sys.modules[f"{PKG}.meta_graph"]


class _FakeRegistry:
    """Registry mínimo: guarda credenciais e status em memória."""

    def __init__(self, creds=None):
        self._creds = dict(creds or {})
        self.status = {}

    def get_credential(self, _cid, key):
        return self._creds.get(key)

    def set_credential(self, _cid, key, value):
        self._creds[key] = value

    def set_status(self, _cid, **fields):
        self.status.update(fields)

    def get_channel(self, _cid):
        return {}


def _channel(**creds):
    creds.setdefault("page_access_token", "PAGE_TOKEN")
    creds.setdefault("page_id", "PAGE1")
    reg = _FakeRegistry(creds)
    # Creds vão TAMBÉM no dict de credenciais: ``_config_bool`` (human_agent_tag)
    # lê do config do canal ou do bag local, e o fake registry não serve config.
    return InstagramChannel("ig_ch", registry=reg, credentials=creds)


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── Descriptor / capabilities / identidade ─────────────────────────────────

def test_descriptor_shape():
    d = InstagramChannel.provider_descriptor()
    assert d["provider"] == "instagram"
    assert d["label"] == "Instagram"
    assert d["color"] == "pink"
    assert d["capabilities"] == {"needs_qr": False, "templates": False}
    assert d["contact_type"] == "instagram"
    creds = {f["key"]: f for f in d["credential_fields"]}
    # A conexão é por Página do Facebook (login via Facebook), não token IG direto.
    assert creds["page_id"]["type"] == "text" and creds["page_id"]["required"] is True
    assert creds["page_access_token"]["type"] == "secret"
    assert creds["page_access_token"]["required"] is True
    # App Secret é OBRIGATÓRIO: sem ele o webhook não registra sozinho.
    assert creds["app_secret"]["type"] == "secret"
    assert creds["app_secret"]["required"] is True
    assert creds["verify_token"]["type"] == "token_suggest"
    # app_id é OPCIONAL (auto-detectado pelo Page token, como no Messenger)
    assert creds["app_id"]["type"] == "text" and creds["app_id"]["required"] is False
    # não há mais os campos do fluxo antigo (token IG direto)
    assert "access_token" not in creds
    assert "ig_id" not in creds
    cfg = {f["key"]: f for f in d["config_fields"]}
    assert cfg["human_agent_tag"]["type"] == "bool"
    # O webhook é registrado na Meta pelo próprio plugin ao criar o canal.
    assert d["post_create"]["kind"] == "autoconfigure"
    assert d["post_create"]["endpoint"] == "/api/plugins/instagram/autoconfigure"
    assert d["post_create"]["webhook_path"] == "/api/webhook/instagram/{channel_id}"


def test_capabilities():
    caps = _channel().capabilities
    # ⚠️ Sem refresh de token: o Page Access Token não expira por tempo.
    assert caps.token_refresh is False
    assert caps.session_window_hours == 24
    assert caps.human_window_hours == 24 * 7    # tag HUMAN_AGENT (humano)
    assert caps.templates is False and caps.qr is False
    assert caps.inbound_route == "path"
    assert set(caps.required_credentials) == {"page_id", "page_access_token",
                                              "app_secret", "verify_token"}


def test_identity_from_page_id():
    # Dedup pela Página (Chatwoot: Channel::FacebookPage é unique em page_id).
    ident = InstagramChannel.identity_from_credentials({"page_id": " PAGE9 "})
    assert (ident.kind, ident.value) == ("page_id", "PAGE9")
    assert InstagramChannel.identity_from_credentials({"page_id": ""}) is None


def test_contact_type():
    assert InstagramChannel.contact_type() == "instagram"


def test_graph_host_is_facebook():
    # Instagram via login do Facebook ⇒ tudo em graph.facebook.com.
    assert _channel().graph_host == "graph.facebook.com"
    assert _channel()._graph_base().startswith("https://graph.facebook.com/")


# ── HTTP dublê ─────────────────────────────────────────────────────────────

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
        type(self).calls.append({"method": "post", "url": url,
                                 "params": params or {}, "json": json or {}})
        return type(self).responses.pop(0)

    def get(self, url, params=None, **kw):
        type(self).calls.append({"method": "get", "url": url,
                                 "params": params or {}})
        return type(self).responses.pop(0)


@pytest.fixture
def fake_http(monkeypatch):
    _Client.calls = []
    _Client.responses = []
    monkeypatch.setattr(channels_mod.httpx, "Client", _Client)
    monkeypatch.setattr(meta_graph_mod.httpx, "Client", _Client)
    return _Client


# ── Send API (COM messaging_type — send path do Messenger) ─────────────────

def test_send_text_body_has_messaging_type(fake_http):
    """Instagram via login do Facebook reusa o send do Messenger: o corpo LEVA
    ``messaging_type=RESPONSE`` (Chatwoot SendOnFacebookService)."""
    fake_http.responses.append(_Resp(200, {"message_id": "mid_out_1"}))
    ch = _channel(app_secret=APP_SECRET)
    res = ch.send_text("IGSID1", "olá")
    assert res.ok and res.external_msg_id == "mid_out_1"
    call = fake_http.calls[0]
    assert call["url"].endswith("/me/messages")
    assert call["json"] == {"recipient": {"id": "IGSID1"},
                            "messaging_type": "RESPONSE",
                            "message": {"text": "olá"}}
    assert call["params"]["access_token"] == "PAGE_TOKEN"
    assert call["params"]["appsecret_proof"] == hmac.new(
        APP_SECRET.encode(), b"PAGE_TOKEN", hashlib.sha256).hexdigest()


def test_send_media_uses_public_url(fake_http, monkeypatch):
    monkeypatch.setattr(media_urls_mod, "public_base_url",
                        lambda: "https://bot.example.com")
    fake_http.responses.append(_Resp(200, {"message_id": "mid_out_2"}))
    res = _channel().send_media("IGSID1", "image", "statics/outbox/foto.jpg")
    assert res.ok
    body = fake_http.calls[0]["json"]
    attachment = body["message"]["attachment"]
    assert attachment["type"] == "image"
    assert attachment["payload"]["url"] == "https://bot.example.com/statics/outbox/foto.jpg"
    assert body["messaging_type"] == "RESPONSE"


# ── Janela de 24h + tag HUMAN_AGENT (02.2) ─────────────────────────────────

_WINDOW_ERROR = {"error": {
    "message": "This message is sent outside of allowed window.",
    "code": 10, "error_subcode": 2018278}}


def test_outside_window_without_toggle_fails_with_clear_message(fake_http):
    fake_http.responses.append(_Resp(400, _WINDOW_ERROR))
    res = _channel(app_secret=APP_SECRET).send_text("IGSID1", "oi")
    assert res.ok is False
    assert "24h" in res.error
    assert len(fake_http.calls) == 1


def test_outside_window_ai_conversation_does_not_use_tag(fake_http, monkeypatch):
    fake_http.responses.append(_Resp(400, _WINDOW_ERROR))
    ch = _channel(app_secret=APP_SECRET, human_agent_tag=True)
    monkeypatch.setattr(ch, "_conversation_with_human", lambda _chat: False)
    res = ch.send_text("IGSID1", "oi")
    assert res.ok is False
    assert len(fake_http.calls) == 1


def test_outside_window_with_human_handoff_uses_message_tag(fake_http, monkeypatch):
    fake_http.responses.append(_Resp(400, _WINDOW_ERROR))
    fake_http.responses.append(_Resp(200, {"message_id": "mid_tag"}))
    ch = _channel(app_secret=APP_SECRET, human_agent_tag=True)
    monkeypatch.setattr(ch, "_conversation_with_human", lambda _chat: True)
    res = ch.send_text("IGSID1", "seguimos por aqui")
    assert res.ok and res.external_msg_id == "mid_tag"
    assert len(fake_http.calls) == 2
    retry = fake_http.calls[1]["json"]
    # O fallback SOBRESCREVE o RESPONSE por MESSAGE_TAG + HUMAN_AGENT.
    assert retry["messaging_type"] == "MESSAGE_TAG"
    assert retry["tag"] == "HUMAN_AGENT"


def test_oauth_error_flags_reauthorization(fake_http):
    """Erro 190 (token revogado) marca o canal para reautorização."""
    fake_http.responses.append(_Resp(400, {"error": {
        "message": "Error validating access token", "code": 190}}))
    ch = _channel(app_secret=APP_SECRET)
    res = ch.send_text("IGSID1", "oi")
    assert res.ok is False
    assert ch.registry.status.get("logged_in") == 0
    assert "reautorize" in (ch.registry.status.get("last_error") or "").lower()


# ── Status (ping da Página + conta IG conectada) ───────────────────────────

def test_status_shows_connected_instagram_username(fake_http):
    fake_http.responses.append(_Resp(200, {
        "id": "PAGE1", "name": "Minha Loja",
        "instagram_business_account": {"username": "minhaloja"}}))
    st = _channel(app_secret=APP_SECRET).status()
    assert st["connected"] and st["logged_in"]
    assert st["verified_name"] == "@minhaloja"
    call = fake_http.calls[0]
    assert call["url"].endswith("/PAGE1")
    assert "instagram_business_account" in call["params"]["fields"]


# ── Limites de mídia ────────────────────────────────────────────────────────

def test_media_limits_declared():
    from channels.media_limits import validate_upload

    caps = _channel().capabilities
    assert validate_upload("foto.jpg", 1024, caps, "image").reason == "ok"
    # imagem > 8 MB é bloqueada
    assert validate_upload("foto.jpg", 9 * 1024 * 1024, caps,
                           "image").reason == "too_big"
    # vídeo > 25 MB é bloqueado
    assert validate_upload("filme.mp4", 30 * 1024 * 1024, caps,
                           "video").reason == "too_big"


# ── Rotas de configuração do webhook (autoconfigure/subscribe) ─────────────

routes_mod = _load("routes", "routes.py")


@pytest.fixture
def fake_routes_http(monkeypatch):
    _Client.calls = []
    _Client.responses = []
    monkeypatch.setattr(routes_mod.httpx, "Client", _Client)
    return _Client


def test_register_app_webhook_uses_facebook_host_and_app_token(fake_routes_http,
                                                              monkeypatch):
    """O callback é registrado no APP (object=instagram) em graph.facebook.com,
    com o app access token ``{app_id}|{app_secret}`` — app_id explícito evita o
    GET /app."""
    monkeypatch.setattr(routes_mod, "_creds", lambda _cid: {
        "app_id": "APP1", "app_secret": APP_SECRET, "verify_token": "vt-ig",
        "page_access_token": "PAGE_TOKEN", "page_id": "PAGE1"})
    fake_routes_http.responses.append(_Resp(200, {"success": True}))
    ok, err = routes_mod._register_app_webhook(
        "ig_ch", "https://bot.example.com/api/webhook/instagram/ig_ch")
    assert ok and not err
    call = fake_routes_http.calls[0]
    assert call["url"] == "https://graph.facebook.com/v25.0/APP1/subscriptions"
    assert call["params"]["object"] == "instagram"
    assert call["params"]["callback_url"].endswith("/api/webhook/instagram/ig_ch")
    assert call["params"]["access_token"] == "APP1|" + APP_SECRET
    # o app token é auto-assinado — nunca mandar appsecret_proof aqui
    assert "appsecret_proof" not in call["params"]


def test_register_app_webhook_auto_detects_app_id(fake_routes_http, monkeypatch):
    """Sem app_id explícito, é detectado por GET /app com o Page token (Messenger)."""
    monkeypatch.setattr(routes_mod, "_creds", lambda _cid: {
        "app_secret": APP_SECRET, "verify_token": "vt-ig",
        "page_access_token": "PAGE_TOKEN", "page_id": "PAGE1"})
    fake_routes_http.responses.append(_Resp(200, {"id": "APP_AUTO"}))   # GET /app
    fake_routes_http.responses.append(_Resp(200, {"success": True}))    # subscriptions
    ok, err = routes_mod._register_app_webhook(
        "ig_ch", "https://bot.example.com/api/webhook/instagram/ig_ch")
    assert ok and not err
    assert fake_routes_http.calls[0]["url"].endswith("/app")
    assert fake_routes_http.calls[1]["url"] == "https://graph.facebook.com/v25.0/APP_AUTO/subscriptions"


def test_subscribe_uses_facebook_host_and_page(fake_routes_http, monkeypatch):
    """A assinatura é da PÁGINA: ``{page_id}/subscribed_apps`` em graph.facebook.com
    com o Page token."""
    monkeypatch.setattr(routes_mod, "_creds", lambda _cid: {
        "app_secret": APP_SECRET, "page_access_token": "PAGE_TOKEN",
        "page_id": "PAGE1"})
    fake_routes_http.responses.append(_Resp(200, {"success": True}))
    ok, err = routes_mod._subscribe("ig_ch")
    assert ok and not err
    call = fake_routes_http.calls[0]
    assert call["url"] == "https://graph.facebook.com/v25.0/PAGE1/subscribed_apps"
    assert call["params"]["access_token"] == "PAGE_TOKEN"
    assert call["params"]["subscribed_fields"] == routes_mod.PAGE_SUBSCRIBED_FIELDS


def test_field_sets_exclude_advanced_permission_fields():
    """Campos de handover/optins/referral/standby exigem acesso avançado e fariam a
    Meta recusar a chamada inteira com '(#200) permissions' — não devem estar nos
    sets."""
    advanced = {"messaging_handover", "messaging_handovers", "standby",
                "messaging_optins", "messaging_referral"}
    app_fields = set(routes_mod.APP_SUBSCRIBED_FIELDS.split(","))
    page_fields = set(routes_mod.PAGE_SUBSCRIBED_FIELDS.split(","))
    assert "messages" in app_fields and "messages" in page_fields
    assert app_fields.isdisjoint(advanced) and page_fields.isdisjoint(advanced)


def test_subscribe_falls_back_without_fields_on_permission_error(fake_routes_http,
                                                                monkeypatch):
    """Erro de permissão em algum campo ⇒ reassina SEM lista de campos (recebe ao
    menos as DMs), em vez de travar tudo."""
    monkeypatch.setattr(routes_mod, "_creds", lambda _cid: {
        "app_secret": APP_SECRET, "page_access_token": "PAGE_TOKEN",
        "page_id": "PAGE1"})
    fake_routes_http.responses.append(_Resp(400, {"error": {
        "message": "You could not subscribe to some of the fields requested due "
                   "to a permissions error", "code": 200}}))
    fake_routes_http.responses.append(_Resp(200, {"success": True}))
    ok, err = routes_mod._subscribe("ig_ch")
    assert ok and not err
    assert len(fake_routes_http.calls) == 2
    # a 1ª tentativa pede campos; a 2ª (fallback) NÃO manda subscribed_fields
    assert "subscribed_fields" in fake_routes_http.calls[0]["params"]
    assert "subscribed_fields" not in fake_routes_http.calls[1]["params"]


def test_subscribe_falls_back_on_invalid_field_error(fake_routes_http, monkeypatch):
    """Erro de VALIDAÇÃO de campo ('must be one of {…}') também dispara o fallback
    sem campos."""
    monkeypatch.setattr(routes_mod, "_creds", lambda _cid: {
        "app_secret": APP_SECRET, "page_access_token": "PAGE_TOKEN",
        "page_id": "PAGE1"})
    fake_routes_http.responses.append(_Resp(400, {"error": {
        "message": "Param subscribed_fields[2] must be one of {messages, "
                   "messaging_postbacks} - got 'foo'",
        "code": 100}}))
    fake_routes_http.responses.append(_Resp(200, {"success": True}))
    ok, err = routes_mod._subscribe("ig_ch")
    assert ok and not err
    assert "subscribed_fields" not in fake_routes_http.calls[1]["params"]


# ── Diagnóstico (/diagnose) ────────────────────────────────────────────────

def _diag_env(monkeypatch, *, app_secret=APP_SECRET, token_ok=True,
              match="ok", subscribed=True, recent=1):
    monkeypatch.setattr(routes_mod, "_creds",
                        lambda _c: {"app_secret": app_secret,
                                    "page_access_token": "T", "page_id": "PAGE1"})
    monkeypatch.setattr(routes_mod, "_me",
                        lambda _c: (({"id": "PAGE1", "name": "Loja",
                                      "instagram_business_account": {"username": "loja"}}, "")
                                    if token_ok else ({}, "token revogado")))
    monkeypatch.setattr(routes_mod, "_webhook_status", lambda _c, _e="": {
        "page_id": "PAGE1", "subscribed": subscribed, "subscribed_fields":
        (["messages"] if subscribed else []), "match": match,
        "configured_url": "https://x/api/webhook/instagram/c",
        "expected_url": "https://x/api/webhook/instagram/c", "reason": ""})
    monkeypatch.setattr(routes_mod, "_recent_inbound_count", lambda _c: recent)


def test_diagnose_all_ok(monkeypatch):
    _diag_env(monkeypatch)
    d = routes_mod._diagnose("c")
    assert d["token_ok"] and d["account_subscribed"] and d["callback_match"] == "ok"
    assert d["verdict"].startswith("✅")


def test_diagnose_flags_missing_app_secret_first(monkeypatch):
    _diag_env(monkeypatch, app_secret="")
    assert "App Secret" in routes_mod._diagnose("c")["verdict"]


def test_diagnose_flags_bad_token(monkeypatch):
    _diag_env(monkeypatch, token_ok=False)
    assert "Page token inválido" in routes_mod._diagnose("c")["verdict"]


def test_diagnose_flags_callback_mismatch(monkeypatch):
    _diag_env(monkeypatch, match="wrong_domain")
    assert "callback" in routes_mod._diagnose("c")["verdict"].lower()


def test_diagnose_flags_account_not_subscribed(monkeypatch):
    _diag_env(monkeypatch, subscribed=False)
    assert "não está assinada" in routes_mod._diagnose("c")["verdict"]


def test_diagnose_flags_dev_mode_when_no_inbound(monkeypatch):
    """Tudo configurado mas 0 webhooks ⇒ aponta o modo desenvolvimento do app."""
    _diag_env(monkeypatch, recent=0)
    assert "DESENVOLVIMENTO" in routes_mod._diagnose("c")["verdict"]


# ── Assinatura no webhook do core (01-A, ponta a ponta) ────────────────────

@pytest.fixture
def instagram_app(build_app):
    from db.repositories import channel_credential_repo, channel_repo

    built = build_app(["gowa"])
    registry = built.app.state.deps.channel_registry
    registry.register_provider(InstagramChannel)
    if channel_repo.get("ig_ch") is None:
        channel_repo.create(id="ig_ch", provider="instagram",
                            display_name="Conta de teste")
    channel_credential_repo.set("ig_ch", "page_id", "PAGE1")
    channel_credential_repo.set("ig_ch", "page_access_token", "PAGE_TOKEN")
    channel_credential_repo.set("ig_ch", "app_secret", APP_SECRET)
    channel_credential_repo.set("ig_ch", "verify_token", "vt-ig")
    registry.add_channel("ig_ch", InstagramChannel("ig_ch", registry=registry))
    return built


def _read_receipt_body() -> bytes:
    # Um "read" não cria contato nem chama LLM — exercita a rota sem efeitos.
    return json.dumps({"object": "instagram", "entry": [{
        "id": "IGACC", "time": 1, "messaging": [{
            "sender": {"id": "IGSID1"}, "recipient": {"id": "IGACC"},
            "read": {"watermark": 42}}]}]}).encode()


def test_webhook_rejects_bad_signature(instagram_app):
    body = _read_receipt_body()
    r = instagram_app.client.post(
        "/api/webhook/instagram/ig_ch", content=body,
        headers={"Content-Type": "application/json",
                 "X-Hub-Signature-256": _sign(body, "segredo-errado")})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "bad_signature"


def test_webhook_accepts_valid_signature(instagram_app):
    body = _read_receipt_body()
    r = instagram_app.client.post(
        "/api/webhook/instagram/ig_ch", content=body,
        headers={"Content-Type": "application/json",
                 "X-Hub-Signature-256": _sign(body)})
    data = r.json()["data"]
    assert data["status"] == "received"
    assert data["events"] == 1


def test_webhook_handshake_uses_verify_token(instagram_app):
    r = instagram_app.client.get(
        "/api/webhook/instagram/ig_ch",
        params={"hub.mode": "subscribe", "hub.verify_token": "vt-ig",
                "hub.challenge": "77"})
    assert r.status_code == 200 and r.text == "77"
    r = instagram_app.client.get(
        "/api/webhook/instagram/ig_ch",
        params={"hub.mode": "subscribe", "hub.verify_token": "errado",
                "hub.challenge": "77"})
    assert r.status_code == 403
