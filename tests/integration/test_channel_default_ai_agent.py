"""Plano 152 — o "atendente padrão" do canal também aceita um AGENTE DE IA.

O campo da tela de Canais é UM só ("Atendente padrão para novas conversas"), mas
guarda duas chaves mutuamente exclusivas em ``channels.config['ai']``:

- ``default_assignee_user_id`` (int) — o humano do plano 71: a conversa nasce
  ``assignee_user_id=<uid>`` e com a IA DESLIGADA;
- ``default_assignee_agent_key`` (texto, plano 152) — o agente de IA: a conversa
  nasce ``active_agent_key=<chave>``, ``assignee_user_id=NULL`` e com a IA
  LIGADA. É o mesmo efeito do ``kind="ai"`` do picker unificado do painel
  (``assign_unified``), só que aplicado ao NASCIMENTO.

Regras que estes testes travam:

1. nascimento carimba o agente + IA on (mesmo com ``default_ai_enabled`` OFF — a
   escolha explícita vence, senão escolher um agente não faria nada);
2. o master do canal (``ai_enabled``) manda: OFF ⇒ nenhum agente assume;
3. reabertura de conversa ÓRFÃ (sem humano E sem agente) reaplica o agente;
4. humano vence quando as duas chaves aparecem juntas (config editada à mão);
5. agente inexistente/desabilitado ⇒ ignorado (fail-open, sem dono fantasma).

    venv/bin/python -m pytest tests/integration/test_channel_default_ai_agent.py -q
"""

from __future__ import annotations

import json

import pytest

from db.repositories import (agent_repo, conversation_repo, config_repo,
                             channel_repo, inbox_repo, user_repo)
from channels import ai_settings


_CREATED_USER_IDS: list[int] = []
_CREATED_AGENT_KEYS: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_p152(_engine_ready):
    yield
    from db.repositories import session_repo

    for user_id in reversed(_CREATED_USER_IDS):
        session_repo.delete_for_user(user_id)
        user_repo.delete(user_id)
    _CREATED_USER_IDS.clear()
    for key in reversed(_CREATED_AGENT_KEYS):
        agent_repo.delete(key)
    _CREATED_AGENT_KEYS.clear()


def _mk_user(email: str) -> int:
    u = user_repo.get_by_email(email)
    if u is None:
        u = user_repo.create(email=email, name="Atendente 152", password_hash="x")
        _CREATED_USER_IDS.append(u["id"])
    return u["id"]


def _mk_agent(key: str, *, enabled: bool = True) -> str:
    """Cria (idempotente) um agente de IA e devolve a chave. ``enabled=False``
    exercita a guarda "agente desabilitado não carimba"."""
    if agent_repo.get(key) is None:
        agent_repo.ensure(key, display_name=f"Agente {key}", prompt="responda",
                          enabled=enabled)
        _CREATED_AGENT_KEYS.append(key)
    elif agent_repo.get(key)["enabled"] != enabled:
        from db.engine import get_engine
        from db.tables import ai_agents
        from sqlalchemy import update as sa_update
        with get_engine().begin() as conn:
            conn.execute(sa_update(ai_agents).where(ai_agents.c.agent_key == key)
                         .values(enabled=1 if enabled else 0))
    return key


def _mk_channel(channel_id: str, *, agent_key: str | None = None,
                user_id: int | None = None, ai_enabled: bool | None = None,
                default_ai_enabled: bool | None = None):
    """Cria (idempotente) canal + inbox com os overrides pedidos em ``config['ai']``.
    ``None`` = sem aquele override."""
    ai: dict = {}
    if agent_key is not None:
        ai["default_assignee_agent_key"] = agent_key
    if user_id is not None:
        ai["default_assignee_user_id"] = user_id
    if ai_enabled is not None:
        ai["ai_enabled"] = ai_enabled
    if default_ai_enabled is not None:
        ai["default_ai_enabled"] = default_ai_enabled
    cfg = json.dumps({"ai": ai}) if ai else None
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1, config=cfg)
    else:
        channel_repo.update(channel_id, config=cfg)
    if inbox_repo.get_by_channel(channel_id) is None:
        inbox_repo.create(channel_id=channel_id, name=channel_id)
    ai_settings.reset_cache(channel_id)


def _set_global(built, enabled: bool) -> None:
    config_repo.set("auto_reply", True)
    config_repo.set("default_ai_enabled", enabled)
    built.agent_handler.default_ai_enabled = enabled


def _seed_conv(handler, phone: str, channel_id: str) -> dict:
    mem = handler._get_contact(phone, channel_id=channel_id)
    saved = mem.add_message("user", "oi")
    return conversation_repo.get(saved["conversation_id"])


# ── Alvo: canal com agente de IA padrão → nasce vinculada + IA on ───────────

