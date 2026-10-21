"""Rotas do atualizador do binário do GOWA (``/api/plugins/gowa/update*``, ``/rollback``).

O motor (``updater.py``) é testado em ``tests/core/test_gowa_updater.py``; aqui vai
o que só o app real mostra: a porta (RBAC), o contrato das respostas e a trilha.

Rodar::

    cd whatsbot-pro && flock /tmp/whatsbot-pytest.lock \\
        venv/bin/python -m pytest -q tests/integration/test_gowa_update_routes.py
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from db.audit_actions import PLUGIN_ACTION_RE
from tests.plugin_test_utils import loaded_plugin_module

API = "/api/plugins/gowa"


class _Runtime:
    def session_root(self):
        return "."

    def running_version(self):
        return "8.11.0"


@pytest.fixture
def bancada(build_app, authenticated_admin, monkeypatch):
    built = build_app(["gowa"])
    authenticated_admin(built.client, name="Operador atualizador GOWA")
    upd = loaded_plugin_module("gowa", "updater")
    routes = loaded_plugin_module("gowa", "routes")
    linhas: list[dict] = []
    monkeypatch.setattr(
        routes, "_core_audit",
        lambda pid, action, **kw: linhas.append({"p": pid, "a": action, **kw}))
    # Nada de rede nem de binário de verdade nos testes de rota.
    monkeypatch.setattr(upd, "fetch_latest_release", lambda force=False: {
        "version": "9.6.0", "published_at": "2026-10-03T00:00:00Z",
        "url": "https://example.test/9.6.0", "cached": False, "rate_limited": False})
    return SimpleNamespace(client=built.client, upd=upd, routes=routes, linhas=linhas)


def _espera(cond, segundos=5.0):
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_status_sem_runtime_diz_que_nao_gerencia(bancada):
    """No harness o lifecycle é no-op: ninguém registrou o runtime."""
    bancada.upd.set_runtime(None)
    r = bancada.client.get(f"{API}/binary/status")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["data"]["managed"] is False


def test_check_compara_com_a_ultima_release(bancada):
    bancada.upd.set_runtime(_Runtime())
    d = bancada.client.get(f"{API}/binary/check").json()["data"]
    assert d["latest_version"] == "9.6.0" and d["managed"] is True
    assert d["installed_version"] == "8.11.0"          # o que o processo VIVO diz
    assert d["update_available"] is True and d["major_jump"] is True


def test_instalar_sem_runtime_recusa_com_mensagem_clara(bancada):
    bancada.upd.set_runtime(None)
    resp = bancada.client.post(f"{API}/binary/update", json={"version": "9.6.0"})
    assert resp.status_code == 200, resp.text
    r = resp.json()
    assert r["ok"] is False and "controle" in r["error"]


def test_versao_invalida_nunca_chega_a_baixar(bancada, monkeypatch):
    bancada.upd.set_runtime(_Runtime())
    chamou = []
    monkeypatch.setattr(bancada.upd, "install", lambda *a, **k: chamou.append(1))
    r = bancada.client.post(f"{API}/binary/update", json={"version": "9.6.0/../../x"}).json()
    assert r["ok"] is False and chamou == []


def test_fora_da_faixa_pede_confirmacao_e_so_instala_confirmado(bancada, monkeypatch):
    bancada.upd.set_runtime(_Runtime())
    chamadas = []
    monkeypatch.setattr(
        bancada.upd, "install",
        lambda v, runtime, force_unsupported=False: chamadas.append((v, force_unsupported))
        or {"ok": True, "version": v, "message": "ok"})

    pedido = bancada.client.post(f"{API}/binary/update", json={"version": "10.0.0"}).json()
    assert pedido["ok"] is False and pedido["requires_confirmation"] is True
    assert chamadas == []                               # nada disparou

    ok = bancada.client.post(
        f"{API}/binary/update", json={"version": "10.0.0", "confirm_unsupported": True}).json()
    assert ok["ok"] is True and ok["data"]["started"] is True
    assert _espera(lambda: chamadas == [("10.0.0", True)])


def test_instalacao_em_andamento_barra_a_segunda(bancada):
    bancada.upd.set_runtime(_Runtime())
    assert bancada.upd._LOCK.acquire(blocking=False)
    try:
        r = bancada.client.post(f"{API}/binary/update", json={"version": "9.6.0"}).json()
        assert r["ok"] is False and "andamento" in r["error"]
        r2 = bancada.client.post(f"{API}/binary/rollback").json()
        assert r2["ok"] is False
    finally:
        bancada.upd._LOCK.release()


def test_rollback_sem_versao_anterior(bancada, monkeypatch):
    bancada.upd.set_runtime(_Runtime())
    monkeypatch.setattr(bancada.upd, "can_rollback", lambda: False)
    r = bancada.client.post(f"{API}/binary/rollback").json()
    assert r["ok"] is False and "anterior" in r["error"]


def test_instalar_grava_na_trilha_com_acao_valida_e_sem_segredo(bancada, monkeypatch):
    bancada.upd.set_runtime(_Runtime())
    monkeypatch.setattr(
        bancada.upd, "install",
        lambda v, runtime, force_unsupported=False: {
            "ok": False, "version": v, "rolled_back": True, "message": "não subiu"})
    assert bancada.client.post(f"{API}/binary/update", json={"version": "9.6.0"}).json()["ok"] is True
    assert _espera(lambda: any(l["a"] == "binario.atualizar" for l in bancada.linhas))

    linha = next(l for l in bancada.linhas if l["a"] == "binario.atualizar")
    assert PLUGIN_ACTION_RE.match(f"gowa.{linha['a']}")
    assert linha["before"]["alvo"] == "9.6.0"
    assert linha["after"] == {"ok": False, "versao": "9.6.0", "revertido": True,
                              "mensagem": "não subiu"}


def test_sem_login_a_api_e_fechada(bancada):
    """O atualizador executa um binário baixado: nada de rota aberta."""
    from fastapi.testclient import TestClient
    anonimo = TestClient(bancada.client.app)
    for metodo, caminho in (("get", "/binary/status"), ("get", "/binary/check"),
                            ("post", "/binary/update"), ("post", "/binary/rollback")):
        r = getattr(anonimo, metodo)(f"{API}{caminho}")
        assert r.status_code in (401, 403), (metodo, caminho, r.status_code)
