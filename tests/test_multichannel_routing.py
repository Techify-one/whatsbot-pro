"""Plano 37 — Caracterização (F0) da classe de bug "keyed by contact, não por canal".

O mesmo número/contato existe em VÁRIOS canais (ex.: o canal GOWA/WhatsApp
``default`` E um canal Telegram/Cloud), cada um com sua conversa (``inbox_id``).
Dezenas de call sites resolvem a conversa/estado SÓ pelo contato
(``get_open_for_contact``/``get_latest_for_contact``) ou largam o ``channel_id``
(caindo no default ``"default"`` = WhatsApp) — mesmo tendo o ``inbox_id`` em mãos
no ``ContactMemory``/``ctx.contact``. Resultado: uma ação num canal escreve/roteia
na conversa de OUTRO canal do mesmo número.

Repro do usuário (conversa #41): uma nota privada / "IA lê" enviada numa conversa
de **Telegram** teve a resposta da IA arquivada numa conversa NOVA de **WhatsApp**
(#41) para o mesmo número. A mensagem saiu pelo Telegram, mas a cópia persistida
foi pro WhatsApp — porque ``_run_private_ai`` chama ``aprocess_message`` /
``save_assistant_message`` SEM ``channel_id``.

Estes testes documentam o comportamento ATUAL (o misfiling). Cada fase de correção
(FA1/FA3/FB1/…) INVERTE a asserção correspondente para o canal correto; a fase
F-REG trava o conjunto. Onde uma asserção já reflete o comportamento correto, ela
está marcada como tal.

    venv/bin/python -m pytest tests/test_multichannel_routing.py -q
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from db.repositories import conversation_repo


# ── Setup: um mesmo phone com DOIS canais/inboxes ───────────────────────────

def _mk_channel_inbox(channel_id: str):
    """Cria (idempotente) um canal + inbox não-default. Mesmo helper de
    tests/test_improve_conversation_scope.py."""
    from db.repositories import channel_repo, inbox_repo

    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    inbox = inbox_repo.get_by_channel(channel_id)
    if inbox is None:
        inbox = inbox_repo.create(channel_id=channel_id, name=channel_id)
    return inbox


def _seed_two_inboxes(handler, phone: str, tg_channel: str):
    """Semeia o mesmo contato com conversa aberta em DOIS inboxes.

    O inbox ``default`` (GOWA/WhatsApp) é forçado a ser o MAIS RECENTE — assim o
    resolver channel-blind (``get_open_for_contact``, ordena por last_activity)
    devolve a conversa ERRADA (default) quando a ação parte do canal do Telegram.
    Retorna os ids das duas conversas + o ContactMemory escopado no Telegram.
    """
    _mk_channel_inbox(tg_channel)
    tg_mem = handler._get_contact(phone, channel_id=tg_channel)
    tg_saved = tg_mem.add_message("user", "oi pelo telegram")
    d_mem = handler._get_contact(phone, channel_id="default")
    d_saved = d_mem.add_message("user", "oi pelo whatsapp")
    # default = mais recente (o resolver channel-blind vai preferi-lo).
    conversation_repo.touch_activity(d_saved["conversation_id"], ts=time.time() + 10)
    return SimpleNamespace(
        contact_id=tg_mem.id,
        default_conv=d_saved["conversation_id"],
        tg_conv=tg_saved["conversation_id"],
        tg_mem=tg_mem,
        d_mem=d_mem,
    )


# ── A10 — gate de IA lê o canal errado ──────────────────────────────────────

def test_f0_gate_ai_reads_wrong_channel(build_app):
    """A10 (FA3 corrigido): com a conversa ``default`` pausada (ai_active=0) e a do
    Telegram ativa, ``_conversation_ai_active`` para o contato do Telegram devolve
    **True** — lê a conversa do canal do turno (Telegram), não a default."""
    from app.services.messaging_service import _conversation_ai_active

    built = build_app(["gowa"])
    s = _seed_two_inboxes(built.agent_handler, "5511970000010", "mc_tg_gate")
    conversation_repo.set_ai_active(s.default_conv, 0)   # WhatsApp pausada
    conversation_repo.set_ai_active(s.tg_conv, 1)        # Telegram ativa

    # CORRETO (FA3): a IA do Telegram responde independente da default pausada.
    assert _conversation_ai_active(s.tg_mem) is True
    # E o inverso: pausar o Telegram cala só o Telegram, não a default.
    conversation_repo.set_ai_active(s.tg_conv, 0)
    conversation_repo.set_ai_active(s.default_conv, 1)
    assert _conversation_ai_active(s.tg_mem) is False


# ── A1 — transfer_to_human muta a conversa do canal errado ──────────────────

def test_f0_transfer_to_human_mutates_wrong_channel(build_app):
    """A1 (FA1 corrigido): ``transfer_to_human`` com ``ctx.contact`` do Telegram
    despausa/desatribui a conversa **do Telegram** (o canal do turno), deixando a
    do default intacta — mesmo com o default mais recente."""
    from agent.tools import transfer_to_human as t2h

    built = build_app(["gowa"])
    handler = built.agent_handler
    s = _seed_two_inboxes(handler, "5511970000011", "mc_tg_t2h")
    conversation_repo.set_ai_active(s.default_conv, 1)
    conversation_repo.set_ai_active(s.tg_conv, 1)

    ctx = SimpleNamespace(contact=s.tg_mem, tag_registry=handler.tag_registry)
    t2h.execute(ctx, {"reason": "quero um humano"})

    default_after = conversation_repo.get(s.default_conv)
    tg_after = conversation_repo.get(s.tg_conv)
    # CORRETO (FA1): a conversa do Telegram foi mutada; o default ficou intacto.
    assert tg_after["ai_active"] == 0
    assert default_after["ai_active"] == 1


# ── A2 — transferir_agente carimba a conversa do canal errado ───────────────

def test_f0_transferir_agente_stamps_wrong_channel(build_app):
    """A2 (FA1 corrigido): ``transferir_agente`` com ``ctx.contact`` do Telegram
    grava o ``active_agent_key`` na conversa **do Telegram**, não no default."""
    from agent.tools import transferir_agente as ta
    from db.repositories import agent_repo

    built = build_app(["gowa"])
    handler = built.agent_handler
    s = _seed_two_inboxes(handler, "5511970000012", "mc_tg_ta")

    agent_repo.save("mc_f0_target", display_name="Alvo F0",
                    prompt="p", model_config={"model": "openai/gpt-4o-mini"},
                    tool_names=None, enabled=True)
    try:
        ctx = SimpleNamespace(contact=s.tg_mem, tag_registry=handler.tag_registry)
        ta.execute(ctx, {"agente": "mc_f0_target"})

        default_after = conversation_repo.get(s.default_conv)
        tg_after = conversation_repo.get(s.tg_conv)
        # CORRETO (FA1): o handoff carimbou a conversa do Telegram (canal do turno);
        # o default NÃO recebeu o alvo.
        assert tg_after["active_agent_key"] == "mc_f0_target"
        assert default_after["active_agent_key"] != "mc_f0_target"
    finally:
        agent_repo.delete("mc_f0_target")


# ── A4 — resolve_active_agent_key vem do canal do turno (FA2) ───────────────

def test_a4_resolve_agent_from_turn_channel(build_app):
    """FA2: com agentes distintos vinculados às duas conversas,
    ``resolve_active_agent_key`` para o ContactMemory do Telegram devolve o agente
    DA conversa do Telegram, não o do default (mais recente)."""
    from agent import agent_factory
    from db.repositories import agent_repo

    built = build_app(["gowa"])
    handler = built.agent_handler
    s = _seed_two_inboxes(handler, "5511970000014", "mc_tg_a4")
    for key in ("mc_a4_default", "mc_a4_tg"):
        agent_repo.save(key, display_name=key, prompt="p",
                        model_config={"model": "openai/gpt-4o-mini"},
                        tool_names=None, enabled=True)
    try:
        conversation_repo.set_agent(s.default_conv, "mc_a4_default")
        conversation_repo.set_agent(s.tg_conv, "mc_a4_tg")
        assert agent_factory.resolve_active_agent_key(s.tg_mem) == "mc_a4_tg"
    finally:
        agent_repo.delete("mc_a4_default")
        agent_repo.delete("mc_a4_tg")


# ── A6 — ensure_ai_agent carimba só a conversa do inbox do turno (FA6) ──────

def test_a6_ensure_ai_agent_scoped_to_inbox(build_app):
    """FA6: com duas conversas abertas, ``ensure_ai_agent`` escopado ao inbox do
    Telegram carimba SÓ a conversa do Telegram; a do default fica intacta."""
    from db.repositories import agent_repo

    built = build_app(["gowa"])
    handler = built.agent_handler
    s = _seed_two_inboxes(handler, "5511970000015", "mc_tg_a6")
    agent_repo.save("mc_a6", display_name="A6", prompt="p",
                    model_config={"model": "openai/gpt-4o-mini"},
                    tool_names=None, enabled=True)
    try:
        default_before = conversation_repo.get(s.default_conv)["active_agent_key"]
        conversation_repo.ensure_ai_agent(s.contact_id, "mc_a6", s.tg_mem.inbox_id)

        assert conversation_repo.get(s.tg_conv)["active_agent_key"] == "mc_a6"
        assert conversation_repo.get(s.default_conv)["active_agent_key"] == default_before
    finally:
        agent_repo.delete("mc_a6")


# ── B1 — private-AI arquiva a resposta no canal errado (a conversa #41) ──────

def _poll(pred, timeout: float = 4.0, step: float = 0.02):
    """Espera a task de fundo (_run_private_ai) rodar no loop do TestClient."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


