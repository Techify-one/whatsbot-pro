"""Plano 71 — atendente padrão para novas conversas é POR-CANAL.

Um canal pode declarar ``config.ai.default_assignee_user_id`` (um ``user_id``
humano). Toda conversa NASCIDA nesse canal é carimbada com ``assignee_user_id`` e
nasce com a IA desligada (``ai_active=0`` ⇒ sem ``active_agent_key``, pela regra
que já existe no INSERT) — sai da fila "Não atribuídas" já pertencendo ao humano.

Espelha o precedente do seed de ``ai_active`` por-canal (plano 38 F1,
``test_seed_ai_active_per_channel.py``): o valor é lido fresh na hora do CREATE
via ``ai_settings`` (cache 30s), só vale no nascimento (reopen NUNCA re-carimba —
P2 travado em "só no nascimento") e um canal SEM o campo segue byte-idêntico ao
legado (nasce ``assignee_user_id=NULL``).

    venv/bin/python -m pytest tests/integration/test_channel_default_assignee.py -q
"""

from __future__ import annotations

import json

import pytest

from db.repositories import (conversation_repo, config_repo, channel_repo,
                             inbox_repo, user_repo)
from channels import ai_settings


_CREATED_USER_IDS: list[int] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_p71_users(_engine_ready):
    yield
    from db.repositories import session_repo

    for user_id in reversed(_CREATED_USER_IDS):
        session_repo.delete_for_user(user_id)
        assert user_repo.delete(user_id), f"could not remove plano 71 user {user_id}"
    _CREATED_USER_IDS.clear()


def _mk_user(email: str, *, active: bool = True) -> int:
    """Cria (idempotente) um usuário e devolve o id. ``active=False`` desativa
    (para exercitar a guarda P5 — atendente inativo não carimba)."""
    u = user_repo.get_by_email(email)
    if u is None:
        u = user_repo.create(email=email, name="Atendente 71", password_hash="x")
        _CREATED_USER_IDS.append(u["id"])
    if not active:
        from db.engine import get_engine
        from db.tables import users
        from sqlalchemy import update as sa_update
        with get_engine().begin() as conn:
            conn.execute(sa_update(users).where(users.c.id == u["id"])
                         .values(is_active=0))
    return u["id"]


def _mk_channel(channel_id: str, *, default_assignee_user_id: int | None,
                ai_enabled: bool | None = None):
    """Cria (idempotente) um canal + inbox, opcionalmente com os overrides
    ``config['ai']['default_assignee_user_id']`` e ``['ai_enabled']`` (master do
    canal). ``None`` = sem aquele override."""
    ai: dict = {}
    if default_assignee_user_id is not None:
        ai["default_assignee_user_id"] = default_assignee_user_id
    if ai_enabled is not None:
        ai["ai_enabled"] = ai_enabled
    cfg = json.dumps({"ai": ai}) if ai else None
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1, config=cfg)
    else:
        channel_repo.update(channel_id, config=cfg)
    if inbox_repo.get_by_channel(channel_id) is None:
        inbox_repo.create(channel_id=channel_id, name=channel_id)
    ai_settings.reset_cache(channel_id)


def _conv_event_count(conv_id: int) -> int:
    """Nº de cards painel-only (role='conversation_event') de uma conversa — usado
    para provar que o carimbo direto NÃO adiciona um card de atribuição no
    nascimento (§2.3: direct stamp em vez de assign_unified)."""
    from db.engine import get_engine
    from db.tables import messages
    from sqlalchemy import select, func
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(messages)
            .where(messages.c.conversation_id == conv_id,
                   messages.c.role == "conversation_event")
        ).scalar() or 0


def _set_global(built, enabled: bool) -> None:
    """Liga o master GLOBAL ``auto_reply`` + o default ``default_ai_enabled`` de
    "IA para novos contatos". Com ambos ON, uma conversa NORMAL nasceria IA-ativa
    + com agente — então provar que o carimbo de atendente a força a nascer IA-off
    (sem agente) só é significativo com este cenário. Espelha o helper do plano 38."""
    config_repo.set("auto_reply", True)
    config_repo.set("default_ai_enabled", enabled)
    built.agent_handler.default_ai_enabled = enabled


