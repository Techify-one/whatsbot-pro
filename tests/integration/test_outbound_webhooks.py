"""Webhooks de saída: cadastro, captura no barramento e entrega (fase 8).

Sem rede — o ``httpx.AsyncClient`` é substituído por um dublê que registra o que
teria sido POSTado. O que se verifica é a costura: quem assina o quê, o que vai
no corpo, como a assinatura é montada e o que acontece quando o destino recusa.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid

import pytest

from db.repositories import session_repo, user_repo, webhook_repo
from server import webhook_dispatcher as wd
from server.auth import generate_session_token


@pytest.fixture
def admin_client(client):
    user = user_repo.create(
        email=f"wh-{uuid.uuid4().hex}@test.local", name="WH",
        password_hash="test-only", role_keys=["admin"])
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
    client.headers.pop("Authorization", None)
    session_repo.delete(token)
    user_repo.delete(user["id"])


@pytest.fixture
def endpoint():
    wd.invalidate_cache()
    row = webhook_repo.create_endpoint(
        url="https://exemplo.invalido/hook", secret=wd.generate_secret(),
        events=["message.sent", "protocolos.*"], description="suite")
    wd.invalidate_cache()
    yield row
    webhook_repo.delete_endpoint(row["id"])
    wd.invalidate_cache()


class _Ctx:
    def __init__(self, name):
        self.event_name = name


# ── cadastro ────────────────────────────────────────────────────────────────

def test_create_returns_the_secret_once(admin_client):
    r = admin_client.post("/api/webhooks", json={
        "url": "https://exemplo.invalido/x", "events": ["*"]})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["secret"].startswith("whsec_")
    try:
        listed = admin_client.get("/api/webhooks").json()["data"]["items"]
        mine = [w for w in listed if w["id"] == data["id"]][0]
        assert "secret" not in mine
        assert data["secret"] not in repr(listed)
    finally:
        webhook_repo.delete_endpoint(data["id"])


def test_create_rejects_a_non_url(admin_client):
    r = admin_client.post("/api/webhooks", json={"url": "nao-e-url", "events": ["*"]})
    assert r.status_code == 400


def test_create_requires_at_least_one_event(admin_client):
    r = admin_client.post("/api/webhooks",
                          json={"url": "https://exemplo.invalido/x", "events": []})
    assert r.status_code == 400


def test_list_exposes_the_curated_catalog(admin_client):
    """A tela monta o seletor a partir daqui — não de uma lista escrita à mão."""
    data = admin_client.get("/api/webhooks").json()["data"]
    assert "message.sent" in data["exportable_events"]
    assert "llm.after" not in data["exportable_events"]


# ── captura no barramento ───────────────────────────────────────────────────

def test_subscribed_event_is_enqueued(endpoint):
    wd.webhook_event_handler(_Ctx("message.sent"),
                             {"phone": "5511999999999", "text": "oi"})
    rows = webhook_repo.list_deliveries(endpoint["id"])
    assert len(rows) == 1
    assert rows[0]["event"] == "message.sent"
    assert rows[0]["payload"]["phone"] == "5511999999999"
    assert rows[0]["status"] == "pending"


def test_unsubscribed_event_is_ignored(endpoint):
    wd.webhook_event_handler(_Ctx("contact.updated"), {"phone": "x"})
    assert webhook_repo.list_deliveries(endpoint["id"]) == []


def test_plugin_event_travels_by_wildcard(endpoint):
    """Evento de PLUGIN não precisa de transporte próprio — o barramento é o mesmo."""
    wd.webhook_event_handler(_Ctx("protocolos.ciclo.aberto"), {"id": 9})
    rows = webhook_repo.list_deliveries(endpoint["id"])
    assert [r["event"] for r in rows] == ["protocolos.ciclo.aberto"]


def test_secrets_and_bulk_never_reach_the_queue(endpoint):
    wd.webhook_event_handler(_Ctx("message.sent"), {
        "phone": "551199", "access_token": "EAAB-segredo",
        "raw": {"audio": "AAAA" * 1000}})
    payload = webhook_repo.list_deliveries(endpoint["id"])[0]["payload"]
    assert payload == {"phone": "551199"}


def test_disabled_endpoint_receives_nothing(endpoint):
    webhook_repo.update_endpoint(endpoint["id"], enabled=0)
    wd.invalidate_cache()
    wd.webhook_event_handler(_Ctx("message.sent"), {"phone": "x"})
    assert webhook_repo.list_deliveries(endpoint["id"]) == []


# ── entrega ─────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeClient:
    """Dublê de ``httpx.AsyncClient`` que grava o que teria sido enviado."""

    def __init__(self, status=200, boom=None):
        self.status, self.boom, self.calls = status, boom, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, *, content, headers, timeout):
        self.calls.append({"url": url, "content": content, "headers": headers})
        if self.boom:
            raise self.boom
        return _FakeResponse(self.status)


def _patch_client(monkeypatch, fake):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)


def test_delivery_signs_the_exact_bytes(endpoint, monkeypatch):
    wd.webhook_event_handler(_Ctx("message.sent"), {"phone": "5511900000000"})
    fake = _FakeClient(200)
    _patch_client(monkeypatch, fake)
    assert asyncio.run(wd.deliver_due()) == 1

    call = fake.calls[0]
    body = call["content"]
    expected = hmac.new(endpoint["secret"].encode(), body, hashlib.sha256).hexdigest()
    assert call["headers"][wd.SIGNATURE_HEADER] == f"sha256={expected}"
    assert call["headers"][wd.EVENT_HEADER] == "message.sent"

    envelope = json.loads(body)
    assert envelope["event"] == "message.sent"
    assert envelope["data"] == {"phone": "5511900000000"}

    row = webhook_repo.list_deliveries(endpoint["id"])[0]
    assert row["status"] == "delivered"
    assert row["response_status"] == 200


def test_failure_is_rescheduled_not_lost(endpoint, monkeypatch):
    wd.webhook_event_handler(_Ctx("message.sent"), {"phone": "x"})
    _patch_client(monkeypatch, _FakeClient(500))
    asyncio.run(wd.deliver_due())
    row = webhook_repo.list_deliveries(endpoint["id"])[0]
    assert row["status"] == "failed"          # vai tentar de novo
    assert row["attempts"] == 1
    assert row["next_attempt_at"] > 0
    assert "500" in (row["last_error"] or "")


def test_network_error_is_recorded_not_raised(endpoint, monkeypatch):
    wd.webhook_event_handler(_Ctx("message.sent"), {"phone": "x"})
    _patch_client(monkeypatch, _FakeClient(boom=OSError("sem rota para o host")))
    asyncio.run(wd.deliver_due())      # não levanta
    row = webhook_repo.list_deliveries(endpoint["id"])[0]
    assert row["status"] == "failed"
    assert "sem rota" in row["last_error"]


def test_exhausted_retries_become_dead_letter(endpoint, monkeypatch):
    """A entrega que desistiu FICA na tabela — é o registro de que algo não chegou."""
    delivery_id = webhook_repo.enqueue(endpoint["id"], "message.sent", {"x": 1})
    status = webhook_repo.mark_failed(
        delivery_id, webhook_repo.MAX_ATTEMPTS, error="destino fora do ar")
    assert status == "dead"
    row = [d for d in webhook_repo.list_deliveries(endpoint["id"])
           if d["id"] == delivery_id][0]
    assert row["status"] == "dead"
    assert row["last_error"] == "destino fora do ar"


def test_purge_keeps_dead_letters(endpoint):
    import time
    delivered = webhook_repo.enqueue(endpoint["id"], "message.sent", {"a": 1})
    dead = webhook_repo.enqueue(endpoint["id"], "message.sent", {"b": 2})
    webhook_repo.mark_delivered(delivered, 200)
    webhook_repo.mark_failed(dead, webhook_repo.MAX_ATTEMPTS, error="x")
    webhook_repo.purge_delivered(time.time() + 60)
    ids = {d["id"] for d in webhook_repo.list_deliveries(endpoint["id"])}
    assert delivered not in ids
    assert dead in ids
