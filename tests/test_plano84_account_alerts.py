"""Plano 84 — avisos da CONTA Meta viram alerta num grupo do Telegram.

O buraco que o plano fecha: o WhatsBot escutava só o campo ``messages`` do
webhook da Meta. Um ``change`` com ``field: "message_template_status_update"``
(template pausado por baixa qualidade) produzia ZERO eventos e sumia sem log —
o operador só descobria quando os envios começavam a falhar.

Esta suíte trava as quatro camadas do conserto:

* **captura segura** — o observador em ``filters.py`` extrai o aviso do payload
  cru apenas depois que o seam genérico do core resolveu provider/canal e marcou
  a assinatura como autenticada; WABA exato, sem fallback;
* **e2e pelo webhook REAL** — POST assinado no endpoint do canal: o alerta chega
  ao Telegram e **nada** é materializado; provider/canal/assinatura/WABA errados
  não produzem efeito externo;
* **puros** — classificação do aviso, formatação PT-BR e a agregação/cooldown
  que impede 15 falhas do mesmo código virarem 15 mensagens no grupo;
* **rotas** — o token do bot nunca sai no GET e um PUT sem token não apaga o
  token salvo.

    venv/bin/python -m pytest tests/test_plano84_account_alerts.py -q
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import time
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from db.repositories import (channel_credential_repo, channel_repo,
                             contact_inbox_repo, contact_repo, config_repo,
                             conversation_repo, inbox_repo, message_repo)
from tests.characterization.golden import EventRecorder

_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLES = _ROOT / "assets" / "plugin_examples"

PHONE_NUMBER_ID = "PNID_P84"
WABA_ID = "WABA_P84"
APP_SECRET = "APP_SECRET_P84"


# ── Carga dos módulos do plugin (mesmo truque do loader real) ───────────────

def _load_plugin_module(plugin_id: str, modname: str, alias: str):
    plugin_dir = _EXAMPLES / plugin_id
    pkg_name = f"whatsbot_test_pkg_{alias}"
    if pkg_name not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            pkg_name, plugin_dir / "__init__.py",
            submodule_search_locations=[str(plugin_dir)])
        assert pkg_spec is not None and pkg_spec.loader is not None
        pkg = importlib.util.module_from_spec(pkg_spec)
        sys.modules[pkg_name] = pkg
        pkg_spec.loader.exec_module(pkg)
    full = f"{pkg_name}.{modname}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, plugin_dir / f"{modname}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_cloud = _load_plugin_module("whatsapp_cloud", "channels", "wa_cloud")
alerts = _load_plugin_module("whatsapp_cloud", "alerts", "wa_cloud")
filters = _load_plugin_module("whatsapp_cloud", "filters", "wa_cloud")


def _channel(channel_id: str = "p84"):
    return _cloud.WhatsAppCloudChannel(
        channel_id=channel_id, registry=None,
        credentials={"access_token": "T", "phone_number_id": PHONE_NUMBER_ID,
                     "verify_token": "V", "waba_id": WABA_ID,
                     "app_secret": APP_SECRET})


def _signed_body(payload: dict, secret: str = APP_SECRET) -> tuple[bytes, dict]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"Content-Type": "application/json",
                  "X-Hub-Signature-256": f"sha256={digest}"}


# ── Envelopes ───────────────────────────────────────────────────────────────

def _account_envelope(field: str, value: dict) -> dict:
    return {"object": "whatsapp_business_account",
            "entry": [{"id": WABA_ID, "time": 1750269342,
                       "changes": [{"field": field, "value": value}]}]}


def _template_paused(name: str = "promo_julho") -> dict:
    return _account_envelope("message_template_status_update", {
        "event": "PAUSED", "message_template_id": 1234567,
        "message_template_name": name, "message_template_language": "pt_BR",
        "reason": "INCORRECT_CATEGORY"})


def _text_envelope(phone: str) -> dict:
    return {"object": "whatsapp_business_account",
            "entry": [{"id": WABA_ID, "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111",
                             "phone_number_id": PHONE_NUMBER_ID},
                "contacts": [{"wa_id": phone, "profile": {"name": "Fulano"}}],
                "messages": [{"from": phone, "id": f"wamid.{uuid.uuid4().hex[:12]}",
                              "timestamp": "1750269342", "type": "text",
                              "text": {"body": "oi"}}]}}]}]}


# ── F2 · captura no webhook cru autenticado ─────────────────────────────────

def test_account_field_is_extracted_from_the_raw_payload():
    hits = filters.account_changes(_template_paused())
    assert len(hits) == 1, hits
    assert hits[0]["field"] == "message_template_status_update"
    assert hits[0]["value"]["event"] == "PAUSED"
    assert hits[0]["waba_id"] == WABA_ID


def test_unknown_field_is_not_swallowed():
    """Campo que a Meta invente amanhã tem de chegar, não sumir (R4)."""
    hits = filters.account_changes(
        _account_envelope("campo_que_a_meta_inventou", {"algo": 1}))
    assert len(hits) == 1
    assert hits[0]["field"] == "campo_que_a_meta_inventou"


def test_hot_path_payloads_extract_nothing():
    """O guard precisa sair barato para o inbound normal e para outros providers."""
    assert filters.account_changes(_text_envelope("5511999990000")) == []
    assert filters.account_changes({"event": "message", "payload": {}}) == []  # GOWA
    assert filters.account_changes(None) == []
    assert filters.account_changes({"object": "whatsapp_business_account"}) == []


def test_observer_always_returns_the_payload_untouched():
    """Devolver None neste filtro DESCARTA a mensagem — nunca pode acontecer."""
    envelope = _template_paused()
    out = filters.observe(SimpleNamespace(extras={}), envelope)
    assert out is envelope
    # Payload quebrado também passa intacto (observador nunca trava o webhook).
    lixo = {"object": "whatsapp_business_account", "entry": "nao-e-lista"}
    assert filters.observe(SimpleNamespace(extras={}), lixo) is lixo


def test_messages_field_is_untouched_by_the_provider():
    """Regressão zero: um payload de mensagem continua idêntico no parse."""
    phone = "5511999990000"
    events = _channel().parse_inbound(_text_envelope(phone))
    assert len(events) == 1
    assert events[0].kind == "message"
    assert events[0].chat_id == phone
    assert events[0].text == "oi"


def test_account_change_produces_no_inbound_event():
    """O aviso da conta NÃO é mensagem: o provider não emite evento nenhum."""
    assert _channel().parse_inbound(_template_paused()) == []


def test_cloud_signature_is_strict_when_configured_and_warns_once_when_missing(caplog):
    payload = _template_paused()
    body, headers = _signed_body(payload)
    channel = _channel()
    assert channel.verify_inbound_signature_result(body, headers) == (True, True)
    assert channel.verify_inbound_signature_result(body, {}) == (False, False)
    assert channel.verify_inbound_signature(body, headers) is True
    assert channel.verify_inbound_signature(body, {}) is False

    legacy = _cloud.WhatsAppCloudChannel(
        channel_id="p84_sem_secret", registry=None,
        credentials={"access_token": "T", "phone_number_id": PHONE_NUMBER_ID,
                     "verify_token": "V", "waba_id": WABA_ID})
    with caplog.at_level("WARNING"):
        assert legacy.verify_inbound_signature(body, {}) is True
        assert legacy.verify_inbound_signature(body, {}) is True
    matching = [r for r in caplog.records if "sem app_secret" in r.message]
    assert len(matching) == 1
    assert legacy.verify_inbound_signature_result(body, {}) == (True, False)


def test_signature_verdict_uses_one_secret_snapshot():
    """Segredo surgindo após um fail-open não pode autenticar aquele payload."""
    class RotatingRegistry:
        def __init__(self):
            self.secret_reads = 0

        def get_credential(self, channel_id, key):
            if key != "app_secret":
                return ""
            self.secret_reads += 1
            return "" if self.secret_reads == 1 else APP_SECRET

    registry = RotatingRegistry()
    channel = _cloud.WhatsAppCloudChannel(
        channel_id="p84_secret_rotating", registry=registry,
        credentials={"access_token": "T", "phone_number_id": PHONE_NUMBER_ID,
                     "verify_token": "V", "waba_id": WABA_ID})
    body, _headers = _signed_body(_template_paused())
    # O corpo sem header foi aceito pela compatibilidade do snapshot vazio, mas
    # jamais marcado como autenticado; não há segunda leitura após o veredito.
    assert channel.verify_inbound_signature_result(body, {}) == (True, False)
    assert registry.secret_reads == 1


def test_signature_credential_read_error_is_not_treated_as_legacy_missing():
    class BrokenRegistry:
        def get_credential(self, channel_id, key):
            raise RuntimeError("credential store unavailable")

    channel = _cloud.WhatsAppCloudChannel(
        channel_id="p84_secret_error", registry=BrokenRegistry())
    with pytest.raises(RuntimeError, match="credential store unavailable"):
        channel.verify_inbound_signature_result(b"{}", {})


def test_app_secret_is_create_required_but_not_legacy_health_required():
    """Legado fail-open continua operacional; canal NOVO continua bloqueado."""
    channel = _channel()
    assert "app_secret" not in channel.capabilities.required_credentials
    assert set(channel.capabilities.required_credentials) == {
        "access_token", "phone_number_id", "verify_token"}
    fields = {
        field["key"]: field
        for field in _cloud.WhatsAppCloudChannel.provider_descriptor()[
            "credential_fields"]
    }
    assert fields["app_secret"]["required"] is True


# ── F3 · e2e pelo webhook REAL + procedência do core ────────────────────────

def _make_cloud_channel(waba_id: str = WABA_ID) -> str:
    channel_id = f"p84_{uuid.uuid4().hex[:8]}"
    channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                        display_name="Cloud P84", enabled=1)
    for key, value in {"access_token": "TOKEN", "phone_number_id": PHONE_NUMBER_ID,
                       "waba_id": waba_id, "verify_token": "VERIFY",
                       "app_secret": APP_SECRET}.items():
        channel_credential_repo.set(channel_id, key, value)
    return channel_id


@pytest.mark.parametrize("url_state", ["missing", "inactive"])
def test_gowa_device_reroute_precedes_url_channel_validation(
        build_app, url_state):
    """O callback compartilhado pode sumir; o device vivo ainda precisa receber."""
    target_id = f"p84_gowa_{uuid.uuid4().hex[:8]}"
    session_id = f"device_{uuid.uuid4().hex[:8]}"
    channel_repo.create(id=target_id, provider="gowa", display_name="GOWA P84",
                        enabled=1, gowa_device_id=session_id)
    url_id = f"p84_default_{uuid.uuid4().hex[:8]}"
    if url_state == "inactive":
        channel_repo.create(id=url_id, provider="gowa",
                            display_name="Callback antigo", enabled=0)

    built = build_app(["gowa"])
    assert built.app.state.deps.channel_registry.get(target_id) is not None
    r = built.client.post(f"/api/webhook/gowa/{url_id}", json={
        "event": "unknown", "session_id": session_id, "payload": {}})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "received", data


def test_webhook_rejects_when_app_secret_store_is_unavailable(
        build_app, monkeypatch):
    channel_id = _make_cloud_channel()
    built = build_app(["whatsapp_cloud"])
    registry = built.app.state.deps.channel_registry
    real_get = registry.get_credential

    def _broken_get(cid, key):
        if cid == channel_id and key == "app_secret":
            raise RuntimeError("credential store unavailable")
        return real_get(cid, key)

    monkeypatch.setattr(registry, "get_credential", _broken_get)
    body, headers = _signed_body(_template_paused())
    r = built.client.post(f"/api/webhook/whatsapp_cloud/{channel_id}",
                          content=body, headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "bad_signature"


# ── F4/F5 · funções puras ───────────────────────────────────────────────────

def test_describe_template_paused_and_approved():
    down = alerts.describe_account_event("message_template_status_update", {
        "event": "PAUSED", "message_template_name": "promo",
        "message_template_language": "pt_BR"})
    assert down["group"] == "template_down"
    assert down["signature"] == "PAUSED"
    assert "promo (pt_BR)" in "\n".join(down["lines"])

    up = alerts.describe_account_event("message_template_status_update", {
        "event": "APPROVED", "message_template_name": "promo",
        "message_template_language": "pt_BR"})
    assert up["group"] == "template_up"
    # Mesma identidade do alerta de queda → o "voltou" agrega no mesmo balde.
    assert up["key"] == down["key"]

    # Evento neutro não vira alerta.
    assert alerts.describe_account_event("message_template_status_update",
                                         {"event": "PENDING"}) is None


def test_describe_quality_and_limit():
    q = alerts.describe_account_event("phone_number_quality_update", {
        "display_phone_number": "15550001111", "event": "FLAGGED",
        "current_limit": "TIER_1K"})
    assert q["group"] == "quality" and q["emoji"] == "🔴"

    lim = alerts.describe_account_event("business_capability_update", {
        "max_daily_conversation_per_phone": 1000})
    assert lim["group"] == "limit"

    restricted = alerts.describe_account_event("account_update", {
        "event": "ACCOUNT_RESTRICTION", "restriction_info": [{"restriction_type": "X"}]})
    assert restricted["group"] == "account" and restricted["emoji"] == "🔴"


def test_describe_failure_filters_by_code():
    relevant = alerts.describe_failure_event({"error_code": 131049, "is_new": True})
    assert relevant["group"] == "send_failure"
    assert relevant["key"] == "failure:131049"

    # 131047 tem grupo próprio (default OFF): é erro de operação, não de conta.
    janela = alerts.describe_failure_event({"error_code": 131047})
    assert janela["group"] == "send_failure_24h"

    # Código irrelevante não vira alerta.
    assert alerts.describe_failure_event({"error_code": 132000}) is None
    assert alerts.describe_failure_event({}) is None


def test_quality_change_only_on_variation():
    assert alerts.describe_quality_change("", "GREEN") is None   # 1ª leitura
    assert alerts.describe_quality_change("GREEN", "GREEN") is None
    desc = alerts.describe_quality_change("GREEN", "RED", number="15550001111")
    assert desc["group"] == "quality" and desc["signature"] == "GREEN->RED"


def test_should_alert_aggregates_within_window():
    cooldown = 900.0
    action, count = alerts.should_alert(None, "131049", 1000.0, cooldown)
    assert (action, count) == ("send", 1)

    state = {"last_value": "131049", "last_alert_ts": 1000.0, "occurrences": 1,
             "telegram_message_id": 42}
    # Repetição dentro da janela: edita a mesma mensagem, contador sobe.
    assert alerts.should_alert(state, "131049", 1010.0, cooldown) == ("edit", 2)
    # Janela vencida: manda uma nova (a antiga já ficou soterrada no grupo).
    assert alerts.should_alert(state, "131049", 3000.0, cooldown) == ("send", 2)
    # Valor diferente: alerta novo, contagem reiniciada.
    assert alerts.should_alert(state, "131026", 1010.0, cooldown) == ("send", 1)
    # Sem mensagem confirmada para editar: tenta enviar de novo; falha de
    # transporte nunca pode consumir o cooldown.
    assert alerts.should_alert({**state, "telegram_message_id": None},
                               "131049", 1010.0, cooldown) == ("send", 2)


def test_format_alert_carries_channel_and_count():
    desc = alerts.describe_failure_event({"error_code": 131026})
    body = alerts.format_alert(desc, channel_label="Cloud (5511999)",
                               when="10:00 de 27/07/2026", count=7)
    assert "Cloud (5511999)" in body
    assert "Ocorrências: 7" in body
    assert "131026" in body


def test_telegram_html_escapes_channel_and_dynamic_reason(monkeypatch):
    import server.message_errors as message_errors

    monkeypatch.setattr(
        message_errors, "describe_failure",
        lambda *_a, **_k: 'Razão <script> & "aspas"')
    desc = alerts.describe_failure_event({"error_code": 131026})
    body = alerts.format_alert(
        desc, channel_label='Canal <VIP> & "Norte"',
        when='agora < 10 & "depois"')

    assert 'Canal: <b>Canal &lt;VIP&gt; &amp; &quot;Norte&quot;</b>' in body
    assert 'Razão &lt;script&gt; &amp; &quot;aspas&quot;' in body
    assert 'agora &lt; 10 &amp; &quot;depois&quot;' in body
    assert "<script>" not in body


def test_group_enabled_defaults():
    assert alerts.group_enabled({}, "template_down") is True
    assert alerts.group_enabled({}, "send_failure_24h") is False  # ruído medido
    assert alerts.group_enabled({"send_failure_24h": True}, "send_failure_24h") is True
    # Grupo desconhecido (config antiga × plugin novo) nunca fica invisível.
    assert alerts.group_enabled({}, "grupo_que_nao_existe") is True


# ── F5 · entrega agregada (sem rede) ────────────────────────────────────────

@pytest.fixture
def alert_ready(build_app, monkeypatch):
    """App com o plugin carregado (migration aplicada) + Telegram mockado."""
    build_app(["whatsapp_cloud"])
    config_repo.set_many({
        "plugin.whatsapp_cloud.alert_enabled": True,
        "plugin.whatsapp_cloud.alert_bot_token": "123:ABC",
        "plugin.whatsapp_cloud.alert_chat_id": "-100999",
        "plugin.whatsapp_cloud.alert_interval_min": 15,
        "plugin.whatsapp_cloud.alert_groups": {},
    })
    sent: list[str] = []
    edited: list[str] = []

    async def _fake_send(token, chat_id, body):
        sent.append(body)
        return 4242

    async def _fake_edit(token, chat_id, message_id, body):
        edited.append(body)
        return True

    monkeypatch.setattr(alerts, "_tg_send", _fake_send)
    monkeypatch.setattr(alerts, "_tg_edit", _fake_edit)
    yield sent, edited
    config_repo.set("plugin.whatsapp_cloud.alert_enabled", False)


def test_burst_of_same_failure_becomes_one_message(alert_ready):
    sent, edited = alert_ready
    channel_id = _make_cloud_channel()
    payload = {"error_code": 131049, "is_new": True, "channel_id": channel_id,
               "phone": "5511999990000"}

    async def _run():
        for _ in range(10):
            await alerts.on_message_failed(None, dict(payload))
    asyncio.run(_run())

    assert len(sent) == 1, "10 falhas iguais não podem virar 10 mensagens"
    assert len(edited) == 9, "as repetições editam a mensagem existente"
    assert "Ocorrências: 10" in edited[-1]


def test_failed_telegram_send_does_not_consume_cooldown(alert_ready, monkeypatch):
    """Sem message_id confirmado não há estado de sucesso; a próxima tenta de novo."""
    channel_id = _make_cloud_channel()
    desc = alerts.describe_account_event("message_template_status_update", {
        "event": "PAUSED",
        "message_template_name": f"falha_{uuid.uuid4().hex[:8]}",
        "message_template_language": "pt_BR",
    })
    alert_key = f"{channel_id}|{desc['key']}"
    attempts: list[int] = []

    async def _flaky_send(token, chat_id, body):
        attempts.append(1)
        return None if len(attempts) == 1 else 777

    monkeypatch.setattr(alerts, "_tg_send", _flaky_send)

    async def _run():
        first = await alerts.deliver(desc, channel_id)
        state_after_failure = await asyncio.to_thread(alerts.load_state, alert_key)
        second = await alerts.deliver(desc, channel_id)
        return first, state_after_failure, second

    first, failed_state, second = asyncio.run(_run())
    assert first == "failed"
    assert failed_state == {}, "falha não pode gravar last_alert_ts/cooldown"
    assert second == "send"
    assert len(attempts) == 2
    assert alerts.load_state(alert_key)["telegram_message_id"] == 777


def test_group_migration_persists_the_effective_chat_for_next_edit(
        build_app, monkeypatch):
    """Após migrate_to_chat_id a agregação edita no supergrupo, não no id velho."""
    build_app(["whatsapp_cloud"])
    channel_id = _make_cloud_channel()
    old_chat, new_chat = "-100111", "-100222"
    config_repo.set_many({
        "plugin.whatsapp_cloud.alert_enabled": True,
        "plugin.whatsapp_cloud.alert_bot_token": "123:ABC",
        "plugin.whatsapp_cloud.alert_chat_id": old_chat,
        "plugin.whatsapp_cloud.alert_interval_min": 15,
        "plugin.whatsapp_cloud.alert_groups": {},
    })
    replies = iter([
        {"ok": False, "parameters": {"migrate_to_chat_id": int(new_chat)}},
        {"ok": True, "result": {"message_id": 9090}},
        {"ok": True, "result": {}},
    ])
    requests: list[tuple[str, dict]] = []

    class _Response:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            requests.append((url, dict(json)))
            return _Response(next(replies))

    monkeypatch.setattr(alerts.httpx, "AsyncClient",
                        lambda **kwargs: _Client())
    desc = alerts.describe_account_event("message_template_status_update", {
        "event": "PAUSED",
        "message_template_name": f"migrado_{uuid.uuid4().hex[:8]}",
        "message_template_language": "pt_BR",
    })
    alert_key = f"{channel_id}|{desc['key']}"

    async def _run():
        return (await alerts.deliver(desc, channel_id),
                await alerts.deliver(desc, channel_id))

    try:
        assert asyncio.run(_run()) == ("send", "edit")
        state = alerts.load_state(alert_key)
        assert state["telegram_chat_id"] == new_chat
        assert config_repo.get(
            "plugin.whatsapp_cloud.alert_chat_id") == new_chat
        assert [body["chat_id"] for _url, body in requests] == [
            old_chat, new_chat, new_chat]
        assert requests[-1][0].endswith("/editMessageText")
    finally:
        config_repo.set("plugin.whatsapp_cloud.alert_enabled", False)


def test_existing_alert_edit_migration_updates_the_state_chat(
        build_app, monkeypatch):
    """Grupo promovido depois do 1º alerta não deixa o estado preso no id velho."""
    build_app(["whatsapp_cloud"])
    channel_id = _make_cloud_channel()
    old_chat, new_chat = "-100333", "-100444"
    config_repo.set_many({
        "plugin.whatsapp_cloud.alert_enabled": True,
        "plugin.whatsapp_cloud.alert_bot_token": "123:ABC",
        "plugin.whatsapp_cloud.alert_chat_id": old_chat,
        "plugin.whatsapp_cloud.alert_interval_min": 15,
        "plugin.whatsapp_cloud.alert_groups": {},
    })
    desc = alerts.describe_account_event("message_template_status_update", {
        "event": "PAUSED",
        "message_template_name": f"edit_migrado_{uuid.uuid4().hex[:8]}",
        "message_template_language": "pt_BR",
    })
    alert_key = f"{channel_id}|{desc['key']}"
    alerts.save_state(
        alert_key, last_value=desc["signature"], last_alert_ts=time.time(),
        occurrences=1, telegram_chat_id=old_chat, telegram_message_id=8080)
    replies = iter([
        {"ok": False, "parameters": {"migrate_to_chat_id": int(new_chat)}},
        {"ok": True, "result": {}},
    ])
    requests: list[dict] = []

    class _Response:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            requests.append(dict(json))
            return _Response(next(replies))

    monkeypatch.setattr(alerts.httpx, "AsyncClient",
                        lambda **kwargs: _Client())
    try:
        assert asyncio.run(alerts.deliver(desc, channel_id)) == "edit"
        assert alerts.load_state(alert_key)["telegram_chat_id"] == new_chat
        assert [body["chat_id"] for body in requests] == [old_chat, new_chat]
    finally:
        config_repo.set("plugin.whatsapp_cloud.alert_enabled", False)


def test_redelivered_failure_is_ignored(alert_ready):
    sent, edited = alert_ready
    channel_id = _make_cloud_channel()
    asyncio.run(alerts.on_message_failed(None, {
        "error_code": 131026, "is_new": False, "is_redelivery": True,
        "channel_id": channel_id,
        "msg_id": "wamid.ja_persistida"}))
    assert sent == [] and edited == []


def test_unmatched_is_new_false_failure_is_not_lost(alert_ready):
    """Mesmo se a row surgir após o emit, o snapshot preserva a primeira falha."""
    sent, edited = alert_ready
    channel_id = _make_cloud_channel()
    msg_id = f"wamid.writer_race.{uuid.uuid4().hex[:12]}"
    snapshot = {
        "error_code": 131026, "is_new": False, "is_redelivery": False,
        "channel_id": channel_id, "msg_id": msg_id}

    # Simula o writer vencendo DEPOIS que o core já emitiu o payload. Consultar o
    # banco agora diria "existe" e perderia o alerta; o snapshot não muda.
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    contact = contact_repo.get_or_create(phone)
    inbox_row = inbox_repo.get_or_create_for_channel(channel_id)
    link = contact_inbox_repo.get_or_create(
        inbox_id=inbox_row["id"], contact_id=contact["id"],
        source_id=phone, source_jid=phone)
    conv = conversation_repo.create(
        inbox_id=inbox_row["id"], contact_id=contact["id"],
        contact_inbox_id=link["id"])
    message_repo.add(contact["id"], "assistant", "writer terminou",
                     status="operator", msg_id=msg_id,
                     conversation_id=conv["id"])
    assert message_repo.exists_by_msg_id(msg_id) is True

    asyncio.run(alerts.on_message_failed(None, {
        **snapshot}))
    assert len(sent) == 1 and edited == []


def test_failure_alert_requires_an_exact_cloud_channel(alert_ready):
    sent, edited = alert_ready
    other_channel = f"p84_gowa_fail_{uuid.uuid4().hex[:8]}"
    channel_repo.create(id=other_channel, provider="gowa",
                        display_name="Outro provider", enabled=1)
    for channel_id in ("", f"missing_{uuid.uuid4().hex[:8]}", other_channel):
        asyncio.run(alerts.on_message_failed(None, {
            "error_code": 131026, "is_new": True,
            "is_redelivery": False, "channel_id": channel_id}))
    assert sent == [] and edited == []


def test_quality_cursor_only_advances_after_confirmed_delivery(
        alert_ready, monkeypatch):
    """Falha do Telegram mantém o valor anterior para o próximo tick tentar de novo."""
    channel_id = _make_cloud_channel()
    cursor_key = f"{channel_id}|quality_seen"
    alerts.save_state(cursor_key, last_value="GREEN", last_alert_ts=1.0)
    monkeypatch.setattr(alerts, "_cloud_channels",
                        lambda: [{"id": channel_id, "own_phone": "5511999"}])
    monkeypatch.setattr(alerts, "_live_quality",
                        lambda _deps, _cid: ("RED", "5511999"))
    outcomes = iter(("failed", "send"))

    async def _deliver(_desc, _channel_id):
        return next(outcomes)

    monkeypatch.setattr(alerts, "deliver", _deliver)

    async def _run():
        await alerts._quality_tick(SimpleNamespace(channel_registry=None))
        after_failure = alerts.load_state(cursor_key).get("last_value")
        await alerts._quality_tick(SimpleNamespace(channel_registry=None))
        after_success = alerts.load_state(cursor_key).get("last_value")
        return after_failure, after_success

    assert asyncio.run(_run()) == ("GREEN", "RED")


def test_disabled_group_sends_nothing(alert_ready):
    sent, edited = alert_ready
    channel_id = _make_cloud_channel()
    # 131047 nasce DESLIGADO (é erro de operação e mede-se às dezenas por dia).
    asyncio.run(alerts.on_message_failed(None, {
        "error_code": 131047, "is_new": True, "channel_id": channel_id}))
    assert sent == [] and edited == []


def test_account_change_reaches_telegram(alert_ready):
    sent, _edited = alert_ready
    channel_id = _make_cloud_channel()
    asyncio.run(alerts.handle_account_change(
        "message_template_status_update",
        {"event": "PAUSED", "message_template_name": "promo",
         "message_template_language": "pt_BR"},
        WABA_ID, channel_id))
    assert len(sent) == 1
    assert "Template fora do ar" in sent[0]
    assert "Cloud P84" in sent[0]  # o canal sempre aparece no texto (P5)


def test_account_source_requires_exact_provider_channel_signature_and_waba(alert_ready):
    """Nenhum fallback: os quatro vínculos precisam casar para haver efeito."""
    waba = f"WABA_{uuid.uuid4().hex[:8]}"
    channel_id = _make_cloud_channel(waba)
    source = {"provider": "whatsapp_cloud", "channel_id": channel_id,
              "signature_authenticated": True}
    assert filters._authenticated_channel(source, waba) == channel_id
    assert filters._authenticated_channel(source, "WABA_ERRADA") == ""
    assert filters._authenticated_channel(
        {**source, "signature_authenticated": False}, waba) == ""
    assert filters._authenticated_channel(
        {**source, "provider": "gowa"}, waba) == ""
    assert filters._authenticated_channel(
        {**source, "channel_id": "canal_inexistente"}, waba) == ""


def test_account_dispatch_retries_transient_telegram_failure(
        alert_ready, monkeypatch):
    waba = f"WABA_{uuid.uuid4().hex[:8]}"
    channel_id = _make_cloud_channel(waba)
    source = {"provider": "whatsapp_cloud", "channel_id": channel_id,
              "signature_authenticated": True}
    hit = {"field": "message_template_status_update", "waba_id": waba,
           "value": {"event": "PAUSED"}}
    outcomes = iter(("failed", "failed", "send"))
    calls: list[int] = []

    async def _flaky_handler(*_args):
        calls.append(1)
        return next(outcomes)

    monkeypatch.setattr(alerts, "handle_account_change", _flaky_handler)
    monkeypatch.setattr(filters, "_ACCOUNT_RETRY_DELAYS", (0, 0))
    asyncio.run(filters._dispatch(hit, source))
    assert len(calls) == 3


def test_irrelevant_account_change_sends_nothing(alert_ready):
    sent, edited = alert_ready
    # Evento neutro de template (PENDING) não vira alerta.
    asyncio.run(alerts.handle_account_change(
        "message_template_status_update", {"event": "PENDING"}, WABA_ID))
    assert sent == [] and edited == []


# ── F6 · rotas de configuração ──────────────────────────────────────────────

@pytest.fixture
def admin_client(build_app):
    """``built`` com o client autenticado como admin, e o usuário REMOVIDO no fim.

    As rotas de alerta exigem ``channel.manage``, e o plano 48 fecha a API assim
    que existe ≥1 usuário. Como o banco de teste é COMPARTILHADO pelo processo,
    autenticar torna o teste independente da ordem — e apagar o usuário no
    teardown evita fechar a API para os testes seguintes, que não autenticam.
    """
    from db.repositories import session_repo, user_repo
    from server.auth import generate_session_token

    built = build_app(["whatsapp_cloud"])
    email = "p84@teste.local"
    created = user_repo.get_by_email(email) is None
    op = (user_repo.get_by_email(email)
          or user_repo.create(email=email, name="P84 Operador",
                              password_hash="x", role_keys=["admin"]))
    tok = generate_session_token()
    session_repo.create(tok, op["id"], user_agent="test", ip="127.0.0.1")
    built.client.headers["Authorization"] = f"Bearer {tok}"
    yield built
    if created:
        try:
            user_repo.delete(op["id"])
        except Exception:  # noqa: BLE001
            pass


def test_new_cloud_channel_endpoint_requires_app_secret(admin_client):
    channel_id = f"p84_no_secret_{uuid.uuid4().hex[:8]}"
    r = admin_client.client.post("/api/channels", json={
        "id": channel_id,
        "provider": "whatsapp_cloud",
        "display_name": "Cloud sem assinatura",
        "credentials": {
            "access_token": "TOKEN",
            "phone_number_id": f"PN_{uuid.uuid4().hex[:8]}",
            "verify_token": "VERIFY",
        },
    })
    assert r.status_code == 400, r.text
    assert "app_secret" in r.text
    assert channel_repo.get(channel_id) is None


def test_legacy_cloud_without_secret_is_not_reported_as_zombie(admin_client):
    """O catálogo separa requisito de CREATE da saúde operacional do card."""
    r = admin_client.client.get("/api/channels/providers")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    cloud = next(d for d in data["providers"]
                 if d["provider"] == "whatsapp_cloud")
    fields = {f["key"]: f for f in cloud["credential_fields"]}
    assert fields["app_secret"]["required"] is True
    assert "app_secret" not in data["required_credentials"]["whatsapp_cloud"]


def test_alert_settings_never_leaks_the_token(admin_client):
    built = admin_client
    config_repo.set("plugin.whatsapp_cloud.alert_bot_token", "123456:SEGREDO")

    r = built.client.get("/api/plugins/whatsapp_cloud/alert-settings")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["bot_token_set"] is True
    assert "SEGREDO" not in r.text
    assert data["bot_token_hint"] == "REDO"  # só os últimos 4
    # O catálogo de grupos vem do motor — a tela não hardcoda nada.
    keys = {g["key"] for g in data["groups"]}
    assert "template_down" in keys and "send_failure_24h" in keys


def test_put_without_token_keeps_the_saved_one(admin_client):
    built = admin_client
    config_repo.set("plugin.whatsapp_cloud.alert_bot_token", "123456:SEGREDO")

    r = built.client.put("/api/plugins/whatsapp_cloud/alert-settings",
                         json={"chat_id": "-100777", "enabled": True,
                               "groups": {"send_failure_24h": True}})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert config_repo.get("plugin.whatsapp_cloud.alert_bot_token") == "123456:SEGREDO"
    assert config_repo.get("plugin.whatsapp_cloud.alert_chat_id") == "-100777"
    saved_groups = config_repo.get("plugin.whatsapp_cloud.alert_groups")
    assert saved_groups.get("send_failure_24h") is True
    config_repo.set("plugin.whatsapp_cloud.alert_enabled", False)


def test_alert_test_transport_error_never_echoes_bot_token(
        admin_client, monkeypatch):
    """Exceção httpx pode conter a URL /bot{token}; a API deve sanitizá-la."""
    secret = "123456:NAO_PODE_VAZAR"
    live_routes = sys.modules.get("whatsbot_plugins.whatsapp_cloud.routes")
    assert live_routes is not None

    class _ExplodingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            raise RuntimeError(f"request failed for {url}")

    monkeypatch.setattr(
        live_routes.httpx, "AsyncClient", lambda **kwargs: _ExplodingClient())
    r = admin_client.client.post(
        "/api/plugins/whatsapp_cloud/alert-test",
        json={"bot_token": secret, "chat_id": "-100777"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": False, "error": "Falha ao contatar o Telegram."}
    assert secret not in r.text


def test_alert_transport_exception_never_logs_or_returns_bot_token(
        monkeypatch, caplog):
    secret = "654321:TAMBEM_NAO_PODE_VAZAR"

    class _ExplodingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            raise RuntimeError(f"request failed for {url}")

    monkeypatch.setattr(alerts.httpx, "AsyncClient",
                        lambda **kwargs: _ExplodingClient())
    with caplog.at_level("WARNING"):
        result = asyncio.run(alerts._tg_call(
            secret, "sendMessage", {"chat_id": "-100777", "text": "teste"}))
    assert result == {"ok": False,
                      "description": "Falha de transporte no Telegram."}
    assert secret not in caplog.text


# ── E2E: webhook REAL, plugin carregado pelo loader REAL ─────────────────────

def test_end_to_end_through_the_real_webhook(build_app, monkeypatch):
    """A prova do plano: um aviso de template pausado entra pelo endpoint que a
    Meta chama, vira mensagem no Telegram e **não materializa nada**. O core não
    ganha ramo de dispatch; só fornece procedência/autenticação ao filtro.

    Diferente dos testes acima, aqui o plugin é carregado pelo LOADER de verdade,
    então o módulo que roda é ``whatsbot_plugins.whatsapp_cloud.alerts`` — é ele
    que precisa ser interceptado.
    """
    channel_id = _make_cloud_channel()
    built = build_app(["whatsapp_cloud"])

    live = sys.modules.get("whatsbot_plugins.whatsapp_cloud.alerts")
    assert live is not None, "o plugin não foi carregado pelo loader"

    sent: list[str] = []

    async def _fake_send(token, chat_id, body):
        sent.append(body)
        return 1

    monkeypatch.setattr(live, "_tg_send", _fake_send)
    monkeypatch.setattr(live, "_tg_edit",
                        lambda *a, **k: asyncio.sleep(0, result=True))
    config_repo.set_many({
        "plugin.whatsapp_cloud.alert_enabled": True,
        "plugin.whatsapp_cloud.alert_bot_token": "123:ABC",
        "plugin.whatsapp_cloud.alert_chat_id": "-100999",
        "plugin.whatsapp_cloud.alert_groups": {},
    })
    try:
        contacts_before = len(contact_repo.list_contacts())

        body, headers = _signed_body(_template_paused())
        r = built.client.post(f"/api/webhook/whatsapp_cloud/{channel_id}",
                              content=body, headers=headers)
        assert r.status_code == 200, r.text
        # O core NÃO conhece este payload: zero eventos parseados — e é justamente
        # por isso que a captura precisa acontecer no filtro.
        assert r.json()["data"]["events"] == 0

        # O alerta é fire-and-forget (sai do caminho do request); espera curta.
        deadline = time.time() + 5
        while not sent and time.time() < deadline:
            time.sleep(0.05)

        assert sent, "o aviso da conta não virou alerta no Telegram"
        assert "Template fora do ar" in sent[0]
        assert "promo_julho (pt_BR)" in sent[0]

        # E nada foi materializado no painel: sem contato novo não há conversa
        # nem mensagem (um aviso da conta não tem telefone para materializar).
        assert len(contact_repo.list_contacts()) == contacts_before
    finally:
        config_repo.set("plugin.whatsapp_cloud.alert_enabled", False)


def test_webhook_rejects_wrong_provider_unknown_channel_unsigned_and_wrong_waba(
        build_app, monkeypatch):
    """A rota pública não pode disparar alerta fora do contexto autenticado exato."""
    channel_id = _make_cloud_channel()
    built = build_app(["whatsapp_cloud"])
    live = sys.modules.get("whatsbot_plugins.whatsapp_cloud.alerts")
    assert live is not None
    sent: list[str] = []

    async def _fake_send(token, chat_id, body):
        sent.append(body)
        return 1

    monkeypatch.setattr(live, "_tg_send", _fake_send)
    config_repo.set_many({
        "plugin.whatsapp_cloud.alert_enabled": True,
        "plugin.whatsapp_cloud.alert_bot_token": "123:ABC",
        "plugin.whatsapp_cloud.alert_chat_id": "-100999",
        "plugin.whatsapp_cloud.alert_groups": {},
    })
    try:
        payload = _template_paused()
        body, headers = _signed_body(payload)

        wrong_provider = built.client.post(
            f"/api/webhook/gowa/{channel_id}", content=body, headers=headers)
        assert wrong_provider.status_code == 200
        assert wrong_provider.json()["data"]["reason"] == "provider_mismatch"

        unknown = built.client.post(
            "/api/webhook/whatsapp_cloud/canal_inexistente",
            content=body, headers=headers)
        assert unknown.status_code == 200
        assert unknown.json()["data"]["reason"] == "unknown_channel"

        unsigned = built.client.post(
            f"/api/webhook/whatsapp_cloud/{channel_id}", json=payload)
        assert unsigned.status_code == 200
        assert unsigned.json()["data"]["status"] == "bad_signature"

        wrong_waba_payload = _template_paused()
        wrong_waba_payload["entry"][0]["id"] = "WABA_DE_OUTRA_CONTA"
        wrong_body, wrong_headers = _signed_body(wrong_waba_payload)
        wrong_waba = built.client.post(
            f"/api/webhook/whatsapp_cloud/{channel_id}",
            content=wrong_body, headers=wrong_headers)
        assert wrong_waba.status_code == 200
        assert wrong_waba.json()["data"]["events"] == 0

        # O observador é fire-and-forget; dê uma chance real de uma regressão
        # insegura chegar ao transporte antes de afirmar que nada foi enviado.
        time.sleep(0.2)
        assert sent == []
    finally:
        config_repo.set("plugin.whatsapp_cloud.alert_enabled", False)
