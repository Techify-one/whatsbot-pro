"""``show_if`` no servidor (plano 176): o espelho de ``fieldVisible`` do formulário.

O servidor não confia no cliente. Se o formulário esconde os campos de um bloco desligado,
o servidor tem de concordar sobre o que é obrigatório e o que tem o formato validado —
senão (a) um canal nasce sem servidor nem senha, ou (b) o operador leva um 400 por um
campo que nem vê. A tabela de casos de ``field_visible`` é a MESMA de
``web/static/js/components/channels/showIf.test.js``.

Sem banco nem rede: o provider é um falso registrado num registry local.
"""

from __future__ import annotations

import types

import pytest

from app.services import channel_service as svc
from channels.registry import ChannelRegistry
from tests.fake_provider import FakeChannel

IMAP_ON = {"key": "imap_enabled", "equals": True}
DESCRIPTOR = {
    "label": "Mail",
    "config_fields": [
        {"key": "imap_enabled", "type": "bool", "default": True},
        {"key": "smtp_enabled", "type": "bool", "default": True},
    ],
    "credential_fields": [
        {"key": "address", "required": True},
        {"key": "imap_host", "required": True, "show_if": IMAP_ON},
        {"key": "imap_password", "type": "secret", "required": True, "show_if": IMAP_ON},
        {"key": "imap_port", "show_if": IMAP_ON, "pattern": r"\d{1,5}",
         "pattern_error": "porta inválida"},
        {"key": "smtp_host", "required": True,
         "show_if": {"key": "smtp_enabled", "equals": True}},
    ],
}


@pytest.fixture()
def deps():
    registry = ChannelRegistry()
    registry.register_provider(FakeChannel.configured(provider="mail", descriptor=DESCRIPTOR))
    return types.SimpleNamespace(channel_registry=registry)


# ── field_visible: a mesma tabela do cliente ────────────────────────────────

@pytest.mark.parametrize("field,config,creds,esperado", [
    ({"key": "a"}, {}, {}, True),
    (None, {}, {}, True),
    ({"key": "x", "show_if": IMAP_ON}, {"imap_enabled": True}, {}, True),
    ({"key": "x", "show_if": IMAP_ON}, {"imap_enabled": False}, {}, False),
    ({"key": "x", "show_if": IMAP_ON}, {"imap_enabled": "true"}, {}, False),   # tipado
    ({"key": "x", "show_if": IMAP_ON}, {"imap_enabled": 1}, {}, False),
    ({"key": "x", "show_if": IMAP_ON}, {}, {}, False),                          # ausente
    ({"key": "x", "show_if": {"key": "f"}}, {"f": True}, {}, True),             # sem equals
    ({"key": "x", "show_if": {"key": "f"}}, {"f": "sim"}, {}, False),
    ({"key": "x", "show_if": {"key": "m", "equals": "adv"}}, {"m": "adv"}, {}, True),
    ({"key": "x", "show_if": {"key": "k", "equals": "v"}}, {}, {"k": "v"}, True),
    ({"key": "x", "show_if": {"key": "k", "equals": "v"}}, {"k": "z"}, {"k": "v"}, False),
])
def test_field_visible_tabela_compartilhada_com_o_cliente(field, config, creds, esperado):
    assert svc.field_visible(field, config, creds) is esperado


@pytest.mark.parametrize("ruim", [{}, {"equals": True}, {"key": ""}, "texto", 5, [], None])
def test_show_if_malformado_e_fail_open(ruim):
    assert svc.field_visible({"key": "x", "show_if": ruim}, {}, {}) is True


# ── obrigatoriedade na criação ──────────────────────────────────────────────

def _required(deps, config):
    return svc.creation_required_credentials(deps, "mail", config)


def test_ligado_exige_servidor_e_senha(deps):
    cfg = {"imap_enabled": True, "smtp_enabled": True}
    assert _required(deps, cfg) == ["address", "imap_host", "imap_password", "smtp_host"]