def _seed_conv(handler, phone: str, channel_id: str) -> dict:
    """Cria uma conversa nova para ``phone`` no ``channel_id`` e devolve a row."""
    mem = handler._get_contact(phone, channel_id=channel_id)
    saved = mem.add_message("user", "oi")
    return conversation_repo.get(saved["conversation_id"])


# ── F1 alvo: canal COM atendente padrão → nasce atribuída + IA off ──────────

def test_channel_with_default_assignee_stamps_owner_and_ia_off(build_app):
    """O alvo (inicialmente VERMELHO): canal com ``default_assignee_user_id`` →
    a conversa nasce ``assignee_user_id=<uid>``, ``ai_active=0`` e SEM agente,
    mesmo com a IA global/canal ligada."""
    built = build_app(["gowa"])
    _set_global(built, True)
    uid = _mk_user("p71_owner@test.com")
    _mk_channel("p71_ch_owner", default_assignee_user_id=uid)

    conv = _seed_conv(built.agent_handler, "5511971000001", "p71_ch_owner")
    assert conv["assignee_user_id"] == uid, \
        "conversa nascida num canal com atendente padrão deve pertencer a ele"
    assert conv["ai_active"] == 0, "atendente humano ⇒ nasce com a IA desligada"
    assert conv["active_agent_key"] is None, \
        "IA off no nascimento ⇒ sem agente vinculado (regra do INSERT)"


# ── F1 controle: canal SEM o campo → legado inalterado ──────────────────────

def test_channel_without_default_assignee_is_unchanged(build_app):
    """Controle: canal SEM ``default_assignee_user_id`` → nasce ``assignee_user_id
    NULL`` (fila "Não atribuídas") e IA ligada (caminho byte-idêntico ao legado)."""
    built = build_app(["gowa"])
    _set_global(built, True)
    _mk_channel("p71_ch_none", default_assignee_user_id=None)

    conv = _seed_conv(built.agent_handler, "5511971000002", "p71_ch_none")
    assert conv["assignee_user_id"] is None
    assert conv["ai_active"] == 1
    assert conv["active_agent_key"] is not None


# ── F4/F6 E2E: reuso preserva; reabertura "Não atribuída" reaplica (P2 revisado) ──

def test_reuse_open_conversation_keeps_owner(build_app):
    """Cenário Curseduca: uma 2ª dúvida do mesmo aluno chega com a conversa AINDA
    ABERTA → ela é reusada (transition=None) e o dono persiste. O carimbo NÃO é
    reaplicado num reuse de conversa aberta (só no nascimento e na reabertura)."""
    built = build_app(["gowa"])
    _set_global(built, True)
    uid = _mk_user("p71_reuse@test.com")
    _mk_channel("p71_ch_reuse", default_assignee_user_id=uid)

    mem = built.agent_handler._get_contact("5511971000010", channel_id="p71_ch_reuse")
    conv_id = mem.add_message("user", "primeira")["conversation_id"]
    assert conversation_repo.get(conv_id)["assignee_user_id"] == uid

    conv_id2 = mem.add_message("user", "segunda")["conversation_id"]
    assert conv_id2 == conv_id, "2ª mensagem reusa a mesma conversa aberta"
    assert conversation_repo.get(conv_id)["assignee_user_id"] == uid


