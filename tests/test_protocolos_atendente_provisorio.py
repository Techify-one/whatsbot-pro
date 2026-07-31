"""Atendente PROVISÓRIO do plugin ``protocolos`` (1.24.0, migração 019).

"Atribuir a CONVERSA a um atendente carimba temporariamente o protocolo e o ciclo abertos
com ele" — para o Kanban e o filtro "Atendente" acharem os protocolos ainda ABERTOS que
ninguém salvou com um atendente, mas que já estão de fato sendo atendidos.

O vínculo é INVISÍVEL na interface desde a 1.24.1 (sem marcador "provisório" e sem o
filtro "Vínculo do atendente"); o mecanismo abaixo continua idêntico.

Cobre a política inteira:

* CARIMBAR — ``conversation.assigned``, e também os dois caminhos do core que atribuem
  SEM emitir evento de atribuição (o atendente padrão do canal, carimbado no nascimento/
  reabertura da conversa, e a tool ``transfer_to_human``);
* LIMPAR — desatribuir, ``assign_unified(kind="none")`` e a IA assumindo — todos chegam
  como ``assignee_user_id=None``. Fechar a conversa NÃO limpa (o core limpa o assignee
  sem emitir atribuição, e o último atendente fica registrado);
* NÃO VAZAR — o provisório não entra em ``_effective_values``/``_missing_required`` nem
  semeia o formulário: o rótulo obrigatório "Atendente" continua bloqueando;
* LER — atendente EFETIVO no agrupamento do Kanban e no filtro nativo "Atendente";
* CUSTO — o carimbo é idempotente e não invalida o índice do Kanban à toa.

Aponta para a cópia INSTALADA em ``storages/plugins/protocolos`` (monkeypatch
``REAL_PLUGIN_EXAMPLES``), como os demais testes do plugin.

    venv/bin/python -m pytest tests/test_protocolos_atendente_provisorio.py -q
"""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from db.engine import get_engine
from db.repositories import (contact_inbox_repo, contact_repo, conversation_repo,
                             user_repo)

_STORAGES_PLUGINS = Path(__file__).resolve().parents[1] / "storages" / "plugins"
INBOX_ID = 1


@pytest.fixture(autouse=True)
def _load_from_storages(monkeypatch):
    monkeypatch.setattr("tests.support.REAL_PLUGIN_EXAMPLES", _STORAGES_PLUGINS)


@pytest.fixture
def logic(build_app):
    build_app(["gowa", "protocolos"])
    return importlib.import_module("whatsbot_plugins.protocolos.logic")


@pytest.fixture
def grouping(logic):
    return importlib.import_module("whatsbot_plugins.protocolos.grouping")


@pytest.fixture
def kanban_index(logic):
    return importlib.import_module("whatsbot_plugins.protocolos.kanban_index")


# ── Seeds ─────────────────────────────────────────────────────────────────────

def _user(name: str) -> dict:
    """Usuário REAL do core (o snapshot do nome sai daqui)."""
    email = f"prov-{uuid.uuid4().hex[:10]}@teste.local"
    return user_repo.create(email=email, name=name, password_hash="x")


def _conversation(*, assignee_user_id=None, status="open") -> dict:
    """Conversa REAL do core (telefone único — o banco é global ao processo)."""
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    if assignee_user_id is not None:
        conv = conversation_repo.set_assignee(conv["id"], assignee_user_id)
    if status != "open":
        conv = conversation_repo.set_status(conv["id"], status, clear_assignee=False)
    return conv


def _protocolo_com_ciclo(logic, conv) -> tuple[dict, dict]:
    at = logic.ensure_protocolo_for_contact(conv["contact_id"], conversation_id=conv["id"])
    cyc = logic.ensure_open_cycle(conv["id"], conv["contact_id"], at["id"])
    return at, cyc


def _prov(logic, at_id: int):
    return logic.get_protocolo(at_id).get("provisional_assignee_user_id")


def _prov_cycle(conv_id: int):
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT provisional_assignee_user_id AS uid, provisional_assignee_name AS nome "
                 "FROM plugin_protocolos_atendimentos WHERE conversation_id = :cv "
                 "ORDER BY id DESC LIMIT 1"), {"cv": conv_id}).mappings().first()
    return dict(row) if row else None


