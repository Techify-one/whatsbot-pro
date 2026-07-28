"""Plano 26 — saúde do webhook do WhatsApp Cloud (read/set/delete override).

Exercita os endpoints do plugin ``whatsapp_cloud`` montados em
``/api/plugins/whatsapp_cloud/*`` através do app hermético (``build_app``), com a
Graph API da Meta MOCKADA (monkeypatch de ``httpx.Client`` — o ``routes.py`` faz
``import httpx`` e chama ``httpx.Client(...)``, então trocar o atributo cobre os
3 helpers ``_graph_get``/``_graph_post``/``_graph_delete`` sem depender do nome
dinâmico do módulo do plugin).

Casos cobertos (§6 do plano):
  read → ok / wrong_domain / wrong_path (caso real ``…/whatsapp_oficial_meta``) /
  unset (sem override) / unknown (sem access_token, sem chamada Graph);
  set → sucesso (re-read confirma ok) / erro Graph / sem waba_id (can_set=false);
  regressão → ``GET /api/channels/{id}/status`` não ganhou chamada Graph de
  webhook (continua só o ``GET /{phone_number_id}``) — guarda o P1.
"""

from __future__ import annotations

import httpx

EXPECTED_HOST = "https://meu.dominio.com"


# ── Fake httpx ──────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.text = text or ""
        self.content = b"x" if (json_data is not None or text) else b""

    def json(self):
        return self._json


class _FakeClient:
    """Context-manager httpx.Client double. Dispatches to a ``handler`` and records
    every call into ``calls`` for assertions."""

    def __init__(self, handler, calls, *a, **k):
        self._handler = handler
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, params=None):
        self._calls.append(("GET", url, params or {}, None))
        return self._handler("GET", url, params or {}, None)

    def post(self, url, headers=None, json=None):
        self._calls.append(("POST", url, {}, json))
        return self._handler("POST", url, {}, json)

    def delete(self, url, headers=None):
        self._calls.append(("DELETE", url, {}, None))
        return self._handler("DELETE", url, {}, None)


def _patch_httpx(monkeypatch, handler):
    """Patch ``httpx.Client`` with the fake; return the shared ``calls`` list."""
    calls: list = []
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **k: _FakeClient(handler, calls, *a, **k))
    return calls


# ── Channel setup ────────────────────────────────────────────────────────────

def _make_cloud_channel(channel_id: str, creds: dict) -> None:
    from db.repositories import channel_repo, channel_credential_repo
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    for k, v in creds.items():
        channel_credential_repo.set(channel_id, k, v)


def _full_creds() -> dict:
    return {
        "access_token": "TOKEN_SECRET",
        "phone_number_id": "PNID123",
        "waba_id": "WABA123",
        "verify_token": "VERIFY_SECRET",
    }


# ── read: ok ─────────────────────────────────────────────────────────────────

def test_read_ok(build_app, monkeypatch):
    cid = "p26_ok"
    expected = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/{cid}"
    _make_cloud_channel(cid, _full_creds())

    def handler(method, url, params, json):
        # primary read: webhook_configuration reflects the WABA override == expected
        return _Resp(200, {"webhook_configuration": {"whatsapp_business_account": expected}})

    built = build_app(["whatsapp_cloud"])
    _patch_httpx(monkeypatch, handler)
    r = built.client.get(
        f"/api/plugins/whatsapp_cloud/webhook-status?channel_id={cid}&expected_url={expected}")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["match"] == "ok", data
    assert data["configured_url"] == expected
    assert data["can_set"] is True
    # secrets never echoed
    assert "TOKEN_SECRET" not in r.text and "VERIFY_SECRET" not in r.text


# ── read: wrong_domain ───────────────────────────────────────────────────────

def test_read_wrong_domain(build_app, monkeypatch):
    cid = "p26_wd"
    expected = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/{cid}"
    other = f"https://outro.dominio.com/api/webhook/whatsapp_cloud/{cid}"
    _make_cloud_channel(cid, _full_creds())

    handler = lambda m, u, p, j: _Resp(  # noqa: E731
        200, {"webhook_configuration": {"whatsapp_business_account": other}})
    built = build_app(["whatsapp_cloud"])
    _patch_httpx(monkeypatch, handler)
    r = built.client.get(
        f"/api/plugins/whatsapp_cloud/webhook-status?channel_id={cid}&expected_url={expected}")
    data = r.json()["data"]
    assert data["match"] == "wrong_domain", data
    assert data["configured_url"] == other


# ── read: wrong_path (caso real: mesmo host, channel_id diferente) ───────────

def test_read_wrong_path(build_app, monkeypatch):
    cid = "teste"
    expected = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/{cid}"
    # same host, DIFFERENT channel_id → inbound cairia em unknown_channel
    other = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/whatsapp_oficial_meta"
    _make_cloud_channel(cid, _full_creds())

    handler = lambda m, u, p, j: _Resp(  # noqa: E731
        200, {"webhook_configuration": {"whatsapp_business_account": other}})
    built = build_app(["whatsapp_cloud"])
    _patch_httpx(monkeypatch, handler)
    r = built.client.get(
        f"/api/plugins/whatsapp_cloud/webhook-status?channel_id={cid}&expected_url={expected}")
    data = r.json()["data"]
    assert data["match"] == "wrong_path", data


