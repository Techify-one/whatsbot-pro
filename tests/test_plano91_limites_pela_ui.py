"""Plano 91 · F4 — os limites de tool são configuráveis PELA TELA (D1).

O backend já servia as três chaves (``exposed=True, writable=True``); o que
faltava era o campo. Este teste trava o contrato de que a tela depende:

1. ``GET /api/config`` devolve os três guardrails;
2. ``PUT /api/config`` grava os valores do plano (5 por tool / 15 global);
3. o limite passa a valer na leitura seguinte, **sem restart** — o motor lê a
   config a cada mensagem;
4. ``0`` desliga de verdade (não pode ser reinterpretado como "use o default");
5. a mensagem de bloqueio **cita a rota de escape**, senão um limite apertado
   deixaria o agente sem saída (R2).

    venv/bin/python -m pytest tests/test_plano91_limites_pela_ui.py -q
"""

from __future__ import annotations

from agent import agno_engine
from ai_engine import hooks

_KEYS = ("ai_tool_call_limit_per_tool", "ai_tool_call_limit_total", "ai_max_route_depth")


def test_get_config_expoe_os_tres_guardrails(client):
    data = client.get("/api/config").json()["data"]
    for key in _KEYS:
        assert key in data, f"{key} não vem no GET /api/config — a tela não conseguiria carregar"


def test_put_grava_os_valores_do_plano_e_valem_sem_restart(client, monkeypatch):
    monkeypatch.delenv("WHATSBOT_TOOL_CALL_LIMIT", raising=False)
    r = client.put("/api/config", json={
        "ai_tool_call_limit_per_tool": 5,
        "ai_tool_call_limit_total": 15,
        "ai_max_route_depth": 5,
    })
    assert r.status_code == 200 and r.json()["ok"]

    data = client.get("/api/config").json()["data"]
    assert data["ai_tool_call_limit_per_tool"] == 5
    assert data["ai_tool_call_limit_total"] == 15

    # Sem restart: o teto global é relido da config a cada run.
    assert agno_engine._resolve_tool_call_limit() == 15

    # E o limite por-tool: 5 chamadas já executadas ⇒ a 6ª é bloqueada.
    executed = [{"tool": "pesquisar_ofertas", "args": {}} for _ in range(5)]
    bloqueio = hooks.check_hooks({}, "pesquisar_ofertas", executed, default_call_limit=5)
    assert bloqueio and "limite" in bloqueio.lower()
    # 4 chamadas ⇒ a 5ª ainda passa.
    assert hooks.check_hooks(
        {}, "pesquisar_ofertas", executed[:4], default_call_limit=5) is None


def test_zero_desliga_o_limite_em_vez_de_virar_default(client, monkeypatch):
    """0 é "sem limite" — a tela precisa conseguir DESLIGAR o freio."""
    monkeypatch.delenv("WHATSBOT_TOOL_CALL_LIMIT", raising=False)
    client.put("/api/config", json={
        "ai_tool_call_limit_per_tool": 0, "ai_tool_call_limit_total": 0})
    assert client.get("/api/config").json()["data"]["ai_tool_call_limit_per_tool"] == 0
    assert agno_engine._resolve_tool_call_limit() is None
    executed = [{"tool": "pesquisar_ofertas", "args": {}} for _ in range(30)]
    assert hooks.check_hooks({}, "pesquisar_ofertas", executed, default_call_limit=0) is None
    client.put("/api/config", json={
        "ai_tool_call_limit_per_tool": 0, "ai_tool_call_limit_total": 25})


def test_bloqueio_aponta_a_rota_de_escape(client):
    """R2: com limite ligado, o modelo precisa saber COMO sair — não só que parou."""
    executed = [{"tool": "pesquisar_ofertas", "args": {}} for _ in range(5)]
    msg = hooks.check_hooks({}, "pesquisar_ofertas", executed, default_call_limit=5)
    assert hooks.ESCAPE_TOOL in msg


def test_chamada_bloqueada_nao_gasta_a_propria_cota(client):
    """Gotcha do §3.4 que motivou usar os DOIS limites: quem freia é o global."""
    executed = [
        {"tool": "pesquisar_ofertas", "args": {}, "skipped": True, "blocked": "x"}
        for _ in range(20)
    ]
    assert hooks.check_hooks({}, "pesquisar_ofertas", executed, default_call_limit=5) is None