def _required_values(logic, scope: str) -> dict:
    """Preenche os rótulos OBRIGATÓRIOS do escopo (o plugin já vem com alguns semeados).
    O "atendente" fica de fora: é o campo que os testes querem ver bloqueando."""
    out = {}
    for d in logic.get_field_defs(scope):
        if not d.get("required") or d.get("type") == "atendente":
            continue
        if d.get("type") == "checkbox":
            out[d["key"]] = True
        elif logic._is_multi(d) if hasattr(logic, "_is_multi") else d.get("type") == "checkboxes":
            out[d["key"]] = [(d.get("options") or ["x"])[0]]
        elif d.get("options"):
            out[d["key"]] = d["options"][0]
        elif d.get("type") == "number":
            out[d["key"]] = 1
        else:
            out[d["key"]] = "ok"
    return out


def _resolve(logic, conv, user) -> None:
    """Resolve o ciclo aberto da conversa preenchendo os obrigatórios do escopo."""
    _, err = logic.resolve_atendimento(conv["id"], _required_values(logic, "atendimento"),
                                       assignee_user_id=user["id"],
                                       assignee_name=user["name"])
    assert err is None, err


def _finaliza(logic, conv, at, user) -> None:
    """Fecha o protocolo de verdade: resolver o ciclo → fechar a conversa (os dois guards
    de ``close_protocolo``) → finalizar."""
    _resolve(logic, conv, user)
    conversation_repo.set_status(conv["id"], "closed", clear_assignee=False)
    # A UI salva os campos ANTES de finalizar — incluindo o Atendente, que o gate exige.
    logic.update_protocolo_fields(
        at["id"], {**_required_values(logic, "protocolo"), "atendente": user["id"]})
    _, err = logic.close_protocolo(at["id"], assignee_user_id=user["id"],
                                   assignee_name=user["name"])
    assert err is None, err


def _assigned_payload(conv, **extra) -> dict:
    """Mesmo shape que ``conversation_service._broadcast`` entrega ao bus."""
    return {"conversation_id": conv["id"], "contact_id": conv["contact_id"],
            "status": conv.get("status"), "assignee_user_id": conv.get("assignee_user_id"),
            "active_agent_key": conv.get("active_agent_key"),
            "ai_active": conv.get("ai_active"), "inbox_id": conv.get("inbox_id"), **extra}


# ── Carimbar ──────────────────────────────────────────────────────────────────

def test_atribuir_a_conversa_carimba_protocolo_e_ciclo(logic):
    u = _user("Ana Provisória")
    conv = _conversation()
    at, _ = _protocolo_com_ciclo(logic, conv)
    assert _prov(logic, at["id"]) is None            # nasce sem provisório

    conv = conversation_repo.set_assignee(conv["id"], u["id"])
    logic.on_conversation_assigned(None, _assigned_payload(conv))

    assert _prov(logic, at["id"]) == u["id"]
    assert _prov_cycle(conv["id"]) == {"uid": u["id"], "nome": "Ana Provisória"}
    # E o DEFINITIVO continua intocado — é isso que mantém o campo obrigatório valendo.
    assert logic.get_protocolo(at["id"])["assignee_user_id"] is None


def test_transferencia_entre_humanos_troca_o_provisorio(logic):
    a, b = _user("Ana"), _user("Bia")
    conv = _conversation()
    at, _ = _protocolo_com_ciclo(logic, conv)

    conv = conversation_repo.set_assignee(conv["id"], a["id"])
    logic.on_conversation_assigned(None, _assigned_payload(conv))
    conv = conversation_repo.set_assignee(conv["id"], b["id"])
    logic.on_conversation_assigned(None, _assigned_payload(conv, previous_assignee=a["id"]))

    assert _prov(logic, at["id"]) == b["id"]
    assert _prov_cycle(conv["id"])["nome"] == "Bia"


def test_protocolo_fechado_nunca_e_recarimbado(logic):
    u = _user("Ana")
    conv = _conversation()
    at, _ = _protocolo_com_ciclo(logic, conv)
    _finaliza(logic, conv, at, u)
    assert logic.get_protocolo(at["id"])["status"] == "fechado"

    congelado = _prov(logic, at["id"])   # o que valia quando o protocolo ainda estava aberto

    outro = _user("Bia")
    conv = conversation_repo.set_assignee(conv["id"], outro["id"])
    logic.on_conversation_assigned(None, _assigned_payload(conv))

    # Histórico congelado: o provisório de um protocolo FECHADO não é reescrito.
    assert _prov(logic, at["id"]) == congelado
    assert _prov(logic, at["id"]) != outro["id"]


