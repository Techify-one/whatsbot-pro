"""Reconcile do espelho de rótulos do plugin protocolos ↔ atributos de conversa do core.

``sync_core_atendimento_defs`` só CRIAVA definições. Rótulo apagado na aba "Resolver
atendimento" deixava a definição espelhada viva para sempre no core — invisível na tela
de Atributos personalizados (que esconde ``is_system=1``) e indeletável pela API (que
recusa apagar atributo de sistema), mas listada no seletor "Não enviar avaliação quando
o contato/conversa/protocolo tiver o atributo:". Daí as opções fantasma.

Agora o sync reconcilia nas três direções (criar / renomear / aposentar), com posse
registrada em ``plugin.protocolos.mirrored_attr_keys``.

    venv/bin/python -m pytest tests/test_protocolos_mirror_sync.py -q
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


def _defs(applies_to: str = "conversation") -> dict:
    """key → linha da definição, INCLUINDO as soft-deletadas (o que a UI não vê)."""
    from db.repositories import custom_attribute_repo as ca_repo
    return {r["attribute_key"]: r
            for r in ca_repo.list_definitions(applies_to=applies_to, include_deleted=True)}


def _visible(applies_to: str = "conversation") -> set:
    """Chaves que o seletor da aba Avaliação enxerga (é o mesmo GET que ele chama)."""
    from db.repositories import custom_attribute_repo as ca_repo
    return {r["attribute_key"] for r in ca_repo.list_definitions(applies_to=applies_to)}


def _label(key: str, label: str, ftype: str = "text") -> dict:
    return {"key": key, "label": label, "type": ftype, "options": [],
            "required": False, "multiple": False}


def test_rotulo_apagado_some_do_seletor(build_app):
    """Apagar o rótulo aposenta (soft delete) a definição espelhada — fim do fantasma."""
    build_app(["gowa", "protocolos"])
    logic = _logic()

    logic.set_field_defs("atendimento", [_label("resultado", "Resultado"),
                                         _label("fantasma", "Rótulo temporário")])
    assert {"resultado", "fantasma"} <= _visible()

    # O operador remove "fantasma" da aba Resolver atendimento.
    logic.set_field_defs("atendimento", [_label("resultado", "Resultado")])

    assert "fantasma" not in _visible(), "deveria sumir do seletor"
    assert "resultado" in _visible(), "o rótulo que ficou não pode ser afetado"
    # Soft delete: a LINHA continua no banco, com deleted_at preenchido.
    assert _defs()["fantasma"]["deleted_at"] is not None


def test_valores_ja_gravados_sobrevivem(build_app):
    """Aposentar a definição NÃO apaga o valor gravado na conversa (soft delete)."""
    build_app(["gowa", "protocolos"])
    logic = _logic()
    from db.engine import get_engine
    from db.repositories import (contact_repo, conversation_repo,
                                 custom_attribute_repo as ca_repo)
    from db.tables import conversations as conv_tbl
    from sqlalchemy import select

    logic.set_field_defs("atendimento", [_label("fantasma", "Rótulo temporário")])
    phone = "5511900000001"
    contact = contact_repo.get_or_create(phone, "Cliente Teste")
    conv_id = conversation_repo.resolve_for_contact(
        contact["id"], f"{phone}@s.whatsapp.net")["id"]
    ca_repo.set_values(conv_tbl, conv_id, {"fantasma": "valor importante"})

    logic.set_field_defs("atendimento", [])  # rótulo apagado

    with get_engine().connect() as conn:
        stored = conn.execute(
            select(conv_tbl.c.custom_attributes).where(conv_tbl.c.id == conv_id)
        ).scalar_one()
    assert (stored or {}).get("fantasma") == "valor importante"


def test_rotulo_renomeado_atualiza_o_nome(build_app):
    """Renomear o rótulo propaga o display_name (antes ficava congelado na criação)."""
    build_app(["gowa", "protocolos"])
    logic = _logic()

    logic.set_field_defs("atendimento", [_label("motivo", "motivo")])
    assert _defs()["motivo"]["display_name"] == "motivo"

    logic.set_field_defs("atendimento", [_label("motivo", "Motivo (Atendimento)")])
    assert _defs()["motivo"]["display_name"] == "Motivo (Atendimento)"


def test_rotulo_recriado_e_restaurado(build_app):
    """Rótulo que volta reativa a MESMA definição — a criação seria no-op (chave ocupada
    pela linha soft-deletada) e o espelho ficaria invisível para sempre."""
    build_app(["gowa", "protocolos"])
    logic = _logic()

    logic.set_field_defs("atendimento", [_label("volta", "Vai e volta")])
    def_id = _defs()["volta"]["id"]
    logic.set_field_defs("atendimento", [])
    assert "volta" not in _visible()

    logic.set_field_defs("atendimento", [_label("volta", "Vai e volta")])
    assert "volta" in _visible()
    assert _defs()["volta"]["id"] == def_id, "restaura a linha, não cria outra"


def test_nao_apaga_definicao_de_outro_dono(build_app):
    """Posse por registro: atributo de sistema que não é nosso fica intacto."""
    build_app(["gowa", "protocolos"])
    logic = _logic()
    from db.repositories import custom_attribute_repo as ca_repo

    logic.set_field_defs("atendimento", [_label("resultado", "Resultado")])  # cria o registro
    ca_repo.ensure_system_definition(attribute_key="de_outro_plugin",
                                     display_name="De outro plugin",
                                     applies_to="conversation")

    logic.sync_core_atendimento_defs()

    assert "de_outro_plugin" in _visible(), "só aposentamos o que nós espelhamos"


def test_atributo_do_usuario_nao_e_sequestrado(build_app):
    """Rótulo cuja chave já é de um atributo criado pelo usuário (is_system=0) não vira
    espelho — a definição dele continua dele, e não é aposentada por nós."""
    build_app(["gowa", "protocolos"])
    logic = _logic()
    from db.repositories import custom_attribute_repo as ca_repo

    ca_repo.create_definition(attribute_key="do_usuario", display_name="Do usuário",
                              type="text", applies_to="conversation")

    logic.set_field_defs("atendimento", [_label("do_usuario", "Colisão")])

    row = _defs()["do_usuario"]
    assert not row["is_system"], "a definição do usuário não foi convertida em sistema"
    assert row["display_name"] == "Do usuário", "nem renomeada pelo rótulo do plugin"

    logic.set_field_defs("atendimento", [])
    assert "do_usuario" in _visible(), "nem aposentada"
