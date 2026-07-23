"""Habilitadores do core da Wave 0 (plano 46 · sub-plano 01).

Cobre, sem rede e sem depender de plugin:

* **01-A** — hook ``Channel.verify_inbound_signature`` (default permissivo) + a
  costura no POST ``/api/webhook/{provider}/{channel_id}``: assinatura inválida
  ⇒ ``bad_signature`` e NADA é ingerido; canal sem ``app_secret`` (GOWA/telegram)
  segue byte-idêntico (caracterização).
* **01-B** — ``MetaGraphChannel.parse_inbound`` sobre ``entry[].messaging[]``
  (texto, anexo, echo, reaction, delivery/read, postback) e o helper de URL
  pública de mídia.
* **01-C** — capability ``token_refresh`` + hook ``refresh_token_if_needed``
  (no-op por padrão).

    venv/bin/python -m pytest tests/test_meta_graph_core.py -q
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from channels.base import Channel, ChannelCapabilities
from channels.media_urls import public_media_url
from channels.providers.meta_graph import MetaGraphChannel, attachment_type_for

APP_SECRET = "s3cr3t"


class _Meta(MetaGraphChannel):
    """Subclasse mínima: só o que o contrato exige para instanciar."""

    provider = "meta_test"

    def status(self) -> dict:  # pragma: no cover - trivial
        return {"connected": True, "logged_in": True, "needs_qr": False, "error": None}


def _channel(**creds) -> _Meta:
    return _Meta("ch1", ChannelCapabilities(inbound_route="path"), credentials=creds)


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── 01-A · hook de assinatura ──────────────────────────────────────────────

def test_base_channel_never_verifies_by_default():
    """O default do contrato é 'não verifica' — GOWA/telegram inalterados."""
    assert Channel.verify_inbound_signature(object(), b"qualquer coisa", {}) is True


def test_signature_valid_and_invalid():
    ch = _channel(app_secret=APP_SECRET)
    body = json.dumps({"entry": [{"messaging": []}]}).encode()
    assert ch.verify_inbound_signature(body, {"X-Hub-Signature-256": _sign(body)}) is True
    assert ch.verify_inbound_signature(body, {"x-hub-signature-256": _sign(body)}) is True
    # Corpo adulterado com a assinatura antiga
    assert ch.verify_inbound_signature(body + b" ", {"X-Hub-Signature-256": _sign(body)}) is False
    # Segredo errado
    assert ch.verify_inbound_signature(
        body, {"X-Hub-Signature-256": _sign(body, "outro")}) is False
    # Header ausente / malformado
    assert ch.verify_inbound_signature(body, {}) is False
    assert ch.verify_inbound_signature(body, {"X-Hub-Signature-256": "sha1=abc"}) is False


def test_signature_without_app_secret_is_permissive():
    """Canal ainda sem App Secret não pode ficar mudo — passa e loga aviso."""
    ch = _channel()
    assert ch.verify_inbound_signature(b"{}", {}) is True


# ── 01-B · parser entry[].messaging[] ──────────────────────────────────────

def _payload(item: dict) -> dict:
    return {"object": "page", "entry": [{"id": "PAGE1", "time": 1,
                                         "messaging": [item]}]}


def test_parse_text_message():
    ch = _channel()
    events = ch.parse_inbound(_payload({
        "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
        "timestamp": 1700000000000,
        "message": {"mid": "m_1", "text": "olá"},
    }))
    assert len(events) == 1
    ev = events[0]
    assert (ev.kind, ev.direction, ev.chat_id, ev.text) == ("message", "in", "PSID1", "olá")
    assert ev.external_msg_id == "m_1"
    assert ev.ts == pytest.approx(1700000000.0)


def test_parse_attachment_carries_url_as_media_id():
    ch = _channel()
    events = ch.parse_inbound(_payload({
        "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
        "message": {"mid": "m_2", "attachments": [
            {"type": "image", "payload": {"url": "https://cdn.meta/x.jpg"}}]},
    }))
    assert len(events) == 1
    ev = events[0]
    assert ev.media_type == "image"
    # O core baixa por download_media(media_id) — para Meta o "id" É a URL do CDN.
    assert ev.media_extras["media_id"] == "https://cdn.meta/x.jpg"


def test_parse_file_attachment_maps_to_document():
    ch = _channel()
    ev = ch.parse_inbound(_payload({
        "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
        "message": {"mid": "m_3", "attachments": [
            {"type": "file", "payload": {"url": "https://cdn.meta/a.pdf"}}]},
    }))[0]
    assert ev.media_type == "document"


def test_parse_echo_flips_direction_and_chat_id():
    """No echo os papéis invertem: a conversa continua sendo a do HUMANO."""
    ch = _channel()
    ev = ch.parse_inbound(_payload({
        "sender": {"id": "PAGE1"}, "recipient": {"id": "PSID1"},
        "message": {"mid": "m_4", "is_echo": True, "text": "resposta"},
    }))[0]
    assert ev.direction == "out"
    assert ev.chat_id == "PSID1"


def test_parse_reaction_and_unreact():
    ch = _channel()
    ev = ch.parse_inbound(_payload({
        "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
        "reaction": {"mid": "m_1", "action": "react", "emoji": "❤️"},
    }))[0]
    assert ev.kind == "reaction"
    assert ev.media_extras["emoji"] == "❤️"
    assert ev.media_extras["reacted_message_id"] == "m_1"

    ev = ch.parse_inbound(_payload({
        "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
        "reaction": {"mid": "m_1", "action": "unreact", "emoji": "❤️"},
    }))[0]
    assert ev.media_extras["emoji"] == ""


def test_parse_delivery_and_read_receipts():
    ch = _channel()
    events = ch.parse_inbound(_payload({
        "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
        "delivery": {"mids": ["m_1", "m_2"], "watermark": 17},
    }))
    assert [e.kind for e in events] == ["receipt", "receipt"]
    assert {e.external_msg_id for e in events} == {"m_1", "m_2"}
    assert all(e.media_extras["status"] == "delivered" for e in events)

    ev = ch.parse_inbound(_payload({
        "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
        "read": {"watermark": 18},
    }))[0]
    assert ev.media_extras["status"] == "read"


def test_parse_postback_becomes_message():
    ch = _channel()
    ev = ch.parse_inbound(_payload({
        "sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"},
        "postback": {"mid": "m_5", "title": "Falar com atendente",
                     "payload": "TALK_HUMAN"},
    }))[0]
    assert ev.kind == "message" and ev.text == "Falar com atendente"
    assert ev.media_extras["payload"] == "TALK_HUMAN"


def test_parse_is_defensive():
    ch = _channel()
    assert ch.parse_inbound({}) == []
    assert ch.parse_inbound({"entry": [None, {"messaging": [None]}]}) == []
    assert ch.parse_inbound("nao é dict") == []  # type: ignore[arg-type]


# ── 01-B · URL pública de mídia ────────────────────────────────────────────

def test_public_media_url_from_local_path():
    assert (public_media_url("statics/outbox/x.jpg", base="https://bot.example.com/")
            == "https://bot.example.com/statics/outbox/x.jpg")
    assert (public_media_url("/app/statics/media/y.ogg", base="https://bot.example.com")
            == "https://bot.example.com/statics/media/y.ogg")


def test_public_media_url_passthrough_and_failures():
    assert public_media_url("https://cdn/x.jpg") == "https://cdn/x.jpg"
    # fora de statics/ ⇒ vazio (o provider falha com mensagem clara)
    assert public_media_url("/tmp/x.jpg", base="https://bot.example.com") == ""
    assert public_media_url("") == ""


def test_send_media_without_public_base_url_fails_cleanly():
    ch = _channel(app_secret=APP_SECRET, access_token="T")
    res = ch.send_media("PSID1", "image", "/tmp/fora.jpg")
    assert res.ok is False and "URL pública" in res.error


def test_attachment_type_follows_the_file_mime_not_the_label():
    """A Send API valida o tipo declarado contra o Content-Type que ela baixa: um
    .mp4 mandado como ``file`` (rótulo "documento" do painel) volta com
    ``code 100/2018007``. O tipo sai do MIME real do arquivo."""
    # anexado pelo botão "documento", mas é vídeo/imagem/áudio ⇒ tipo da Meta certo
    assert attachment_type_for("document", "statics/outbox/a.mp4") == "video"
    assert attachment_type_for("document", "statics/outbox/a.png") == "image"
    assert attachment_type_for("document", "statics/outbox/a.ogg") == "audio"
    # documento de verdade continua ``file``
    assert attachment_type_for("document", "statics/outbox/a.pdf") == "file"
    # o nome ORIGINAL manda sobre o do arquivo salvo (upload vira .bin)
    assert attachment_type_for("document", "statics/outbox/x.bin", "Manual.pdf") == "file"
    # sem extensão nenhuma, cai no ``kind`` pedido pelo core
    assert attachment_type_for("image", "statics/outbox/sem_ext") == "image"
    assert attachment_type_for("document", "statics/outbox/sem_ext") == "file"


def test_send_media_payload_uses_resolved_attachment_type(monkeypatch):
    import channels.media_urls as media_urls

    monkeypatch.setattr(media_urls, "public_base_url", lambda: "https://bot.example.com")
    ch = _channel(app_secret=APP_SECRET, access_token="T")
    payload = ch._media_payload("PSID1", "document", "statics/outbox/a.mp4")
    assert payload["message"]["attachment"]["type"] == "video"
    assert payload["message"]["attachment"]["payload"]["url"].endswith(
        "/statics/outbox/a.mp4")


# ── 01-C · hook de refresh de token ────────────────────────────────────────

def test_token_refresh_capability_defaults_off_and_hook_is_noop():
    assert ChannelCapabilities().token_refresh is False
    assert ChannelCapabilities(token_refresh=True).token_refresh is True
    ch = _channel()
    assert ch.refresh_token_if_needed() is None  # no-op, nunca levanta


# ── appsecret_proof ────────────────────────────────────────────────────────

def test_appsecret_proof_is_hmac_of_token_with_secret():
    ch = _channel(access_token="TOKEN", app_secret=APP_SECRET)
    expected = hmac.new(APP_SECRET.encode(), b"TOKEN", hashlib.sha256).hexdigest()
    assert ch._appsecret_proof() == expected
    assert ch._auth_params()["appsecret_proof"] == expected
    # Sem app_secret não manda proof (app que não exige)
    assert "appsecret_proof" not in _channel(access_token="TOKEN")._auth_params()