# ── Limpar ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("evento", ["assigned", "unassigned"])
def test_desatribuir_limpa_o_provisorio(logic, evento):
    """``/assign`` com corpo nulo emite ``conversation.unassigned``; o
    ``assign_unified(kind="none")`` emite ``conversation.assigned``. Mesmo payload, mesmo
    handler — o discriminante é ``assignee_user_id=None``."""
    u = _user("Ana")
    conv = _conversation(assignee_user_id=u["id"])
    at, _ = _protocolo_com_ciclo(logic, conv)
    logic.on_conversation_assigned(None, _assigned_payload(conv))
    assert _prov(logic, at["id"]) == u["id"]

    conv = conversation_repo.set_assignee(conv["id"], None)
    logic.on_conversation_assigned(None, _assigned_payload(conv, previous_assignee=u["id"]))

    assert _prov(logic, at["id"]) is None
    assert _prov_cycle(conv["id"]) == {"uid": None, "nome": ""}


def test_ia_assumindo_limpa_o_provisorio_e_cancela_o_hold(logic):
    """Regressão da 1.22.0 junto: o efeito do hold e o do provisório são independentes."""
    u = _user("Ana")
    conv = _conversation(assignee_user_id=u["id"])
    at, _ = _protocolo_com_ciclo(logic, conv)
    logic.on_conversation_assigned(None, _assigned_payload(conv))
    logic._write_ai_hold(conv["id"], hold_until=logic.now() + 600, mode="owner",
                         owner_user_id=u["id"], protocolo_id=at["id"], reason="teste")

    conv = conversation_repo.set_assignee(conv["id"], None)
    logic.on_conversation_assigned(
        None, _assigned_payload(conv, active_agent_key="vendas"))

    assert _prov(logic, at["id"]) is None
    assert logic.get_ai_hold(conv["id"]) is None


def test_fechar_a_conversa_NAO_limpa_o_provisorio(logic):
    """O core limpa o assignee ao fechar, mas sem emitir atribuição — de propósito: o
    último atendente continua registrado no protocolo, que segue aberto."""
    u = _user("Ana")
    conv = _conversation(assignee_user_id=u["id"])
    at, _ = _protocolo_com_ciclo(logic, conv)
    logic.on_conversation_assigned(None, _assigned_payload(conv))

    logic.on_conversation_status(None, {"conversation_id": conv["id"], "status": "closed"})

    assert _prov(logic, at["id"]) == u["id"]


# ── Caminhos SEM evento de atribuição ────────────────────────────────────────

def test_atendente_padrao_do_canal_no_nascimento(logic):
    """A conversa nasce já atribuída (``resolve_for_contact_ex``) e o core não emite nada:
    quem sincroniza é o ``on_inbound``."""
    u = _user("Ana")
    conv = _conversation(assignee_user_id=u["id"])
    contact = contact_repo.get(conv["contact_id"])

    logic.on_inbound(None, {"phone": contact["phone"], "text": "oi"})

    at = logic.get_open_protocolo_for_contact(conv["contact_id"])
    assert at is not None and at["provisional_assignee_user_id"] == u["id"]
    assert _prov_cycle(conv["id"])["uid"] == u["id"]


def test_reabertura_recarimba_ciclo_ja_existente(logic):
    """Na reabertura o core re-carimba o dono, mas o ciclo aberto NÃO é recriado — por
    isso a sincronização compara em vez de olhar só a criação."""
    u = _user("Ana")
    conv = _conversation()
    at, _ = _protocolo_com_ciclo(logic, conv)
    contact = contact_repo.get(conv["contact_id"])

    conversation_repo.set_assignee(conv["id"], u["id"])   # sem evento nenhum
    logic.on_inbound(None, {"phone": contact["phone"], "text": "voltei"})

    assert _prov(logic, at["id"]) == u["id"]
    assert _prov_cycle(conv["id"])["uid"] == u["id"]


