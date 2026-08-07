"""Plano 36 — agente padrão para novas conversas (flag ``is_default``, radio).

Cobre as garantias do plano:
- ``agent_repo``: salvar com ``is_default=1`` rebaixa o padrão anterior (semântica
  radio); o índice único parcial impede dois padrões; ``get_new_conversation_default``
  devolve a linha marcada; ``rollback`` preserva a flag.
- **Carimbo de criação** (``default_agent_key_for_inbox`` / ``conversation_repo.create``):
  uma conversa NOVA nasce no agente ``is_default``; ``inbox.default_agent_key`` continua
  vencendo; sem padrão, cai no piso ``"default"``.
- **Não muda em andamento**: uma conversa criada ANTES de marcar o padrão mantém o
  ``active_agent_key`` que já tinha — a flag nunca reescreve conversa em curso.

    venv/bin/python -m pytest tests/integration/test_default_agent.py -q
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from db.engine import get_engine
from db.repositories import agent_repo, conversation_repo
from db.tables import ai_agents

INBOX_ID = 1  # seeded by migration 0013


def _seed_agent(key: str, **kw):
    return agent_repo.save(
        key, display_name=kw.pop("display_name", key.title()),
        model_config={}, tool_names=None, enabled=kw.pop("enabled", True), **kw)


def _make_pair(phone: str):
    from db.repositories import contact_repo, contact_inbox_repo
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    return contact, ci


def _clear_defaults():
    """Zera qualquer is_default para deixar o teste hermético (DB é process-global)."""
    with get_engine().begin() as conn:
        conn.execute(ai_agents.update().where(ai_agents.c.is_default == 1)
                     .values(is_default=0))


@pytest.fixture(autouse=True)
def _floor_agent(_engine_ready):
    """Garante a row do piso legado ('default') antes de cada teste.

    Numa instalação real ela nasce no seed do boot; o banco de teste só roda as
    migrations, e desde o fix agente-padrão o piso exige a ROW viva (antes o
    código devolvia a string literal sem consultar o banco)."""
    agent_repo.ensure(agent_repo.DEFAULT_AGENT_KEY, display_name="Agente padrão")


# ---------------------------------------------------------------- F2: agent_repo

def test_save_is_default_radio_demotes_previous(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_a")
    _seed_agent("p36_b")

    _seed_agent("p36_a", is_default=True)
    assert agent_repo.get_new_conversation_default()["agent_key"] == "p36_a"

    # Promover o B rebaixa o A (radio) — só um padrão sobrevive.
    _seed_agent("p36_b", is_default=True)
    assert agent_repo.get_new_conversation_default()["agent_key"] == "p36_b"
    assert agent_repo.get("p36_a")["is_default"] is False
    assert agent_repo.get("p36_b")["is_default"] is True

    rows = [a for a in agent_repo.list_all() if a["is_default"]]
    assert len(rows) == 1


def test_partial_unique_index_blocks_two_defaults(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_idx_a")
    _seed_agent("p36_idx_b", is_default=True)
    # Forçar um segundo is_default=1 direto no banco (contornando o save) viola o
    # índice único parcial ux_ai_agents_single_default.
    with pytest.raises(IntegrityError):
        with get_engine().begin() as conn:
            conn.execute(ai_agents.update()
                         .where(ai_agents.c.agent_key == "p36_idx_a")
                         .values(is_default=1))


def test_rollback_preserves_is_default(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_rb", is_default=True)  # v1 (is_default)
    _seed_agent("p36_rb", display_name="Mudou", is_default=False)  # v2 (não é mais)
    assert agent_repo.get("p36_rb")["is_default"] is False
    # Reverter para a v1 (que era is_default) restaura a flag.
    agent_repo.rollback("p36_rb", 1)
    assert agent_repo.get("p36_rb")["is_default"] is True


def test_get_new_conversation_default_none_when_unset(_engine_ready):
    _clear_defaults()
    assert agent_repo.get_new_conversation_default() is None


# --------------------------------------------------- F4: carimbo de criação

def test_default_agent_key_for_inbox_uses_is_default(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_stamp")
    # Sem padrão marcado: cai no piso "default".
    assert conversation_repo.default_agent_key_for_inbox(INBOX_ID) == \
        agent_repo.DEFAULT_AGENT_KEY
    # Marcado: o carimbo passa a apontar para ele.
    _seed_agent("p36_stamp", is_default=True)
    assert conversation_repo.default_agent_key_for_inbox(INBOX_ID) == "p36_stamp"


def test_disabled_default_agent_falls_back_to_floor(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_off", is_default=True, enabled=False)
    # Padrão desabilitado não é adotado — carimbo cai no piso.
    assert conversation_repo.default_agent_key_for_inbox(INBOX_ID) == \
        agent_repo.DEFAULT_AGENT_KEY


def test_new_conversation_stamps_is_default_agent(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_conv", is_default=True)
    contact, ci = _make_pair("5511990036001")
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    try:
        assert conv["active_agent_key"] == "p36_conv"
    finally:
        conversation_repo.set_status(conv["id"], "closed")


def test_existing_conversation_not_rebound(_engine_ready):
    """A restrição dura: conversa em andamento não muda quando o padrão troca."""
    _clear_defaults()
    contact, ci = _make_pair("5511990036002")
    # Nasce sem padrão marcado → carimba o piso "default".
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    try:
        assert conv["active_agent_key"] == agent_repo.DEFAULT_AGENT_KEY
        # AGORA marca outro agente como padrão de novas conversas.
        _seed_agent("p36_late", is_default=True)
        # A conversa EXISTENTE segue apontando para o que tinha — nada reescreve.
        again = conversation_repo.get(conv["id"])
        assert again["active_agent_key"] == agent_repo.DEFAULT_AGENT_KEY
    finally:
        conversation_repo.set_status(conv["id"], "closed")


# ------------------------------------------- fix agente-padrão (2026-07)
# O fallback de RUNTIME passa a honrar o is_default (get_default resolve o
# marcado primeiro), e o agente 'default' legado vira excluível assim que outro
# carrega a marcação — a trava deixa de ser a chave e vira "é o fallback atual".

def test_get_default_resolves_is_default_first(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_fb")
    # Sem marcação: piso legado.
    assert agent_repo.get_default()["agent_key"] == agent_repo.DEFAULT_AGENT_KEY
    # Marcado: o runtime cai NELE (era o bug — fechar+reabrir voltava ao 'default').
    _seed_agent("p36_fb", is_default=True)
    assert agent_repo.get_default()["agent_key"] == "p36_fb"
    assert agent_repo.get_fallback_key() == "p36_fb"
    # Marcado porém desabilitado não vale como fallback — volta ao piso legado.
    _seed_agent("p36_fb", is_default=True, enabled=False)
    assert agent_repo.get_default()["agent_key"] == agent_repo.DEFAULT_AGENT_KEY


def test_delete_refuses_current_fallback(_engine_ready):
    _clear_defaults()
    # Sem marcação, o fallback é o 'default' legado — não sai.
    assert agent_repo.delete(agent_repo.DEFAULT_AGENT_KEY) is False
    # Com outro agente marcado, ELE vira o intocável e o 'default' é excluível.
    _seed_agent("p36_crown", is_default=True)
    assert agent_repo.delete("p36_crown") is False
    assert agent_repo.delete(agent_repo.DEFAULT_AGENT_KEY) is True
    assert agent_repo.get(agent_repo.DEFAULT_AGENT_KEY) is None
    # Restaura o piso imediatamente (outros testes do processo assumem a row).
    agent_repo.ensure(agent_repo.DEFAULT_AGENT_KEY, display_name="Agente padrão")


def test_delete_unbinds_conversations(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_gone")
    contact, ci = _make_pair("5511990036003")
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"],
        active_agent_key="p36_gone")
    try:
        assert conv["active_agent_key"] == "p36_gone"
        assert agent_repo.delete("p36_gone") is True
        # A exclusão limpa o vínculo na mesma transação → NULL (runtime resolve
        # o fallback do momento), nunca uma chave pendurada.
        again = conversation_repo.get(conv["id"])
        assert again["active_agent_key"] is None
    finally:
        conversation_repo.set_status(conv["id"], "closed")


def test_ai_off_conversation_born_unassigned(_engine_ready):
    """Fix atribuição-IA-off: conversa nascida com IA desligada NÃO carimba
    agente — fica de fato "Não atribuída" (sem humano e sem agente)."""
    _clear_defaults()
    _seed_agent("p36_aioff", is_default=True)
    contact, ci = _make_pair("5511990036004")
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"],
        ai_active=0)
    try:
        assert conv["ai_active"] == 0
        assert conv["active_agent_key"] is None
        assert conv["assignee_user_id"] is None
    finally:
        conversation_repo.set_status(conv["id"], "closed")


def test_ai_off_creation_respects_explicit_agent(_engine_ready):
    """Um active_agent_key EXPLÍCITO do caller continua valendo com IA off."""
    _clear_defaults()
    _seed_agent("p36_explicit")
    contact, ci = _make_pair("5511990036005")
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"],
        ai_active=0, active_agent_key="p36_explicit")
    try:
        assert conv["active_agent_key"] == "p36_explicit"
    finally:
        conversation_repo.set_status(conv["id"], "closed")


def test_stamp_and_fallback_none_when_floor_deleted(_engine_ready):
    _clear_defaults()
    _seed_agent("p36_crown2", is_default=True)
    assert agent_repo.delete(agent_repo.DEFAULT_AGENT_KEY) is True
    _clear_defaults()  # desmarca tudo → sistema sem padrão marcado e sem piso
    try:
        assert conversation_repo.default_agent_key_for_inbox(INBOX_ID) is None
        assert agent_repo.get_default() is None
        assert agent_repo.get_fallback_key() is None
    finally:
        agent_repo.ensure(agent_repo.DEFAULT_AGENT_KEY, display_name="Agente padrão")