def test_reopen_reassigns_default_when_unassigned(build_app):
    """P2 revisado (F6) — O FLUXO CURSEDUCA: a 1ª dúvida nasce atribuída ao atendente
    padrão; o atendente fecha (o close limpa o dono, comportamento legado); a 2ª
    dúvida REABRE a mesma conversa, que voltaria "Não atribuída" — e agora é
    RE-ATRIBUÍDA ao atendente padrão do canal + IA off. É exatamente o caso que o
    usuário pediu (aluno recorrente reabre a mesma conversa)."""
    built = build_app(["gowa"])
    _set_global(built, True)
    uid = _mk_user("p71_reopen@test.com")
    _mk_channel("p71_ch_reopen", default_assignee_user_id=uid)

    mem = built.agent_handler._get_contact("5511971000011", channel_id="p71_ch_reopen")
    conv_id = mem.add_message("user", "abre")["conversation_id"]
    assert conversation_repo.get(conv_id)["assignee_user_id"] == uid

    # Fecha limpando o dono (default) → conversa fica "Não atribuída".
    conversation_repo.set_status(conv_id, "closed")
    assert conversation_repo.get(conv_id)["assignee_user_id"] is None
    # 2ª dúvida reabre a MESMA conversa → reaplica o atendente padrão + IA off.
    conv_id2 = mem.add_message("user", "reabre")["conversation_id"]
    assert conv_id2 == conv_id, "mesma conversa é reaberta, não uma nova"
    row = conversation_repo.get(conv_id)
    assert row["status"] == "open"
    assert row["assignee_user_id"] == uid, \
        "reabertura de conversa Não atribuída deve reaplicar o atendente padrão (F6)"
    assert row["ai_active"] == 0, "reabertura com atendente ⇒ IA off"
    assert row["active_agent_key"] is None


def test_reopen_preserves_manually_changed_owner(build_app):
    """P2: se o dono foi TROCADO manualmente para outro atendente, um reopen que
    preserva o dono (close com clear_assignee=False) mantém o NOVO dono — nunca
    força de volta ao atendente padrão do canal."""
    built = build_app(["gowa"])
    _set_global(built, True)
    uid = _mk_user("p71_owner_a@test.com")
    uid2 = _mk_user("p71_owner_b@test.com")
    _mk_channel("p71_ch_manual", default_assignee_user_id=uid)

    mem = built.agent_handler._get_contact("5511971000012", channel_id="p71_ch_manual")
    conv_id = mem.add_message("user", "abre")["conversation_id"]
    conversation_repo.set_assignee(conv_id, uid2)  # reatribuição manual
    conversation_repo.set_status(conv_id, "closed", clear_assignee=False)

    mem.add_message("user", "reabre")
    assert conversation_repo.get(conv_id)["assignee_user_id"] == uid2, \
        "reopen preserva o dono manual; nunca força de volta ao padrão do canal"


# ── F4 guarda P5: atendente inativo/removido não carimba (dono "fantasma") ───

def test_inactive_assignee_is_ignored(build_app):
    """P5: config aponta para um ``user_id`` DESATIVADO → o carimbo é ignorado
    (fail-open): a conversa nasce sem dono e com a IA no seed normal do canal
    (aqui, ligada) — nada de dono "fantasma"."""
    built = build_app(["gowa"])
    _set_global(built, True)
    dead_uid = _mk_user("p71_inactive@test.com", active=False)
    _mk_channel("p71_ch_dead", default_assignee_user_id=dead_uid)

    conv = _seed_conv(built.agent_handler, "5511971000013", "p71_ch_dead")
    assert conv["assignee_user_id"] is None, "atendente inativo não carimba"
    assert conv["ai_active"] == 1, "sem atendente válido ⇒ seed de IA normal do canal"


def test_bogus_assignee_value_coerces_to_none(build_app):
    """Coerção defensiva: valores inválidos na config (``""``/``"0"``/lixo) ⇒ None
    (nasce sem dono), nunca uma exceção no fluxo crítico de criação."""
    built = build_app(["gowa"])
    _set_global(built, True)
    for i, bogus in enumerate(("", "0", "abc", 0, True)):
        cid = f"p71_ch_bogus_{i}"
        _mk_channel(cid, default_assignee_user_id=bogus)
        conv = _seed_conv(built.agent_handler, f"551197100003{i}", cid)
        assert conv["assignee_user_id"] is None, f"valor inválido {bogus!r} ⇒ sem dono"


