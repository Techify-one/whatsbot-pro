"""Plano 51 · sub-plano 01 · Fase 3 — versionamento de ``ai_variables``.

save→history→rollback→dedup no repo + rotas /api/ai/variables/{name}/history e
/rollback/{version}. ai_variables era a única entidade de IA sem revert (D4).
"""

from __future__ import annotations

from db.repositories import variable_repo


def _cleanup(name: str) -> None:
    variable_repo.delete(name)


# ── Repo ─────────────────────────────────────────────────────────────────────

def test_save_bumps_version_and_snapshots(_engine_ready):
    name = "p51_var_a"
    _cleanup(name)
    try:
        v1 = variable_repo.save(name, "valor um")
        assert v1["version"] == 1
        v2 = variable_repo.save(name, "valor dois")
        assert v2["version"] == 2

        hist = variable_repo.list_history(name)
        assert [h["version"] for h in hist] == [2, 1]
        snap1 = variable_repo.get_snapshot(name, 1)
        assert snap1 == {"name": name, "value": "valor um"}
    finally:
        _cleanup(name)


def test_save_identical_value_is_noop(_engine_ready):
    name = "p51_var_dedup"
    _cleanup(name)
    try:
        variable_repo.save(name, "mesmo valor")
        again = variable_repo.save(name, "mesmo valor")
        assert again["version"] == 1
        assert len(variable_repo.list_history(name)) == 1
    finally:
        _cleanup(name)


def test_rollback_is_forward_new_version(_engine_ready):
    name = "p51_var_rb"
    _cleanup(name)
    try:
        variable_repo.save(name, "original")
        variable_repo.save(name, "editado")
        rolled = variable_repo.rollback(name, 1)
        assert rolled["value"] == "original"
        assert rolled["version"] == 3  # forward, não-destrutivo
        assert [h["version"] for h in variable_repo.list_history(name)] == [3, 2, 1]
        assert variable_repo.get(name)["value"] == "original"
        # versão inexistente → None
        assert variable_repo.rollback(name, 99) is None
    finally:
        _cleanup(name)


def test_delete_removes_history(_engine_ready):
    name = "p51_var_del"
    _cleanup(name)
    variable_repo.save(name, "x")
    variable_repo.save(name, "y")
    assert variable_repo.delete(name) == 1
    assert variable_repo.get(name) is None
    assert variable_repo.list_history(name) == []


# ── Rotas ────────────────────────────────────────────────────────────────────

def test_variable_history_and_rollback_endpoints(build_app):
    name = "p51_var_api"
    _cleanup(name)
    built = build_app(["gowa"])
    try:
        r = built.client.put(f"/api/ai/variables/{name}", json={"value": "um"})
        assert r.status_code == 200 and r.json()["ok"], r.text
        r = built.client.put(f"/api/ai/variables/{name}", json={"value": "dois"})
        assert r.json()["data"]["version"] == 2

        r = built.client.get(f"/api/ai/variables/{name}/history")
        assert r.status_code == 200
        assert [h["version"] for h in r.json()["data"]] == [2, 1]

        r = built.client.get(f"/api/ai/variables/{name}/history/1")
        assert r.json()["data"]["value"] == "um"

        r = built.client.post(f"/api/ai/variables/{name}/rollback/1")
        assert r.status_code == 200
        assert r.json()["data"]["value"] == "um"
        assert r.json()["data"]["version"] == 3

        r = built.client.post(f"/api/ai/variables/{name}/rollback/99")
        assert r.status_code == 404
    finally:
        _cleanup(name)