# ── read: unset (sem override; primário responde vazio) ──────────────────────

def test_read_unset(build_app, monkeypatch):
    cid = "p26_unset"
    expected = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/{cid}"
    _make_cloud_channel(cid, _full_creds())

    def handler(method, url, params, json):
        if "subscribed_apps" in url:
            return _Resp(200, {"data": [{"whatsapp_business_api_data": {}}]})  # no override
        return _Resp(200, {"webhook_configuration": {}})  # primary empty

    built = build_app(["whatsapp_cloud"])
    _patch_httpx(monkeypatch, handler)
    r = built.client.get(
        f"/api/plugins/whatsapp_cloud/webhook-status?channel_id={cid}&expected_url={expected}")
    data = r.json()["data"]
    assert data["match"] == "unset", data
    assert data["configured_url"] is None


# ── read: unknown (sem access_token ⇒ nenhuma chamada Graph) ──────────────────

def test_read_unknown_no_token(build_app, monkeypatch):
    cid = "p26_notoken"
    expected = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/{cid}"
    _make_cloud_channel(cid, {"phone_number_id": "PNID", "waba_id": "WABA"})  # no token

    built = build_app(["whatsapp_cloud"])
    calls = _patch_httpx(monkeypatch, lambda *a: _Resp(200, {}))
    r = built.client.get(
        f"/api/plugins/whatsapp_cloud/webhook-status?channel_id={cid}&expected_url={expected}")
    data = r.json()["data"]
    assert data["match"] == "unknown", data
    assert data["can_set"] is False  # no verify_token
    assert calls == [], "must not hit the Graph API without an access_token"


# ── set: sucesso (re-read confirma ok) ───────────────────────────────────────

def test_set_webhook_success(build_app, monkeypatch):
    cid = "p26_set"
    expected = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/{cid}"
    _make_cloud_channel(cid, _full_creds())

    def handler(method, url, params, json):
        if method == "POST" and "subscribed_apps" in url:
            assert json["override_callback_uri"] == expected
            assert json["verify_token"] == "VERIFY_SECRET"  # always the saved token
            return _Resp(200, {"success": True})
        # re-read after set → now configured == expected
        return _Resp(200, {"webhook_configuration": {"whatsapp_business_account": expected}})

    built = build_app(["whatsapp_cloud"])
    _patch_httpx(monkeypatch, handler)
    r = built.client.post("/api/plugins/whatsapp_cloud/set-webhook",
                          json={"channel_id": cid, "url": expected})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    assert body["data"]["match"] == "ok", body


# ── set: erro da Graph é propagado sem 500 ───────────────────────────────────

def test_set_webhook_graph_error(build_app, monkeypatch):
    cid = "p26_seterr"
    expected = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/{cid}"
    _make_cloud_channel(cid, _full_creds())

    def handler(method, url, params, json):
        return _Resp(400, {"error": {"message": "URL não é pública"}}, text="err")

    built = build_app(["whatsapp_cloud"])
    _patch_httpx(monkeypatch, handler)
    r = built.client.post("/api/plugins/whatsapp_cloud/set-webhook",
                          json={"channel_id": cid, "url": expected})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "URL não é pública" in (body["error"] or "")


# ── set: sem waba_id ⇒ bloqueado (can_set=false), sem chamada Graph ──────────

def test_set_webhook_without_waba(build_app, monkeypatch):
    cid = "p26_nowaba"
    expected = f"{EXPECTED_HOST}/api/webhook/whatsapp_cloud/{cid}"
    _make_cloud_channel(cid, {"access_token": "T", "phone_number_id": "P",
                              "verify_token": "V"})  # no waba_id

    built = build_app(["whatsapp_cloud"])
    calls = _patch_httpx(monkeypatch, lambda *a: _Resp(200, {}))
    r = built.client.post("/api/plugins/whatsapp_cloud/set-webhook",
                          json={"channel_id": cid, "url": expected})
    body = r.json()
    assert body["ok"] is False
    assert "WABA" in (body["error"] or "")
    assert calls == [], "must not call Graph when waba_id is missing"


# ── regressão P1: status() não ganhou chamada Graph de webhook ───────────────

def test_status_endpoint_has_no_webhook_graph_call(build_app, monkeypatch):
    """``GET /api/channels/{id}/status`` deve continuar fazendo SÓ o ping ao
    phone-number node (``GET /{phone_number_id}``), sem ler webhook_configuration
    nem subscribed_apps — o caminho de status é chamado em massa (P1)."""
    cid = "p26_statusguard"
    _make_cloud_channel(cid, _full_creds())

    built = build_app(["whatsapp_cloud"])
    calls = _patch_httpx(monkeypatch, lambda *a: _Resp(200, {"verified_name": "X"}))
    r = built.client.get(f"/api/channels/{cid}/status")
    assert r.status_code == 200, r.text
    urls = [u for (_m, u, _p, _j) in calls]
    assert not any("webhook_configuration" in u or "subscribed_apps" in u for u in urls), \
        f"status() must not perform a webhook Graph call; saw {urls}"