def test_transfer_to_human_le_a_conversa(logic):
    """O payload de ``conversation.transferred_to_human`` não carrega o assignee."""
    u = _user("Ana")
    conv = _conversation()
    at, _ = _protocolo_com_ciclo(logic, conv)

    # (a) default da tool: desatribui → nada a carimbar.
    logic.on_conversation_transferred_to_human(None, {"conversation_id": conv["id"]})
    assert _prov(logic, at["id"]) is None

    # (b) o filtro `filter.conversation.assignment` redirecionou a um humano.
    conversation_repo.set_assignee(conv["id"], u["id"])
    logic.on_conversation_transferred_to_human(None, {"conversation_id": conv["id"]})
    assert _prov(logic, at["id"]) == u["id"]


def test_plugin_escrevendo_no_core_mantem_o_espelho(logic):
    """``assign_protocolo`` escreve o assignee do core direto no repo (sem evento). Sem
    sincronizar aqui, um card arrastado para "Não atribuído" voltaria para a coluna do
    provisório antigo."""
    u = _user("Ana")
    conv = _conversation(assignee_user_id=u["id"])
    at, _ = _protocolo_com_ciclo(logic, conv)
    logic.on_conversation_assigned(None, _assigned_payload(conv))

    logic.assign_protocolo(at["id"], None)

    assert _prov(logic, at["id"]) is None
    assert conversation_repo.get(conv["id"])["assignee_user_id"] is None


# ── Não vazar para o formulário ───────────────────────────────────────────────

def test_provisorio_nao_satisfaz_o_campo_obrigatorio(logic):
    u = _user("Ana")
    conv = _conversation(assignee_user_id=u["id"])
    at, _ = _protocolo_com_ciclo(logic, conv)
    logic.on_conversation_assigned(None, _assigned_payload(conv))

    at = logic.get_protocolo(at["id"])
    eff = logic._effective_values("protocolo", at)
    assert eff.get("atendente") is None                      # o provisório não entra
    assert logic._missing_required("protocolo", eff)          # segue bloqueando o fechar

    # E o gate de fechamento REALMENTE barra: resolvido o ciclo e fechada a conversa (os
    # dois guards anteriores), o que sobra é o obrigatório "Atendente" não preenchido.
    _resolve(logic, conv, u)
    conversation_repo.set_status(conv["id"], "closed", clear_assignee=False)
    logic.update_protocolo_fields(at["id"], _required_values(logic, "protocolo"))
    ok, err = logic.close_protocolo(at["id"])
    assert ok is None and "Atendente" in (err or "")


# ── Leitura: agrupamento e filtros ────────────────────────────────────────────

def test_agrupamento_usa_o_atendente_efetivo(grouping):
    g = grouping.build_grouping({"group_by": "atendente"}, users=[{"id": 7, "name": "Ana"}])
    assert g.column_id_of({"assignee_user_id": 7}) == "u:7"
    assert g.column_id_of({"assignee_user_id": None,
                           "provisional_assignee_user_id": 7}) == "u:7"
    # Definitivo ganha do provisório.
    assert g.column_id_of({"assignee_user_id": 3,
                           "provisional_assignee_user_id": 7}) == "u:3"
    assert g.column_id_of({"assignee_user_id": None,
                           "provisional_assignee_user_id": None}) == "__none__"


def test_filtro_nativo_atendente_acha_o_provisorio(logic):
    u = _user("Ana")
    conv = _conversation(assignee_user_id=u["id"])
    at, _ = _protocolo_com_ciclo(logic, conv)
    logic.on_conversation_assigned(None, _assigned_payload(conv))

    env = logic.list_protocolos(assignee_user_id=[u["id"]], limit=200)
    assert any(p["id"] == at["id"] for p in env["items"])
    # Ficou no SQL: total do envelope = contagem real (não caiu no scan-cap).
    assert env["total"] == len(env["items"]) or env["has_more"]


# ── Custo ─────────────────────────────────────────────────────────────────────

def test_carimbo_idempotente_nao_invalida_o_kanban(logic, kanban_index):
    u = _user("Ana")
    conv = _conversation(assignee_user_id=u["id"])
    _protocolo_com_ciclo(logic, conv)

    assert logic.stamp_provisional_assignee(conv["id"], u["id"]) is True
    gen = kanban_index.generation()
    assert logic.stamp_provisional_assignee(conv["id"], u["id"]) is False
    assert kanban_index.generation() == gen      # nada mudou → cache preservado


def test_stamp_tolera_entrada_invalida(logic):
    assert logic.stamp_provisional_assignee(None, 1) is False
    assert logic.stamp_provisional_assignee("abc", 1) is False
    assert logic.stamp_provisional_assignee(999999999, "nao-numero") is False