def test_f0_private_ai_saves_reply_to_wrong_channel(build_app):
    """B1 (o bug relatado): uma nota "IA lê" numa conversa de Telegram salva a
    resposta assistant no inbox **default** (WhatsApp), não no Telegram — porque
    ``_run_private_ai`` chama ``save_assistant_message`` sem ``channel_id``.

    Caracteriza o ``channel_id`` que ``save_assistant_message`` recebe. F-REG
    inverte para o canal do Telegram."""
    from tests.fakes import fake_agent_reply

    built = build_app(["gowa"])
    handler = built.agent_handler
    phone = "5511970000013"
    s = _seed_two_inboxes(handler, phone, "mc_tg_priv")

    saved_channels: list[str] = []
    orig = handler.save_assistant_message

    def _spy(p, text, **kw):
        saved_channels.append(kw.get("channel_id", "default"))
        return orig(p, text, **kw)

    handler.save_assistant_message = _spy
    try:
        with fake_agent_reply("segue a resposta", handler=handler):
            r = built.client.post(f"/api/contacts/{phone}/private-message", json={
                "text": "responde o cliente",
                "conversation_id": s.tg_conv,
                "ai_read": True,
                "ai_reply": True,
            })
            assert r.status_code == 200, r.text
            assert _poll(lambda: bool(saved_channels)), "private-AI não rodou a tempo"
    finally:
        handler.save_assistant_message = orig

    # BUG (F0): a resposta foi salva no canal default, não no do Telegram.
    assert saved_channels == ["default"]
