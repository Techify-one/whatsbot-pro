"""Plano 178 / Fase 2 — as chaves ``ai_reactivation_*`` na API de configuração.

* ``GET /api/config`` devolve a regra EFETIVA, sempre bem formada;
* ``PUT /api/config`` valida cada chave e DESCARTA o lixo sem derrubar o resto (a tela
  salva tudo junto — um campo ruim não pode impedir a gravação dos outros);
* ``ai_reactivation_last_run`` e ``provisioning_phones`` são estado do servidor e NÃO
  são graváveis pela API.

    venv/bin/python -m pytest tests/integration/test_ai_reactivation_config.py -q
"""

from __future__ import annotations

import pytest

from db.repositories import config_repo

_KEYS = (
    "ai_reactivation_enabled", "ai_reactivation_unit", "ai_reactivation_value",
    "ai_reactivation_weekdays", "ai_reactivation_time", "ai_reactivation_last_sender",
    "ai_reactivation_include_groups_archived", "ai_reactivation_timezone",
)

_DEFAULTS = {
    "ai_reactivation_enabled": False, "ai_reactivation_unit": "hours",
    "ai_reactivation_value": 24, "ai_reactivation_weekdays": [0, 1, 2, 3, 4],
    "ai_reactivation_time": "08:00", "ai_reactivation_last_sender": "both",
    "ai_reactivation_include_groups_archived": False, "ai_reactivation_timezone": "",
}

@pytest.fixture(autouse=True)
def _reset():
    config_repo.set_many(_DEFAULTS)
    config_repo.set("ai_reactivation_last_run", 0.0)
    config_repo.set("provisioning_phones", [])
    yield
    config_repo.set_many(_DEFAULTS)

def _get(client) -> dict:
    return client.get("/api/config").json()["data"]

def test_get_exposes_the_eight_keys_with_their_defaults(client):
    data = _get(client)
    for key in _KEYS:
        assert key in data, f"{key} não vem no GET /api/config"
        assert data[key] == _DEFAULTS[key]

def test_get_does_not_expose_server_state(client):
    data = _get(client)
    assert "ai_reactivation_last_run" not in data
    assert "provisioning_phones" not in data

def test_put_persists_a_good_rule(client):
    body = {
        "ai_reactivation_enabled": True, "ai_reactivation_unit": "schedule",
        "ai_reactivation_value": 3, "ai_reactivation_weekdays": [5, 1, 1],
        "ai_reactivation_time": "09:30", "ai_reactivation_last_sender": "customer",
        "ai_reactivation_include_groups_archived": True,
        "ai_reactivation_timezone": "America/Sao_Paulo",
    }
    r = client.put("/api/config", json=body)
    assert r.status_code == 200 and r.json()["ok"]
    data = _get(client)
    assert data["ai_reactivation_enabled"] is True
    assert data["ai_reactivation_unit"] == "schedule"
    assert data["ai_reactivation_value"] == 3
    assert data["ai_reactivation_weekdays"] == [1, 5]          # ordenado, sem repetição
    assert data["ai_reactivation_time"] == "09:30"
    assert data["ai_reactivation_last_sender"] == "customer"
    assert data["ai_reactivation_include_groups_archived"] is True
    assert data["ai_reactivation_timezone"] == "America/Sao_Paulo"

def test_put_junk_is_dropped_without_failing_the_rest(client):
    r = client.put("/api/config", json={
        "ai_reactivation_enabled": "sim",            # bool estrito
        "ai_reactivation_unit": "semanas",           # fora da lista
        "ai_reactivation_time": "25:00",             # hora inválida
        "ai_reactivation_last_sender": "ia",
        "ai_reactivation_timezone": "../etc/passwd",
        "ai_reactivation_weekdays": "0,1",
        # …e uma chave BOA no mesmo corpo continua sendo gravada:
        "ai_reactivation_value": 12,
    })
    assert r.status_code == 200 and r.json()["ok"]
    data = _get(client)
    for key in ("ai_reactivation_enabled", "ai_reactivation_unit",
                "ai_reactivation_time", "ai_reactivation_last_sender",
                "ai_reactivation_timezone", "ai_reactivation_weekdays"):
        assert data[key] == _DEFAULTS[key], f"{key} gravou lixo"
    assert data["ai_reactivation_value"] == 12

def test_put_clamps_the_value_instead_of_rejecting_it(client):
    client.put("/api/config", json={"ai_reactivation_value": 0})
    assert _get(client)["ai_reactivation_value"] == 1
    client.put("/api/config", json={"ai_reactivation_value": 10**9})
    assert _get(client)["ai_reactivation_value"] == 100_000

def test_put_cannot_write_server_state(client):
    config_repo.set("provisioning_phones", ["5511900000001"])
    r = client.put("/api/config", json={
        "ai_reactivation_last_run": 99999999999.0,
        "provisioning_phones": [],
    })
    assert r.status_code == 200
    assert float(config_repo.get("ai_reactivation_last_run", 0)) == 0.0
    assert config_repo.get("provisioning_phones") == ["5511900000001"]

def test_get_normalizes_garbage_that_was_written_straight_to_the_database(client):
    """Lixo gravado por fora da API (import manual, edição à mão) nunca chega à tela."""
    config_repo.set("ai_reactivation_unit", "lixo")
    config_repo.set("ai_reactivation_time", "99:99")
    data = _get(client)
    assert data["ai_reactivation_unit"] == "hours"
    assert data["ai_reactivation_time"] == "08:00"
