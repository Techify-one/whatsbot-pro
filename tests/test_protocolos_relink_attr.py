"""Continuidade de protocolo decidida por ATRIBUTO PERSONALIZADO (plugin protocolos).

O popup "faz parte do protocolo anterior?" deixou de depender só do clique do atendente:
a escolha GRAVA um valor num atributo personalizado do core e um valor já presente DECIDE
sozinho o próximo re-engajamento (vincula ao anterior / abre novo / não abre nada).

Cobre config (round-trip + sanitização), leitura da decisão nos 2 escopos, aplicação
automática em ``on_inbound``, consumo do valor, bloqueio (protocolo + reabertura) e a
gravação pelas 3 rotas do popup. Aponta para o código INSTALADO em
``storages/plugins/protocolos`` (mesmo monkeypatch de ``test_protocolos_popup``).

    venv/bin/python -m pytest tests/test_protocolos_relink_attr.py -q
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from db.engine import get_engine

_STORAGES_PLUGINS = Path(__file__).resolve().parents[1] / "storages" / "plugins"


@pytest.fixture(autouse=True)
def _load_from_storages(monkeypatch):
    monkeypatch.setattr("tests.support.REAL_PLUGIN_EXAMPLES", _STORAGES_PLUGINS)


def _logic():
    return importlib.import_module("whatsbot_plugins.protocolos.logic")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_protocolo(contact_id: int, *, status: str = "fechado",
                    closed_at: float | None = None) -> int:
    ts = time.time()
    with get_engine().begin() as conn:
        pid = conn.execute(text(
            "INSERT INTO plugin_protocolos_protocolos "
            "(contact_id, contact_phone, contact_name, status, assignee_name, fields, "
            " opened_at, closed_at, created_at, updated_at) "
            "VALUES (:cid, '5511', 'Cliente', :st, 'Ana', '{}', :ts, :cl, :ts, :ts) "
            "RETURNING id"),
            {"cid": contact_id, "st": status, "cl": closed_at, "ts": ts}).scalar()
    return int(pid)


def _seed_ciclo(protocolo_id: int, conversation_id: int, contact_id: int) -> int:
    ts = time.time()
    with get_engine().begin() as conn:
        cid = conn.execute(text(
            "INSERT INTO plugin_protocolos_atendimentos "
            "(protocolo_id, conversation_id, contact_id, assignee_name, fields, "
            " started_at, created_at, updated_at) "
            "VALUES (:pid, :conv, :contact, '', '{}', :ts, :ts, :ts) RETURNING id"),
            {"pid": protocolo_id, "conv": conversation_id, "contact": contact_id,
             "ts": ts}).scalar()
    return int(cid)


def _contact_and_conversation(phone: str):
    """Contato + conversa REAIS do core (o atributo vive em ``custom_attributes`` deles)."""
    from db.repositories import contact_repo, conversation_repo
    contact = contact_repo.get_or_create(phone)
    conv = conversation_repo.resolve_for_contact(contact["id"], f"{phone}@s.whatsapp.net")
    return contact, conv


def _configure(plogic, *, scope="contact", key="continuidade", consume=True,
               previous="continuacao", new="novo", block="nao_abrir", enabled=True):
    return plogic.set_relink_attr_config({
        "enabled": enabled, "scope": scope, "key": key, "consume": consume,
        "values": {"previous": previous, "new": new, "block": block},
    })


def _set_attr(scope, entity_id, key, value):
    from db.repositories import custom_attribute_repo
    from db.tables import contacts, conversations
    tbl = contacts if scope == "contact" else conversations
    custom_attribute_repo.set_values(tbl, entity_id, {key: value})


def _get_attr(scope, entity_id, key):
    from db.repositories import custom_attribute_repo
    from db.tables import contacts, conversations
    tbl = contacts if scope == "contact" else conversations
    return (custom_attribute_repo.get_values(tbl, entity_id) or {}).get(key)


def _cycles_of(protocolo_id: int) -> list[dict]:
    """Atendimentos-ciclo de um protocolo, do mais antigo ao mais novo."""
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT * FROM plugin_protocolos_atendimentos WHERE protocolo_id = :p "
            "ORDER BY started_at, id"), {"p": protocolo_id}).mappings().all()
    return [dict(r) for r in rows]


def _count_open(contact_id: int) -> int:
    with get_engine().connect() as conn:
        return int(conn.execute(text(
            "SELECT COUNT(*) FROM plugin_protocolos_protocolos "
            "WHERE contact_id = :c AND status = 'aberto'"), {"c": contact_id}).scalar())


@pytest.fixture
def plugin_logic(build_app):
    """App montado com o plugin + config da regra restaurada ao fim de cada teste."""
    build_app(["gowa", "protocolos"])
    plogic = _logic()
    try:
        yield plogic
    finally:
        _configure(plogic, enabled=False, key="", previous="", new="", block="")


# ── Config ────────────────────────────────────────────────────────────────────

def test_config_round_trip_and_sanitize(plugin_logic):
    plogic = plugin_logic
    saved = _configure(plogic, scope="conversation", key="continuidade", consume=False)
    assert saved["enabled"] is True and saved["scope"] == "conversation"
    assert saved["values"] == {"previous": "continuacao", "new": "novo", "block": "nao_abrir"}
    assert saved["consume"] is False
    # get_general_config carrega o sub-objeto (é o que a tela de config lê).
    assert plogic.get_general_config()["relink_attr"]["key"] == "continuidade"

    # Escopo inválido cai em 'contact'; sem chave a regra NÃO fica ligada.
    bad = plogic.set_relink_attr_config({"enabled": True, "scope": "xpto", "key": "  ",
                                         "values": {"previous": "a"}})
    assert bad["scope"] == "contact" and bad["enabled"] is False


def test_disabled_by_default_keeps_manual_flow(plugin_logic):
    """Sem a regra ligada nada muda: a sugestão continua sendo a do popup manual."""
    plogic = plugin_logic
    contact, conv = _contact_and_conversation("5511970000001")
    _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 120)
    _set_attr("contact", contact["id"], "continuidade", "continuacao")

    assert plogic.read_relink_attr_decision(contact["id"], conv["id"]) is None
    data = plogic.relink_suggestion_for_contact(contact["id"], conv["id"])
    assert data["attr_decision"] is None
    assert data["suggest"] is True


# ── Leitura da decisão ────────────────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["contact", "conversation"])
def test_read_decision_in_both_scopes(plugin_logic, scope):
    plogic = plugin_logic
    contact, conv = _contact_and_conversation(f"55119700100{0 if scope == 'contact' else 1}")
    _configure(plogic, scope=scope)
    entity_id = contact["id"] if scope == "contact" else conv["id"]

    assert plogic.read_relink_attr_decision(contact["id"], conv["id"]) is None
    _set_attr(scope, entity_id, "continuidade", "novo")
    assert plogic.read_relink_attr_decision(contact["id"], conv["id"]) == "new"
    _set_attr(scope, entity_id, "continuidade", "CONTINUACAO")   # case-insensitive
    assert plogic.read_relink_attr_decision(contact["id"], conv["id"]) == "previous"
    _set_attr(scope, entity_id, "continuidade", "valor_qualquer") # sem mapeamento → None
    assert plogic.read_relink_attr_decision(contact["id"], conv["id"]) is None


def test_decision_suppresses_popup(plugin_logic):
    plogic = plugin_logic
    contact, conv = _contact_and_conversation("5511970002000")
    _configure(plogic)
    _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 120)
    assert plogic.relink_suggestion_for_contact(contact["id"], conv["id"])["suggest"] is True

    _set_attr("contact", contact["id"], "continuidade", "continuacao")
    data = plogic.relink_suggestion_for_contact(contact["id"], conv["id"])
    assert data["attr_decision"] == "previous"
    assert data["suggest"] is False


# ── Aplicação automática ──────────────────────────────────────────────────────

def _inbound(plogic, phone):
    plogic.on_inbound(None, {"phone": phone, "text": "oi"})


def test_inbound_always_opens_protocol_inside_window(plugin_logic):
    """A mensagem do cliente SEMPRE abre um protocolo novo, mesmo dentro da janela e com
    uma decisão gravada — a continuidade é decidida depois, ao resolver (o valor fica)."""
    plogic = plugin_logic
    phone = "5511970003000"
    contact, _conv = _contact_and_conversation(phone)
    _configure(plogic)
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 120)
    _set_attr("contact", contact["id"], "continuidade", "continuacao")

    _inbound(plogic, phone)

    assert _count_open(contact["id"]) == 1
    assert plogic.get_protocolo(prev)["status"] == "fechado"     # o anterior segue fechado
    assert _get_attr("contact", contact["id"], "continuidade") == "continuacao"


def test_operator_message_reopens_cycle_and_blocks_closing_protocol(plugin_logic):
    """Atendente resolve, manda mensagem (a conversa reabre) e tenta finalizar o protocolo.

    O envio numa conversa REABERTA tem de abrir um ciclo novo — senão o protocolo ficava
    sem atendimento aberto e dava para finalizá-lo com o atendimento em curso."""
    from db.repositories import conversation_repo
    plogic = plugin_logic
    phone = "5511970010000"
    contact, conv = _contact_and_conversation(phone)
    _inbound(plogic, phone)                                   # protocolo + ciclo abertos
    proto = plogic._select_open_protocolo(contact["id"])
    with get_engine().begin() as c:                           # resolve o atendimento
        c.execute(text("UPDATE plugin_protocolos_atendimentos SET ended_at = :ts "
                       "WHERE conversation_id = :c"), {"ts": time.time(), "c": conv["id"]})
    conversation_repo.set_status(conv["id"], "closed")
    # Resolvido + conversa fechada: o Finalizar não é mais barrado por atendimento aberto
    # (pode ser barrado por rótulo obrigatório do protocolo — outro gate, não este).
    assert "resolva-o antes de finalizar" not in (plogic.close_protocolo(proto["id"])[1] or "")

    # O atendente envia uma mensagem: o core reabre a conversa ANTES do message.sent.
    conversation_repo.set_status(conv["id"], "open")
    plogic.on_outbound(None, {"phone": phone, "text": "opa"})

    assert plogic._open_cycles_of_protocolo(proto["id"])      # ciclo novo aberto
    err = plogic.close_protocolo(proto["id"])[1]
    assert err and "resolva-o antes de finalizar" in err


def test_closing_blocked_when_conversation_open_without_cycle(plugin_logic):
    """Rede de segurança: conversa aberta SEM ciclo aberto (reabertura pelo botão / dados
    antigos) também impede finalizar o protocolo."""
    from db.repositories import conversation_repo
    plogic = plugin_logic
    phone = "5511970010100"
    contact, conv = _contact_and_conversation(phone)
    _inbound(plogic, phone)
    proto = plogic._select_open_protocolo(contact["id"])
    with get_engine().begin() as c:
        c.execute(text("UPDATE plugin_protocolos_atendimentos SET ended_at = :ts "
                       "WHERE conversation_id = :c"), {"ts": time.time(), "c": conv["id"]})
    conversation_repo.set_status(conv["id"], "open")           # aberta, sem ciclo aberto

    assert not plogic._open_cycles_of_protocolo(proto["id"])
    err = plogic.close_protocolo(proto["id"])[1]
    assert err and "resolva-o antes de finalizar" in err


# ── Decisão no momento de RESOLVER ────────────────────────────────────────────

def test_resolve_previous_merges_and_keeps_protocol_closed(plugin_logic):
    """'Faz parte do anterior': funde o re-engajamento no protocolo anterior — nenhum
    atendimento novo, só a data final do último é estendida e o protocolo segue FINALIZADO."""
    plogic = plugin_logic
    phone = "5511970003100"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic)
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 120)
    old_cycle = _seed_ciclo(prev, conv["id"], contact["id"])
    with get_engine().begin() as c:
        c.execute(text("UPDATE plugin_protocolos_atendimentos SET ended_at = :ts "
                       "WHERE id = :id"), {"ts": time.time() - 120, "id": old_cycle})
    _inbound(plogic, phone)                                       # abre o protocolo novo

    data, err = plogic.apply_resolve_decision(conv["id"], "previous")
    assert err is None and data["applied"] == "previous"
    at = plogic.get_protocolo(prev)
    assert at["status"] == "fechado"                    # não reabre
    assert _count_open(contact["id"]) == 0              # o transitório foi descartado
    assert at["closed_at"] > time.time() - 30           # fechamento re-carimbado
    rows = _cycles_of(prev)
    assert len(rows) == 1 and int(rows[0]["id"]) == old_cycle    # nada de atendimento novo
    assert rows[0]["ended_at"] > time.time() - 30                # data final estendida
    # Escolha do atendente (source=user) → grava o valor p/ decidir o próximo ciclo.
    assert _get_attr("contact", contact["id"], "continuidade") == "continuacao"


def test_resolve_previous_discards_transient_protocol_and_its_cycles(plugin_logic):
    """O protocolo transitório (aberto pela mensagem do cliente) some junto com os ciclos
    dele — nenhuma linha órfã sobra apontando para o protocolo descartado."""
    plogic = plugin_logic
    phone = "5511970003200"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic)
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 120)
    _seed_ciclo(prev, conv["id"], contact["id"])
    _inbound(plogic, phone)
    transient = plogic._select_open_protocolo(contact["id"])["id"]
    assert transient != prev

    data, err = plogic.apply_resolve_decision(conv["id"], "previous")
    assert err is None and data["applied"] == "previous"
    assert plogic.get_protocolo(transient) is None
    assert not _cycles_of(transient)
    # O único atendimento da conversa é o do protocolo anterior (o novo foi apagado).
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT protocolo_id FROM plugin_protocolos_atendimentos "
            "WHERE conversation_id = :c"), {"c": conv["id"]}).all()
    assert [int(r[0]) for r in rows] == [prev]


def test_resolve_previous_preserves_fields(plugin_logic):
    """Os valores dos rótulos (do atendimento e do protocolo) continuam EXATAMENTE como
    estavam no fechamento anterior — o merge só mexe em datas."""
    plogic = plugin_logic
    phone = "5511970003300"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic)
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 120)
    cyc = _seed_ciclo(prev, conv["id"], contact["id"])
    with get_engine().begin() as c:
        c.execute(text("UPDATE plugin_protocolos_atendimentos SET ended_at = :ts, "
                       "assignee_user_id = 7, assignee_name = 'Ana' WHERE id = :id"),
                  {"ts": time.time() - 120, "id": cyc})
        c.execute(text(
            "INSERT INTO plugin_protocolos_campos_extras "
            "(atendimento_id, def_id, payload, created_at, updated_at) "
            "VALUES (:cy, 'd1', :pl, :ts, :ts)"),
            {"cy": cyc, "ts": time.time(),
             "pl": '{"type":"text","name":"obs","label":"Obs","value":"1o contato"}'})
        c.execute(text(
            "INSERT INTO plugin_protocolos_protocolo_extras "
            "(protocolo_id, def_id, payload, created_at, updated_at) "
            "VALUES (:p, 'p1', :pl, :ts, :ts)"),
            {"p": prev, "ts": time.time(),
             "pl": '{"type":"text","name":"motivo","label":"Motivo","value":"duvida"}'})
    _inbound(plogic, phone)

    assert plogic.apply_resolve_decision(conv["id"], "previous")[1] is None
    row = _cycles_of(prev)[0]
    assert int(row["assignee_user_id"]) == 7 and row["assignee_name"] == "Ana"
    with get_engine().connect() as conn:
        assert "1o contato" in conn.execute(text(
            "SELECT payload FROM plugin_protocolos_campos_extras "
            "WHERE atendimento_id = :cy"), {"cy": cyc}).scalar()
        assert "duvida" in conn.execute(text(
            "SELECT payload FROM plugin_protocolos_protocolo_extras "
            "WHERE protocolo_id = :p"), {"p": prev}).scalar()


def test_resolve_previous_without_cycles_creates_one_closed(plugin_logic):
    """Dado legado: protocolo fechado sem NENHUM atendimento → cria exatamente 1, já
    encerrado (mantém o invariante sem inflar a lista no caso normal)."""
    plogic = plugin_logic
    phone = "5511970003400"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic)
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 120)
    _inbound(plogic, phone)

    assert plogic.apply_resolve_decision(conv["id"], "previous")[1] is None
    rows = _cycles_of(prev)
    assert len(rows) == 1 and rows[0]["ended_at"] is not None


def test_before_status_allows_close_after_merge(plugin_logic):
    """Depois do merge não há atendimento novo a preencher: fechar a conversa é liberado
    mesmo com rótulo OBRIGATÓRIO configurado (senão o core 403aria e a conversa ficaria
    aberta). No fluxo normal (protocolo ABERTO) o gate continua barrando."""
    plogic = plugin_logic
    phone = "5511970003500"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic)
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 120)
    _seed_ciclo(prev, conv["id"], contact["id"])
    before = plogic.get_extra_defs("atendimento")
    plogic.set_field_defs("atendimento", [
        {"key": "motivo", "label": "Motivo", "type": "text", "required": True}])
    try:
        _inbound(plogic, phone)                   # protocolo ABERTO → gate ativo
        payload = {"conversation_id": conv["id"], "new_status": "closed"}
        assert plogic._check_before_status(dict(payload)) is None

        assert plogic.apply_resolve_decision(conv["id"], "previous")[1] is None
        assert plogic._check_before_status(dict(payload)) is not None
    finally:
        plogic.set_field_defs("atendimento", before)


def test_resolve_new_opens_protocol_with_open_cycle(plugin_logic):
    """'É um novo protocolo': o protocolo novo fica ABERTO com um atendimento (ciclo) ABERTO
    — nada é resolvido — e o anterior é marcado como revisado (a pergunta não volta)."""
    plogic = plugin_logic
    phone = "5511970004000"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic)
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 60)
    _inbound(plogic, phone)

    data, err = plogic.apply_resolve_decision(conv["id"], "new")
    assert err is None and data["applied"] == "new"
    assert _count_open(contact["id"]) == 1
    cur = plogic._select_open_protocolo(contact["id"])
    assert data["protocolo_id"] == cur["id"] and cur["id"] != prev
    assert plogic.get_open_cycle(conv["id"], cur["id"]) is not None   # atendimento em curso
    assert _get_attr("contact", contact["id"], "continuidade") == "novo"
    # Não pergunta de novo no próximo clique em "Resolver".
    assert plogic.relink_suggestion_for_contact(contact["id"], conv["id"])["suggest"] is False


def test_resolve_new_without_open_protocol_creates_one(plugin_logic):
    """Sem protocolo aberto (auto_link desligado/adiado), 'novo' CRIA o protocolo + ciclo."""
    plogic = plugin_logic
    phone = "5511970004050"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic)
    assert _count_open(contact["id"]) == 0

    data, err = plogic.apply_resolve_decision(conv["id"], "new")
    assert err is None and _count_open(contact["id"]) == 1
    assert plogic.get_open_cycle(conv["id"], data["protocolo_id"]) is not None


def test_resolve_from_attr_consumes_the_value(plugin_logic):
    """Decisão vinda do atributo (source='attr') CONSOME o valor em vez de regravá-lo."""
    plogic = plugin_logic
    phone = "5511970004100"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic)
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 60)
    _inbound(plogic, phone)
    _set_attr("contact", contact["id"], "continuidade", "continuacao")

    data, err = plogic.apply_resolve_decision(conv["id"], "previous", source="attr")
    assert err is None and data["applied"] == "previous"
    assert plogic.get_protocolo(prev)["status"] == "fechado"    # funde, não reabre
    assert _get_attr("contact", contact["id"], "continuidade") is None


def test_resolve_decision_rejects_bad_input(plugin_logic):
    plogic = plugin_logic
    contact, conv = _contact_and_conversation("5511970004200")
    _configure(plogic)
    assert plogic.apply_resolve_decision(conv["id"], "xpto")[1] is not None
    assert plogic.apply_resolve_decision(99999999, "new")[1] is not None
    # 'previous' sem protocolo anterior do contato → erro (não inventa vínculo).
    assert plogic.apply_resolve_decision(conv["id"], "previous")[1] is not None


def test_block_value_opens_nothing_and_keeps_conversation_closed(plugin_logic):
    plogic = plugin_logic
    phone = "5511970005000"
    contact, _conv = _contact_and_conversation(phone)
    _configure(plogic)
    _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 60)
    _set_attr("contact", contact["id"], "continuidade", "nao_abrir")

    _inbound(plogic, phone)
    assert _count_open(contact["id"]) == 0

    # before_reopen (filter do core) também recusa reabrir a conversa.
    class _Ctx:
        extras = {"phone": phone, "role": "user", "text": "oi"}
    assert plogic.before_reopen(_Ctx(), True) is False
    # O bloqueio NÃO é consumido — é um estado do contato, não uma decisão de um ciclo.
    assert _get_attr("contact", contact["id"], "continuidade") == "nao_abrir"


def test_consume_disabled_keeps_value(plugin_logic):
    """Com 'limpar após aplicar' desligado, a decisão do atributo permanece."""
    plogic = plugin_logic
    phone = "5511970006000"
    contact, conv = _contact_and_conversation(phone)
    _configure(plogic, consume=False)
    _inbound(plogic, phone)
    _set_attr("contact", contact["id"], "continuidade", "novo")

    plogic.apply_resolve_decision(conv["id"], "new", source="attr")
    assert _get_attr("contact", contact["id"], "continuidade") == "novo"


# ── Escrita pelas rotas do popup ──────────────────────────────────────────────

def test_popup_choices_write_the_mapped_value(plugin_logic, build_app):
    plogic = plugin_logic
    c = build_app(["gowa", "protocolos"]).client
    _configure(plogic)

    # (a) "Faz parte do anterior" → grava 'continuacao'.
    contact_a, conv_a = _contact_and_conversation("5511970007000")
    prev = _seed_protocolo(contact_a["id"], status="fechado", closed_at=time.time() - 60)
    cur = _seed_protocolo(contact_a["id"], status="aberto")
    _seed_ciclo(cur, conv_a["id"], contact_a["id"])
    r = c.post(f"/api/plugins/protocolos/protocolos/{prev}/relink",
               json={"current_open_id": cur, "conversation_id": conv_a["id"]})
    assert r.status_code == 200, r.text
    assert _get_attr("contact", contact_a["id"], "continuidade") == "continuacao"

    # (b) "É um novo protocolo" → grava 'novo'.
    contact_b, conv_b = _contact_and_conversation("5511970007001")
    r = c.post(f"/api/plugins/protocolos/conversas/{conv_b['id']}/open-new-protocolo")
    assert r.status_code == 200, r.text
    assert _get_attr("contact", contact_b["id"], "continuidade") == "novo"

    # (c) "Fechar conversa e protocolo juntos" → grava o valor de bloqueio.
    contact_c, conv_c = _contact_and_conversation("5511970007002")
    prev_c = _seed_protocolo(contact_c["id"], status="fechado", closed_at=time.time() - 60)
    r = c.post(f"/api/plugins/protocolos/protocolos/{prev_c}/relink-reviewed"
               f"?conversation_id={conv_c['id']}")
    assert r.status_code == 200, r.text
    assert _get_attr("contact", contact_c["id"], "continuidade") == "nao_abrir"


def test_relink_decision_endpoint(plugin_logic, build_app):
    """Rota que o painel chama no resolver (com a escolha do atendente ou do atributo)."""
    plogic = plugin_logic
    c = build_app(["gowa", "protocolos"]).client
    _configure(plogic)
    contact, conv = _contact_and_conversation("5511970008000")
    prev = _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 60)
    _inbound(plogic, "5511970008000")

    r = c.post(f"/api/plugins/protocolos/conversas/{conv['id']}/relink-decision",
               json={"kind": "previous", "previous_id": prev})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["applied"] == "previous"
    assert plogic.get_protocolo(prev)["status"] == "fechado"   # fundido, segue finalizado
    assert _count_open(contact["id"]) == 0
    assert _get_attr("contact", contact["id"], "continuidade") == "continuacao"

    # Conversa inexistente → 404; decisão inválida → 409.
    assert c.post("/api/plugins/protocolos/conversas/99999999/relink-decision",
                  json={"kind": "new"}).status_code == 404
    assert c.post(f"/api/plugins/protocolos/conversas/{conv['id']}/relink-decision",
                  json={"kind": "xpto"}).status_code == 409


def test_suggestion_endpoint_exposes_attr_decision(plugin_logic, build_app):
    """No resolver só 'previous'/'new' são decisões; 'block' é regra de ABERTURA."""
    plogic = plugin_logic
    c = build_app(["gowa", "protocolos"]).client
    _configure(plogic)
    contact, conv = _contact_and_conversation("5511970009000")
    _seed_protocolo(contact["id"], status="fechado", closed_at=time.time() - 60)
    _set_attr("contact", contact["id"], "continuidade", "nao_abrir")

    url = (f"/api/plugins/protocolos/contacts/{contact['id']}/relink-suggestion"
           f"?conversation_id={conv['id']}")
    data = c.get(url).json()["data"]
    assert data["attr_decision"] is None
    assert data["suggest"] is True

    _set_attr("contact", contact["id"], "continuidade", "novo")
    data = c.get(url).json()["data"]
    assert data["attr_decision"] == "new"
    assert data["suggest"] is False
