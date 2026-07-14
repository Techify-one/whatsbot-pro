"""Avaliação pública de protocolo (plano 50) — round-trip da página externa.

Cobre os 2 endpoints PÚBLICOS (auth-exempt, sob ``/public/``) que a página de
avaliação (Cloudflare Worker) chama pelo ``id_protocol``:

* ``GET  /api/plugins/protocolos/public/avaliacao/{id_protocol}`` → nome do atendente
* ``POST /api/plugins/protocolos/public/avaliacao/{id_protocol}`` → grava nota+sugestão

Integração via ``build_app`` (motor Postgres de teste): a migration ``015`` cria a
tabela e o plugin monta as rotas. O token é semeado pela própria
``logic.register_avaliacao`` (a função que roda no fechamento do protocolo).

    venv/bin/python -m pytest tests/test_avaliacao_protocolo.py -q
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# O plugin ``protocolos`` é um plugin Pro: a cópia VIVA/instalada mora em
# ``storages/plugins/protocolos`` (não versionada), enquanto ``assets/plugin_examples``
# guarda um espelho que pode estar atrás. O ``build_app`` copia de ``assets`` por padrão
# — aqui apontamos para ``storages/plugins`` para exercitar o código instalado (real).
_STORAGES_PLUGINS = Path(__file__).resolve().parents[1] / "storages" / "plugins"


@pytest.fixture(autouse=True)
def _load_from_storages(monkeypatch):
    monkeypatch.setattr("tests.support.REAL_PLUGIN_EXAMPLES", _STORAGES_PLUGINS)


def _logic():
    """Módulo logic do plugin JÁ carregado por ``build_app`` (whatsbot_plugins.protocolos)."""
    return importlib.import_module("whatsbot_plugins.protocolos.logic")


def _seed(idp: str, *, atendente: str = "João Atendente",
          protocolo_id: int = 4242, contact_id: int = 77) -> None:
    """Registra um token de avaliação PENDENTE (snapshot), como o fechamento faria."""
    _logic().register_avaliacao(
        {"id": protocolo_id, "contact_id": contact_id, "assignee_user_id": None,
         "assignee_name": atendente, "contact_phone": "5511999999999",
         "contact_name": "Maria Cliente"},
        id_protocol=idp, conversation_id=None, channel_id="")


def test_avaliacao_round_trip(build_app):
    built = build_app(["gowa", "protocolos"])
    c = built.client
    idp = "26062026-102708.735-90001"
    _seed(idp, atendente="João Atendente")

    # GET consulta o nome do atendente pelo id_protocol (o que a página faz ao abrir).
    r = c.get(f"/api/plugins/protocolos/public/avaliacao/{idp}")
    assert r.status_code == 200, r.text          # rota /public/ alcançada sem auth
    data = r.json()
    assert data["ok"] is True
    assert data["data"]["atendente"] == "João Atendente"
    assert data["data"]["protocolo"] == idp
    assert data["data"]["ja_avaliado"] is False

    # POST grava a nota do cliente.
    r = c.post(f"/api/plugins/protocolos/public/avaliacao/{idp}",
               json={"nota": 5, "sugestao": "Ótimo atendimento!"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # GET agora reflete a avaliação (uso único consumido).
    r = c.get(f"/api/plugins/protocolos/public/avaliacao/{idp}")
    body = r.json()["data"]
    assert body["ja_avaliado"] is True
    assert body["nota"] == 5

    # POST de novo → 409 (uso único: já respondida).
    r = c.post(f"/api/plugins/protocolos/public/avaliacao/{idp}",
               json={"nota": 3, "sugestao": "mudei de ideia"})
    assert r.status_code == 409, r.text


def test_avaliacao_unknown_returns_404(build_app):
    built = build_app(["gowa", "protocolos"])
    r = built.client.get("/api/plugins/protocolos/public/avaliacao/nao-existe-000")
    assert r.status_code == 404
    assert r.json()["ok"] is False


def test_avaliacao_invalid_nota_rejected(build_app):
    built = build_app(["gowa", "protocolos"])
    c = built.client
    idp = "26062026-102708.735-90002"
    _seed(idp)
    # Fora de 1..5 → 400.
    r = c.post(f"/api/plugins/protocolos/public/avaliacao/{idp}", json={"nota": 9})
    assert r.status_code == 400, r.text
    # Não numérica → 400.
    r = c.post(f"/api/plugins/protocolos/public/avaliacao/{idp}", json={"nota": "abc"})
    assert r.status_code == 400, r.text
    # Booleana (true→1) → rejeitada, não grava.
    r = c.post(f"/api/plugins/protocolos/public/avaliacao/{idp}", json={"nota": True})
    assert r.status_code == 400, r.text
    # Corpo JSON truthy NÃO-dict não pode virar 500 (rota pública) → 400 controlado.
    r = c.post(f"/api/plugins/protocolos/public/avaliacao/{idp}", json=5)
    assert r.status_code == 400, r.text
    # A avaliação continua PENDENTE (nenhum POST inválido a consumiu).
    r = c.get(f"/api/plugins/protocolos/public/avaliacao/{idp}")
    assert r.json()["data"]["ja_avaliado"] is False


def test_avaliacao_sugestao_capped_and_stored(build_app):
    built = build_app(["gowa", "protocolos"])
    plogic = _logic()
    idp = "26062026-102708.735-90003"
    _seed(idp, atendente="Ana")
    data, err = plogic.record_avaliacao(idp, 4, "x" * 5000, ip="1.2.3.4")
    assert err is None
    assert data["nota"] == 4
    assert len(data["sugestao"]) == 2000     # cortada no teto _SUGESTAO_CAP
    got = plogic.get_avaliacao_public(idp)
    assert got["ja_avaliado"] is True and got["nota"] == 4


def test_list_filter_by_nota_and_protocol_code(build_app):
    import json
    import time
    from sqlalchemy import text
    from db.engine import get_engine
    built = build_app(["gowa", "protocolos"])
    plogic = _logic()
    ts = time.time()
    with get_engine().begin() as conn:
        pid = conn.execute(text(
            "INSERT INTO plugin_protocolos_protocolos "
            "(contact_id, contact_phone, contact_name, status, assignee_name, fields, "
            " opened_at, created_at, updated_at, obs) "
            "VALUES (:cid, '5511', 'Cliente Nota', 'fechado', 'Ana', '{}', :ts, :ts, :ts, '') "
            "RETURNING id"), {"cid": 4141, "ts": ts}).scalar()
    idp = f"20260101-120000-{pid}"
    plogic.register_avaliacao(
        {"id": pid, "contact_id": 4141, "assignee_user_id": None, "assignee_name": "Ana",
         "contact_phone": "5511", "contact_name": "Cliente Nota"},
        id_protocol=idp, conversation_id=None, channel_id="")
    _, err = plogic.record_avaliacao(idp, 5, "muito bom")
    assert err is None
    c = built.client
    # Filtro nota=5 → o protocolo aparece, com a avaliação anexada em cada linha.
    r = c.get("/api/plugins/protocolos/protocolos", params={"nota": json.dumps(["5"])})
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert pid in [p["id"] for p in rows]
    assert next(p for p in rows if p["id"] == pid)["avaliacao"]["nota"] == 5
    # Filtro nota=1 → NÃO aparece (a nota é 5).
    r = c.get("/api/plugins/protocolos/protocolos", params={"nota": json.dumps(["1"])})
    assert pid not in [p["id"] for p in r.json()["data"]]
    # Busca pelo CÓDIGO do protocolo (último grupo de dígitos = id) → aparece.
    r = c.get("/api/plugins/protocolos/protocolos", params={"q": idp})
    assert pid in [p["id"] for p in r.json()["data"]]
    # Busca pelo id puro também.
    r = c.get("/api/plugins/protocolos/protocolos", params={"q": str(pid)})
    assert pid in [p["id"] for p in r.json()["data"]]