def test_channel_with_default_ai_agent_binds_it_and_ia_on(build_app):
    """O alvo: canal com ``default_assignee_agent_key`` → a conversa nasce
    ``active_agent_key=<chave>``, ``ai_active=1`` e SEM dono humano."""
    built = build_app(["gowa"])
    _set_global(built, True)
    key = _mk_agent("p152_vendas")
    _mk_channel("p152_ch_agent", agent_key=key)

    conv = _seed_conv(built.agent_handler, "5511972000001", "p152_ch_agent")
    assert conv["active_agent_key"] == key, \
        "conversa nascida num canal com agente padrão deve nascer vinculada a ele"
    assert conv["ai_active"] == 1, "agente de IA ⇒ nasce com a IA ligada"
    assert conv["assignee_user_id"] is None, "agente de IA não é dono humano"


def test_ai_agent_wins_over_default_ai_enabled_off(build_app):
    """A escolha EXPLÍCITA do agente vence o "IA ativada por padrão para novos
    contatos" desmarcado — do contrário escolher um agente não faria nada (a
    conversa nasceria IA-off e, pela regra do INSERT, sem agente nenhum)."""
    built = build_app(["gowa"])
    _set_global(built, True)
    key = _mk_agent("p152_suporte")
    _mk_channel("p152_ch_seedoff", agent_key=key, default_ai_enabled=False)

    conv = _seed_conv(built.agent_handler, "5511972000002", "p152_ch_seedoff")
    assert conv["ai_active"] == 1
    assert conv["active_agent_key"] == key


# ── Master do canal DESLIGADO ⇒ nenhum agente assume ────────────────────────

def test_channel_ai_master_off_ignores_ai_agent(build_app):
    """Cinto de segurança do "bloquear na UI + ignorar no backend": com
    ``ai_enabled=False`` o agente é ignorado e a conversa nasce SEM dono e com a
    IA off — nunca "atribuída" a uma IA que o gate do canal cala."""
    built = build_app(["gowa"])
    _set_global(built, True)
    key = _mk_agent("p152_calado")
    _mk_channel("p152_ch_master_off", agent_key=key, ai_enabled=False)

    conv = _seed_conv(built.agent_handler, "5511972000003", "p152_ch_master_off")
    assert conv["active_agent_key"] is None, "master do canal off ⇒ sem agente"
    assert conv["ai_active"] == 0
    assert conv["assignee_user_id"] is None


def test_global_auto_reply_off_ignores_ai_agent(build_app):
    """O gate GLOBAL ``auto_reply`` continua soberano no INSERT: com ele off, nem
    o agente escolhido nem a IA sobrevivem ao nascimento."""
    built = build_app(["gowa"])
    config_repo.set("auto_reply", False)
    config_repo.set("default_ai_enabled", True)
    built.agent_handler.default_ai_enabled = True
    key = _mk_agent("p152_global")
    _mk_channel("p152_ch_global_off", agent_key=key)

    conv = _seed_conv(built.agent_handler, "5511972000004", "p152_ch_global_off")
    assert conv["active_agent_key"] is None
    assert conv["ai_active"] == 0


# ── Reabertura órfã reaplica o agente (espelha o P2 revisado do plano 71) ────

def test_reopen_rebinds_default_ai_agent_when_orphan(build_app):
    """Conversa fechada que reabre ÓRFÃ (sem humano e sem agente) volta para o
    agente do canal, com a IA ligada. Sem isto a 2ª conversa do mesmo cliente
    cairia no agente padrão GLOBAL, não no escolhido para o canal."""
    built = build_app(["gowa"])
    _set_global(built, True)
    key = _mk_agent("p152_reopen")
    _mk_channel("p152_ch_reopen", agent_key=key)

    mem = built.agent_handler._get_contact("5511972000010", channel_id="p152_ch_reopen")
    conv_id = mem.add_message("user", "abre")["conversation_id"]
    assert conversation_repo.get(conv_id)["active_agent_key"] == key

    conversation_repo.set_status(conv_id, "closed")
    # O fechamento deixa a conversa órfã (limpa dono e agente).
    closed = conversation_repo.get(conv_id)
    assert closed["assignee_user_id"] is None and not closed["active_agent_key"]

    conv_id2 = mem.add_message("user", "reabre")["conversation_id"]
    assert conv_id2 == conv_id, "mesma conversa é reaberta, não uma nova"
    row = conversation_repo.get(conv_id)
    assert row["status"] == "open"
    assert row["active_agent_key"] == key, \
        "reabertura órfã deve reaplicar o agente de IA padrão do canal"
    assert row["ai_active"] == 1
    assert row["assignee_user_id"] is None


