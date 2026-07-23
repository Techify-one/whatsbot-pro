"""Plano 67 — opt-in do plugin protocolos ao seam de manter atendente.

Cobre o setting ``resolve_keep_assignee`` (default OFF) e o handler
``clear_assignee_on_close`` que o plugin registra em
``filter.conversation.clear_assignee_on_close``:

* OFF/default → o handler devolve o ``value`` recebido (o core limpa, como hoje).
* ON          → o handler devolve ``False`` (o core mantém o atendente).

Também valida o round-trip do ``general-config`` (a chave aparece no GET e o PUT a
persiste sem zerar os defaults das outras chaves).

    venv/bin/python -m pytest tests/test_plano67_protocolos_toggle.py -q
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# Exercita a cópia INSTALADA (real) em ``storages/plugins/protocolos``.
_STORAGES_PLUGINS = Path(__file__).resolve().parents[1] / "storages" / "plugins"


@pytest.fixture(autouse=True)
def _load_from_storages(monkeypatch):
    monkeypatch.setattr("tests.support.REAL_PLUGIN_EXAMPLES", _STORAGES_PLUGINS)


def _logic():
    return importlib.import_module("whatsbot_plugins.protocolos.logic")


def test_handler_off_by_default(build_app):
    """Default OFF → o handler devolve o value recebido (core limpa)."""
    build_app(["gowa", "protocolos"])
    logic = _logic()
    from db.repositories import config_repo
    config_repo.delete_prefix(logic._general_key("resolve_keep_assignee"))

    assert logic.resolve_keep_assignee_enabled() is False
    assert logic.clear_assignee_on_close(None, True) is True


def test_handler_on_keeps_assignee(build_app):
    """ON → o handler devolve False (core mantém o atendente)."""
    build_app(["gowa", "protocolos"])
    logic = _logic()

    logic.set_general_config({"resolve_keep_assignee": True})
    assert logic.resolve_keep_assignee_enabled() is True
    assert logic.clear_assignee_on_close(None, True) is False


def test_general_config_roundtrip(build_app):
    """A chave aparece no get_general_config e o set a persiste; um payload SEM a
    chave não a zera (default OFF preservado como os demais defaults)."""
    build_app(["gowa", "protocolos"])
    logic = _logic()
    from db.repositories import config_repo
    config_repo.delete_prefix(logic._general_key("resolve_keep_assignee"))

    cfg = logic.get_general_config()
    assert "resolve_keep_assignee" in cfg
    assert cfg["resolve_keep_assignee"] is False

    logic.set_general_config({"resolve_keep_assignee": True})
    assert logic.get_general_config()["resolve_keep_assignee"] is True

    # Payload legado (sem a chave) NÃO zera o valor já gravado.
    logic.set_general_config({"relink_prompt_enabled": True})
    assert logic.get_general_config()["resolve_keep_assignee"] is True


def test_reactivate_ai_on_close_toggle_roundtrip(build_app):
    """O toggle "Religar a IA ao finalizar" (exposto na aba Configurações gerais) usa a
    MESMA chave que o getter/rota de fechar leem (plugin.protocolos.reactivate_ai_on_close,
    NÃO _general_key). Default ON; set persiste; payload sem a chave não zera."""
    build_app(["gowa", "protocolos"])
    logic = _logic()
    from db.repositories import config_repo
    key = f"plugin.{logic.PLUGIN_ID}.reactivate_ai_on_close"
    config_repo.delete_prefix(key)

    cfg = logic.get_general_config()
    assert cfg["reactivate_ai_on_close"] is True          # default ON
    assert logic.get_reactivate_ai_on_close_setting() is True

    logic.set_general_config({"reactivate_ai_on_close": False})
    assert logic.get_general_config()["reactivate_ai_on_close"] is False
    assert logic.get_reactivate_ai_on_close_setting() is False  # a rota de fechar lê o mesmo

    # Payload legado (sem a chave) NÃO religa o valor já gravado.
    logic.set_general_config({"resolve_keep_assignee": True})
    assert logic.get_reactivate_ai_on_close_setting() is False
