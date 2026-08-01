"""Condicionais das regras "não enviar avaliação" (aba Avaliação do plugin protocolos).

Cada linha da regra é UM atributo com uma ou mais condições ``{op, value}``, combinadas
por ``join`` (``any`` = OU, ``all`` = E). Entre linhas continua sendo OU.

Cobre o saneamento (forma + compatibilidade com a config antiga de um único ``value``)
e a avaliação (cada operador, a combinação OU/E e o que é inerte).

    venv/bin/python -m pytest tests/test_protocolos_skip_conditions.py -q
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# Exercita a cópia INSTALADA (real) em ``storages/plugins/protocolos``.
_STORAGES_PLUGINS = Path(__file__).resolve().parents[1] / "storages" / "plugins"
pytestmark = pytest.mark.skipif(
    not (_STORAGES_PLUGINS / "protocolos" / "plugin.yaml").is_file(),
    reason="plugin protocolos não instalado em storages/plugins "
           "(fonte intencional deste módulo)",
)


@pytest.fixture(autouse=True)
def _load_from_storages(monkeypatch):
    monkeypatch.setattr("tests.support.REAL_PLUGIN_EXAMPLES", _STORAGES_PLUGINS)


def _logic():
    return importlib.import_module("whatsbot_plugins.protocolos.logic")


def _rule(*conds, join="any", key="attr", scope="contact"):
    return {"key": key, "scope": scope, "join": join, "conditions": list(conds)}


# ── Avaliação de uma linha ────────────────────────────────────────────────────

def test_ou_casa_com_qualquer_condicao(build_app):
    build_app(["gowa", "protocolos"])
    m = _logic()._rule_matches
    r = _rule({"op": "eq", "value": "teste"}, {"op": "eq", "value": "outro"}, join="any")

    assert m("outro", r) is True
    assert m("teste", r) is True
    assert m("terceiro", r) is False


def test_e_exige_todas_as_condicoes(build_app):
    build_app(["gowa", "protocolos"])
    m = _logic()._rule_matches
    r = _rule({"op": "contains", "value": "sup"},
              {"op": "not_contains", "value": "comercial"}, join="all")

    assert m("Suporte", r) is True
    assert m(["Suporte", "Comercial"], r) is False   # a 2ª condição derruba
    assert m("Comercial", r) is False                # a 1ª condição derruba


def test_operadores(build_app):
    build_app(["gowa", "protocolos"])
    m = _logic()._rule_matches

    assert m("Teste", _rule({"op": "eq", "value": "teste"})) is True      # case-insensitive
    assert m(" teste ", _rule({"op": "eq", "value": "teste"})) is True    # trim
    assert m("outro", _rule({"op": "neq", "value": "teste"})) is True
    assert m("teste", _rule({"op": "neq", "value": "teste"})) is False
    assert m("", _rule({"op": "neq", "value": "teste"})) is True          # ausente ≠ valor
    assert m("abcdef", _rule({"op": "contains", "value": "cde"})) is True
    assert m("abcdef", _rule({"op": "not_contains", "value": "xyz"})) is True
    assert m("qualquer", _rule({"op": "filled"})) is True
    assert m("", _rule({"op": "filled"})) is False
    assert m(None, _rule({"op": "empty"})) is True
    assert m("  ", _rule({"op": "empty"})) is True
    assert m("x", _rule({"op": "empty"})) is False


def test_valor_multiplo_lista_e_string(build_app):
    """checkboxes/atributo de lista chegam como lista; o espelho do plugin chega como
    string separada por vírgula. Os dois casam item a item."""
    build_app(["gowa", "protocolos"])
    m = _logic()._rule_matches

    assert m(["Suporte", "Comercial"], _rule({"op": "eq", "value": "comercial"})) is True
    assert m("Suporte, Comercial", _rule({"op": "eq", "value": "comercial"})) is True
    assert m("Suporte, Comercial", _rule({"op": "eq", "value": "financeiro"})) is False


def test_condicao_sem_valor_e_inerte(build_app):
    """Operador que exige valor e está em branco não filtra — nem casa sozinho, nem
    derruba o modo E (senão a linha travaria enquanto o operador digita)."""
    build_app(["gowa", "protocolos"])
    m = _logic()._rule_matches

    assert m("qualquer", _rule({"op": "eq", "value": ""})) is False
    assert m("teste", _rule({"op": "eq", "value": "teste"},
                            {"op": "contains", "value": ""}, join="all")) is True
    assert m("teste", _rule(join="all")) is False  # linha sem condição nenhuma


def test_regra_legada_sem_conditions(build_app):
    """Config gravada antes das condicionais (só ``value``) continua valendo."""
    build_app(["gowa", "protocolos"])
    m = _logic()._rule_matches

    assert m("teste", {"key": "a", "scope": "contact", "value": "teste"}) is True
    assert m("outro", {"key": "a", "scope": "contact", "value": "teste"}) is False


# ── Saneamento / persistência ─────────────────────────────────────────────────

def test_sanitize_converte_formato_antigo(build_app):
    build_app(["gowa", "protocolos"])
    out = _logic()._sanitize_skip_attrs([{"key": "email", "scope": "contact", "value": "x"}])

    assert out == [{"key": "email", "scope": "contact", "join": "any",
                    "conditions": [{"op": "eq", "value": "x"}], "value": "x"}]


def test_sanitize_espelha_value_apenas_no_eq_simples(build_app):
    """O espelho legado ``value`` só é gravado quando a regra é UMA igualdade — com
    outro operador ele é omitido, para uma versão antiga tratar a regra como inerte
    em vez de interpretá-la como igualdade."""
    build_app(["gowa", "protocolos"])
    san = _logic()._sanitize_skip_attrs

    assert "value" not in san([_rule({"op": "contains", "value": "x"})])[0]
    assert "value" not in san([_rule({"op": "eq", "value": "a"},
                                     {"op": "eq", "value": "b"})])[0]
    assert san([_rule({"op": "eq", "value": "a"})])[0]["value"] == "a"


def test_sanitize_descarta_invalidos_mas_mantem_linha_em_branco(build_app):
    build_app(["gowa", "protocolos"])
    san = _logic()._sanitize_skip_attrs

    assert san([{"key": "", "scope": "contact"}]) == []          # sem chave
    assert san([{"key": "a", "scope": "inventado"}]) == []       # escopo inválido
    assert san([_rule({"op": "hackzor", "value": "x"})])[0]["conditions"] == []
    # Linha ainda em branco sobrevive ao salvar (a UI mantém a linha na tela).
    kept = san([_rule({"op": "eq", "value": ""})])
    assert len(kept) == 1 and kept[0]["conditions"] == [{"op": "eq", "value": ""}]


def test_round_trip_pela_config(build_app):
    """set/get da config preserva as condições (é o que o PUT /protocol-config faz)."""
    build_app(["gowa", "protocolos"])
    logic = _logic()
    logic.set_protocol_config({
        "enabled": True, "normal": {}, "privado": {},
        "skip_attrs": [_rule({"op": "neq", "value": "vip"},
                             {"op": "filled"}, join="all", key="plano")],
    })
    rules = logic.get_protocol_config()["skip_attrs"]

    assert rules == [{"key": "plano", "scope": "contact", "join": "all",
                      "conditions": [{"op": "neq", "value": "vip"},
                                     {"op": "filled", "value": ""}]}]


def test_join_invalido_cai_em_ou(build_app):
    build_app(["gowa", "protocolos"])
    out = _logic()._sanitize_skip_attrs([_rule({"op": "eq", "value": "a"}, join="xor")])
    assert out[0]["join"] == "any"