def test_reopen_preserves_human_owner_over_ai_agent(build_app):
    """Estado vivo vence o padrão: se o dono HUMANO sobreviveu ao fechamento, a
    reabertura NÃO o troca pelo agente de IA do canal."""
    built = build_app(["gowa"])
    _set_global(built, True)
    key = _mk_agent("p152_preserva")
    uid = _mk_user("p152_dono@test.com")
    _mk_channel("p152_ch_preserva", agent_key=key)

    mem = built.agent_handler._get_contact("5511972000011", channel_id="p152_ch_preserva")
    conv_id = mem.add_message("user", "abre")["conversation_id"]
    conversation_repo.set_assignee(conv_id, uid)
    conversation_repo.set_status(conv_id, "closed", clear_assignee=False)

    mem.add_message("user", "reabre")
    row = conversation_repo.get(conv_id)
    assert row["assignee_user_id"] == uid, "dono humano vivo não é trocado pela IA"
    assert row["active_agent_key"] is None


# ── Exclusão mútua: humano vence quando as duas chaves estão setadas ─────────

def test_human_wins_when_both_keys_are_set(build_app):
    """Config editada à mão com as DUAS chaves: o humano vence (comportamento
    legado do plano 71) — nunca ligar a IA por acidente."""
    built = build_app(["gowa"])
    _set_global(built, True)
    key = _mk_agent("p152_perdedor")
    uid = _mk_user("p152_vencedor@test.com")
    _mk_channel("p152_ch_ambos", agent_key=key, user_id=uid)

    conv = _seed_conv(built.agent_handler, "5511972000020", "p152_ch_ambos")
    assert conv["assignee_user_id"] == uid
    assert conv["ai_active"] == 0
    assert conv["active_agent_key"] is None


# ── Guarda: agente inexistente / desabilitado / valor lixo ───────────────────

def test_unknown_or_disabled_agent_is_ignored(build_app):
    """Agente que não existe, ou existe DESABILITADO, é ignorado (fail-open): a
    conversa nasce sem vínculo explícito e com o seed normal do canal — nada de
    agente "fantasma" anunciado na tela."""
    built = build_app(["gowa"])
    _set_global(built, True)
    _mk_channel("p152_ch_ghost", agent_key="p152_nao_existe")
    conv = _seed_conv(built.agent_handler, "5511972000030", "p152_ch_ghost")
    assert conv["active_agent_key"] != "p152_nao_existe"

    off_key = _mk_agent("p152_desligado", enabled=False)
    _mk_channel("p152_ch_disabled", agent_key=off_key)
    conv = _seed_conv(built.agent_handler, "5511972000031", "p152_ch_disabled")
    assert conv["active_agent_key"] != off_key, "agente desabilitado não carimba"


def test_bogus_agent_key_coerces_to_none(build_app):
    """Coerção defensiva: valores não-texto ou vazios na config ⇒ ignorados, nunca
    uma exceção no caminho crítico de criação da conversa."""
    built = build_app(["gowa"])
    _set_global(built, True)
    for i, bogus in enumerate(("", "   ", 0, 7, True, ["x"])):
        cid = f"p152_ch_bogus_{i}"
        _mk_channel(cid, agent_key=bogus)
        conv = _seed_conv(built.agent_handler, f"551197200004{i}", cid)
        assert conv["assignee_user_id"] is None
        # O seed normal do canal continua valendo (IA on, agente resolvido pelo
        # fallback global) — o que importa é NÃO ter explodido nem carimbado lixo.
        assert conv["active_agent_key"] != bogus


# ── Nascimento closed ("ignorar abertura") NÃO recebe agente ────────────────

def test_create_closed_birth_gets_no_ai_agent(build_app):
    """Espelha o P2 do plano 71: o ramo ``create_closed`` não recebe o seed — nem
    dono humano, nem o agente de IA DO CANAL.

    Ele também ignora ``ai_active_seed`` (chama ``create`` direto), então a conversa
    ainda pode nascer com o agente padrão GLOBAL vinculado — comportamento anterior
    ao plano 152, fora do escopo desta mudança. O que se trava aqui é que o agente
    ESCOLHIDO PARA O CANAL não chega ao nascimento fechado."""
    built = build_app(["gowa"])
    _set_global(built, True)
    key = _mk_agent("p152_closed")
    _mk_channel("p152_ch_closedbirth", agent_key=key)

    mem = built.agent_handler._get_contact("5511972000050",
                                           channel_id="p152_ch_closedbirth")
    saved = mem.add_message("assistant", "aviso automático", reopen=False)
    conv = conversation_repo.get(saved["conversation_id"])
    assert conv["status"] == "closed"
    assert conv["active_agent_key"] != key, "create_closed não recebe o agente do canal"
    assert conv["assignee_user_id"] is None