def test_lado_desligado_sai_da_obrigatoriedade(deps):
    assert _required(deps, {"imap_enabled": False, "smtp_enabled": True}) == \
        ["address", "smtp_host"]
    assert _required(deps, {"imap_enabled": True, "smtp_enabled": False}) == \
        ["address", "imap_host", "imap_password"]
    assert _required(deps, {"imap_enabled": False, "smtp_enabled": False}) == ["address"]


def test_cliente_que_omite_o_checkbox_recebe_o_default_e_nao_um_canal_morto(deps):
    """O formulário sempre manda o checkbox, mas um cliente de API pode omiti-lo. Omitir
    NÃO pode ler como "desmarcado" (esconderia os campos e dispensaria servidor e senha)."""
    assert _required(deps, {}) == ["address", "imap_host", "imap_password", "smtp_host"]
    assert _required(deps, None) == ["address", "imap_host", "imap_password", "smtp_host"]
    assert _required(deps, {"smtp_enabled": False}) == \
        ["address", "imap_host", "imap_password"]


def test_sem_config_o_comportamento_e_o_de_sempre(deps):
    """Chamada antiga (sem o 3º argumento) continua valendo."""
    assert svc.creation_required_credentials(deps, "mail") == \
        ["address", "imap_host", "imap_password", "smtp_host"]


def test_provider_sem_show_if_nao_muda_em_nada():
    registry = ChannelRegistry()
    registry.register_provider(FakeChannel.configured(provider="plain", descriptor={
        "credential_fields": [{"key": "token", "required": True}, {"key": "x"}]}))
    d = types.SimpleNamespace(channel_registry=registry)
    for cfg in (None, {}, {"qualquer": False}):
        assert svc.creation_required_credentials(d, "plain", cfg) == ["token"]


# ── formato ─────────────────────────────────────────────────────────────────

def test_formato_de_campo_visivel_e_validado(deps):
    erros = svc.credential_format_errors(deps, "mail", {"imap_port": "abc"},
                                         {"imap_enabled": True})
    assert erros == {"imap_port": "porta inválida"}


def test_formato_de_campo_escondido_nao_e_validado(deps):
    """Valor velho de um bloco desligado não pode barrar o save com um erro sem saída."""
    assert svc.credential_format_errors(deps, "mail", {"imap_port": "abc"},
                                        {"imap_enabled": False}) == {}


def test_formato_com_config_omitido_usa_o_default_do_provider(deps):
    assert svc.credential_format_errors(deps, "mail", {"imap_port": "abc"}, {}) == \
        {"imap_port": "porta inválida"}
    assert svc.credential_format_errors(deps, "mail", {"imap_port": "abc"}) == \
        {"imap_port": "porta inválida"}


def test_mensagem_de_formato_nunca_cita_o_valor(deps):
    erros = svc.credential_format_errors(deps, "mail", {"imap_port": "SEGREDO-ABC"}, {})
    assert "SEGREDO" not in " ".join(erros.values())


# ── helpers ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    ('{"a": 1}', {"a": 1}), ({"a": 1}, {"a": 1}), (None, {}), ("", {}),
    ("{não é json", {}), ("[1, 2]", {}), (5, {}), ('"texto"', {}),
])
def test_config_as_dict_nunca_levanta(entrada, esperado):
    assert svc.config_as_dict(entrada) == esperado


def test_config_with_defaults_completa_so_o_que_falta():
    assert svc.config_with_defaults(DESCRIPTOR, {}) == {"imap_enabled": True,
                                                       "smtp_enabled": True}
    assert svc.config_with_defaults(DESCRIPTOR, {"imap_enabled": False}) == {
        "imap_enabled": False, "smtp_enabled": True}
    assert svc.config_with_defaults({}, {"x": 1}) == {"x": 1}
    assert svc.config_with_defaults(DESCRIPTOR, None) == {"imap_enabled": True,
                                                         "smtp_enabled": True}
