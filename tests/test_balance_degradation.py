"""Plano 42 C — ``/api/balance`` degrada graciosamente em vez de 502.

Quando o proxy Techify ``/credits`` está fora E não há cache (janela do boot,
antes de qualquer chamada de LLM), o endpoint ANTES devolvia 502 e o painel ficava
"sem saldo". Agora devolve 200 com ``available:false`` + ``balance:null`` + razão,
e o frontend degrada silenciosamente. O 400 de "sem api_key" permanece.

    venv/bin/python -m pytest tests/test_balance_degradation.py -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import server.balance_monitor as _bm


@pytest.fixture
def bal_app(build_app):
    built = build_app(["gowa"])
    prev_key = built.settings.get("openrouter_api_key", "")
    prev_thr = built.settings.get("low_balance_threshold", 0.50)
    built.settings.set("low_balance_threshold", 0.50)  # determinístico
    try:
        yield built
    finally:
        built.settings.set("openrouter_api_key", prev_key or "")
        built.settings.set("low_balance_threshold", prev_thr)


def test_no_api_key_returns_400(bal_app):
    bal_app.settings.set("openrouter_api_key", "")
    r = bal_app.client.get("/api/balance")
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_proxy_down_no_cache_degrades_to_200(bal_app):
    """Proxy fora + sem cache ⇒ 200 available:false (NÃO 502)."""
    bal_app.settings.set("openrouter_api_key", "sk-test-balance")
    with patch.object(_bm, "fetch_balance", AsyncMock(return_value=None)), \
         patch.object(_bm, "get_cached", return_value=None):
        r = bal_app.client.get("/api/balance")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["data"]["available"] is False
    assert d["data"]["balance"] is None
    assert d["data"].get("reason")


def test_proxy_down_with_cache_uses_cache(bal_app):
    """Proxy fora mas cache quente ⇒ 200 available:true com o saldo do cache."""
    bal_app.settings.set("openrouter_api_key", "sk-test-balance")
    with patch.object(_bm, "fetch_balance", AsyncMock(return_value=None)), \
         patch.object(_bm, "get_cached",
                      return_value={"total_credits": 5.0, "total_usage": 1.0,
                                    "remaining": 4.0}):
        r = bal_app.client.get("/api/balance")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["available"] is True
    assert d["remaining"] == 4.0
    assert d["balance"] == 4.0
    assert d["below_threshold"] is False  # 4.0 >= 0.5


def test_live_fetch_ok_computes_below_threshold(bal_app):
    """Fetch ao vivo OK ⇒ available:true + below_threshold calculado."""
    bal_app.settings.set("openrouter_api_key", "sk-test-balance")
    with patch.object(_bm, "fetch_balance",
                      AsyncMock(return_value={"total_credits": 10.0,
                                              "total_usage": 9.8, "remaining": 0.2})):
        r = bal_app.client.get("/api/balance")
    d = r.json()["data"]
    assert r.status_code == 200
    assert d["available"] is True
    assert d["balance"] == 0.2
    assert d["below_threshold"] is True  # 0.2 < 0.5 default


def test_prime_cache_seeds_snapshot():
    """``prime_cache`` semeia ``get_cached`` a partir de um fetch bem-sucedido."""
    import asyncio
    with patch.object(_bm, "fetch_balance",
                      AsyncMock(return_value={"total_credits": 3.0,
                                              "total_usage": 0.5, "remaining": 2.5})):
        asyncio.run(_bm.prime_cache("sk-test-balance"))
    cached = _bm.get_cached()
    assert cached is not None
    assert cached["remaining"] == 2.5