# ── Config REAL do Curseduca: master do canal OFF + atendente padrão ─────────

def test_curseduca_config_channel_ai_off_plus_assignee(build_app):
    """A config de produção do canal "Avisos Curseduca": master do canal
    (``ai_enabled``) DESLIGADO **e** atendente padrão setado. A conversa nasce
    atribuída ao humano + IA off + sem agente. (Regressão do placement do <select>:
    o campo tem de valer mesmo com a IA do canal desligada.)"""
    built = build_app(["gowa"])
    _set_global(built, True)
    uid = _mk_user("p71_curseduca@test.com")
    _mk_channel("p71_ch_curseduca", default_assignee_user_id=uid, ai_enabled=False)

    conv = _seed_conv(built.agent_handler, "5511971000050", "p71_ch_curseduca")
    assert conv["assignee_user_id"] == uid
    assert conv["ai_active"] == 0
    assert conv["active_agent_key"] is None


def test_global_auto_reply_off_still_stamps_assignee(build_app):
    """O carimbo de atendente é INDEPENDENTE do gate de IA: mesmo com o master
    GLOBAL ``auto_reply`` DESLIGADO (que por si já força IA off + sem agente), a
    conversa nasce atribuída ao humano."""
    built = build_app(["gowa"])
    config_repo.set("auto_reply", False)
    config_repo.set("default_ai_enabled", True)
    built.agent_handler.default_ai_enabled = True
    uid = _mk_user("p71_global_off@test.com")
    _mk_channel("p71_ch_global_off", default_assignee_user_id=uid)

    conv = _seed_conv(built.agent_handler, "5511971000051", "p71_ch_global_off")
    assert conv["assignee_user_id"] == uid
    assert conv["ai_active"] == 0
    assert conv["active_agent_key"] is None


# ── Nascimento closed ("ignorar abertura") NÃO recebe dono (P2) ─────────────

def test_create_closed_birth_gets_no_owner(build_app):
    """P2 / regra "ignorar abertura": um nascimento FORÇADO a closed
    (``reopen=False`` num contato novo) NÃO recebe atendente — o seed só chega ao
    ramo ``created`` (open), nunca ao ramo ``create_closed``."""
    built = build_app(["gowa"])
    _set_global(built, True)
    uid = _mk_user("p71_closedbirth@test.com")
    _mk_channel("p71_ch_closedbirth", default_assignee_user_id=uid)

    mem = built.agent_handler._get_contact("5511971000052", channel_id="p71_ch_closedbirth")
    saved = mem.add_message("assistant", "aviso automático", reopen=False)
    conv = conversation_repo.get(saved["conversation_id"])
    assert conv["status"] == "closed", "reopen=False num contato novo nasce closed"
    assert conv["assignee_user_id"] is None, "create_closed não recebe dono (P2)"


# ── Carimbo direto NÃO emite card de atribuição no nascimento (§2.3) ─────────

def test_birth_stamp_emits_no_extra_assignment_card(build_app):
    """O carimbo direto no INSERT (em vez de ``assign_unified``) NÃO deve emitir um
    card de atribuição no nascimento — só o ``created`` normal. Prova comparando o
    nº de ``conversation_event`` de um nascimento COM atendente vs SEM (controle):
    devem ser iguais (nenhum card extra de "atribuído")."""
    built = build_app(["gowa"])
    _set_global(built, True)
    uid = _mk_user("p71_card@test.com")
    _mk_channel("p71_ch_card_on", default_assignee_user_id=uid)
    _mk_channel("p71_ch_card_off", default_assignee_user_id=None)

    conv_on = _seed_conv(built.agent_handler, "5511971000053", "p71_ch_card_on")
    conv_off = _seed_conv(built.agent_handler, "5511971000054", "p71_ch_card_off")
    assert _conv_event_count(conv_on["id"]) == _conv_event_count(conv_off["id"]), \
        "carimbo direto não deve adicionar card de atribuição (sem assign_unified)"
