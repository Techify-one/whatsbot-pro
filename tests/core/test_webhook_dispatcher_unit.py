"""Unidades do despachante de webhooks de saída (fase 8) — sem rede, sem banco.

O que importa aqui é a REGRA de quem recebe o quê e a forma da assinatura; a
entrega em si (POST, backoff, dead-letter) depende do repo e do loop.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from server import webhook_dispatcher as wd


# ── allowlist ───────────────────────────────────────────────────────────────

def test_wildcard_covers_only_the_curated_set():
    """``"*"`` NÃO é "qualquer coisa do barramento".

    É o que impede que um endpoint cadastrado hoje comece a receber, num upgrade
    do core, um evento novo que ninguém revisou — e que ``llm.after`` (que leva o
    histórico da conversa e o prompt) saia da instalação por descuido.
    """
    assert wd.event_allowed("message.sent", ["*"])
    assert not wd.event_allowed("llm.after", ["*"])
    assert not wd.event_allowed("presence.changed", ["*"])
    assert not wd.event_allowed("protocolos.ciclo.aberto", ["*"])


def test_exact_and_glob_subscriptions():
    assert wd.event_allowed("message.sent", ["message.sent"])
    assert not wd.event_allowed("message.saved", ["message.sent"])
    # Evento de plugin só chega quando nomeado — direto ou por curinga.
    assert wd.event_allowed("protocolos.ciclo.aberto", ["protocolos.*"])
    assert not wd.event_allowed("retornos.agendado", ["protocolos.*"])


def test_empty_subscription_receives_nothing():
    assert not wd.event_allowed("message.sent", [])
    assert not wd.event_allowed("message.sent", None)


# ── sanitização do corpo ────────────────────────────────────────────────────

def test_sanitize_strips_secrets_and_bulk():
    payload = {
        "phone": "5511999999999",
        "access_token": "EAAB...",
        "raw": {"audio": "base64" * 10000},
        "_audit_before": {"x": 1},
        "nested": {"password": "hunter2", "ok": True},
        "list": [{"token": "t", "keep": 1}],
    }
    out = wd.sanitize(payload)
    assert out == {"phone": "5511999999999", "nested": {"ok": True},
                   "list": [{"keep": 1}]}
    blob = repr(out)
    assert "EAAB" not in blob and "hunter2" not in blob and "base64" not in blob


# ── assinatura ──────────────────────────────────────────────────────────────

def test_signature_matches_hmac_of_the_exact_bytes():
    secret, body = "whsec_abc", b'{"event":"message.sent"}'
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert wd.signature_for(secret, body) == f"sha256={expected}"


def test_signature_changes_with_a_single_byte():
    """Serializar de novo do outro lado quebra a comparação — é o ponto."""
    a = wd.signature_for("s", b'{"a":1}')
    b = wd.signature_for("s", b'{"a": 1}')     # um espaço a mais
    assert a != b


def test_generate_secret_shape():
    s1, s2 = wd.generate_secret(), wd.generate_secret()
    assert s1.startswith("whsec_") and s1 != s2
