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

Estado (pós-execução do plano 37): as asserções destes testes já refletem o
comportamento CORRETO (o misfiling foi consertado). Cobrem os 4 clusters — gate
(A10), tools (A1/A2), motor (A4), ensure_ai_agent (A6), private-AI (B1),
toggle-ai (B3), transcrição (B5) e a trava de transferência (D1) — mais a
regressão single-channel e o guardrail anti-regressão (P4).

    venv/bin/python -m pytest tests/integration/test_multichannel_routing.py -q
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

    Hermeticidade: liga o master global ``auto_reply`` antes de semear — o gate
    de criação zera ai_active+agente com ele off (o boot persiste ``False`` num
    banco fresco), e testes como o A6 (``ensure_ai_agent`` no-op com ai_active=0)
    dependiam de outro arquivo da sessão ter ligado o master (ordem).
    """
    from db.repositories import config_repo
    config_repo.set("auto_reply", True)
    config_repo.set("default_ai_enabled", True)
    handler.default_ai_enabled = True  # o handler cacheia o global no boot
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


# ── D1 — transferir num canal não silencia o outro (FD1/P1-a) ───────────────

def test_d1_transfer_does_not_silence_sibling_channel(build_app):
    """FD1/P1-a: ``transfer_to_human`` no Telegram pausa a IA SÓ da conversa do
    Telegram (ai_active=0). A conversa default segue respondendo — a tag
    ``transferido_atendente`` (contact-global) NÃO é mais lida como trava."""
    from app.services.messaging_service import _conversation_ai_active
    from agent.tools import transfer_to_human as t2h

    built = build_app(["gowa"])
    handler = built.agent_handler
    s = _seed_two_inboxes(handler, "5511970000018", "mc_tg_d1")
    conversation_repo.set_ai_active(s.default_conv, 1)
    conversation_repo.set_ai_active(s.tg_conv, 1)

    ctx = SimpleNamespace(contact=s.tg_mem, tag_registry=handler.tag_registry)
    t2h.execute(ctx, {"reason": "quero humano"})

    # A tag foi gravada (rótulo visual, contact-global)...
    from db.repositories import tag_repo
    assert t2h.TRANSFER_TAG in (tag_repo.get_contact_tags(s.contact_id) or [])
    # ...mas o gate é por-conversa: Telegram calado, WhatsApp respondendo.
    assert _conversation_ai_active(s.tg_mem) is False
    assert _conversation_ai_active(s.d_mem) is True


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


# ── B3 — toggle-ai do contato ancora no canal do painel (FB3/P2) ────────────

def test_b3_toggle_ai_scoped_to_panel_channel(build_app):
    """FB3/P2: desligar a IA informando a conversa do Telegram zera o ``ai_active``
    SÓ do Telegram; a conversa default segue com a IA ligada (não reflete)."""
    built = build_app(["gowa"])
    handler = built.agent_handler
    phone = "5511970000016"
    s = _seed_two_inboxes(handler, phone, "mc_tg_b3")
    conversation_repo.set_ai_active(s.default_conv, 1)
    conversation_repo.set_ai_active(s.tg_conv, 1)

    r = built.client.post(f"/api/contacts/{phone}/toggle-ai", json={
        "enabled": False, "conversation_id": s.tg_conv})
    assert r.status_code == 200, r.text

    assert conversation_repo.get(s.tg_conv)["ai_active"] == 0        # togglada
    assert conversation_repo.get(s.default_conv)["ai_active"] == 1   # intacta


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


# ── B5 — transcrição escrita na msg do canal do turno (FB5) ─────────────────

def _last_user_content(conv_id: int) -> str | None:
    from db.tables import messages as _m
    from db.engine import get_engine
    from sqlalchemy import select
    with get_engine().connect() as conn:
        row = conn.execute(
            select(_m.c.content).where(
                _m.c.conversation_id == conv_id, _m.c.role == "user")
            .order_by(_m.c.ts.desc()).limit(1)).first()
    return row[0] if row else None


def test_b5_transcription_updates_turn_channel_message(build_app):
    """FB5: com msg de user nos dois canais (default mais recente), a transcrição
    escopada ao Telegram atualiza a msg DO Telegram, não a globalmente mais recente."""
    built = build_app(["gowa"])
    handler = built.agent_handler
    phone = "5511970000017"
    s = _seed_two_inboxes(handler, phone, "mc_tg_b5")

    handler.update_last_user_message_content(phone, "TRANSCRITO", channel_id="mc_tg_b5")

    assert _last_user_content(s.tg_conv) == "TRANSCRITO"
    assert _last_user_content(s.default_conv) == "oi pelo whatsapp"  # intacta


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
    """B1 (FB1 corrigido — a conversa #41): uma nota "IA lê" numa conversa de
    Telegram salva a resposta assistant no inbox **do Telegram**, não no default —
    ``_run_private_ai`` agora threada ``channel_id`` para ``aprocess_message`` e
    ``save_assistant_message``.

    Caracteriza o ``channel_id`` que ``save_assistant_message`` recebe + a conversa
    real onde a resposta caiu."""
    from tests.fakes import fake_agent_reply

    built = build_app(["gowa"])
    handler = built.agent_handler
    phone = "5511970000013"
    s = _seed_two_inboxes(handler, phone, "mc_tg_priv")

    saved_channels: list[str] = []
    aproc_channels: list[str] = []
    orig = handler.save_assistant_message

    def _spy(p, text, **kw):
        saved_channels.append(kw.get("channel_id", "default"))
        return orig(p, text, **kw)

    handler.save_assistant_message = _spy
    try:
        with fake_agent_reply("segue a resposta", handler=handler) as rec:
            r = built.client.post(f"/api/contacts/{phone}/private-message", json={
                "text": "responde o cliente",
                "conversation_id": s.tg_conv,
                "ai_read": True,
                "ai_reply": True,
            })
            assert r.status_code == 200, r.text
            assert _poll(lambda: bool(saved_channels)), "private-AI não rodou a tempo"
        aproc_channels = [c[2].get("channel_id") for c in rec.calls]
    finally:
        handler.save_assistant_message = orig

    # CORRETO (FB1): resposta salva no canal do Telegram; contexto lido do Telegram.
    assert saved_channels == ["mc_tg_priv"]
    assert aproc_channels == ["mc_tg_priv"]
    # Nenhuma resposta assistant caiu na conversa default (WhatsApp fantasma #41).
    from db.tables import messages as _m
    from db.engine import get_engine
    from sqlalchemy import select, func
    with get_engine().connect() as conn:
        default_assistants = conn.execute(
            select(func.count()).select_from(_m).where(
                _m.c.conversation_id == s.default_conv,
                _m.c.role == "assistant")).scalar()
    assert default_assistants == 0


# ── A5 — atributos de conversa no prompt vêm do canal do turno (FA2) ────────

def test_a5_prompt_conversation_attrs_from_turn_channel(build_app):
    """FA2/A5: ``ContactMemory._custom_attr_lines('conversation')`` lê a conversa
    do inbox DAQUELE ContactMemory (self.inbox_id), não a mais recente."""
    from db.repositories import custom_attribute_repo as ca_repo
    from db.tables import conversations as conv_tbl

    built = build_app(["gowa"])
    handler = built.agent_handler
    s = _seed_two_inboxes(handler, "5511970000019", "mc_tg_a5")

    # Define um atributo de conversa e grava valores DISTINTOS em cada conversa.
    if not ca_repo.definition_exists("mc_a5_attr", "conversation"):
        ca_repo.create_definition(attribute_key="mc_a5_attr", display_name="A5",
                                  applies_to="conversation", type="text")
    ca_repo.set_values(conv_tbl, s.default_conv, {"mc_a5_attr": "VALOR-DEFAULT"})
    ca_repo.set_values(conv_tbl, s.tg_conv, {"mc_a5_attr": "VALOR-TELEGRAM"})

    lines = "\n".join(s.tg_mem._custom_attr_lines("conversation"))
    assert "VALOR-TELEGRAM" in lines
    assert "VALOR-DEFAULT" not in lines


# ── Regressão single-channel: comportamento idêntico ao legado ──────────────

def test_regression_single_channel_gate_and_tools(build_app):
    """O caminho comum (single-channel): resolver por-inbox coincide com o legado.
    Uma só conversa (default); gate e transfer agem NELA, exatamente como antes."""
    from app.services.messaging_service import _conversation_ai_active
    from agent.tools import transfer_to_human as t2h

    built = build_app(["gowa"])
    handler = built.agent_handler
    phone = "5511970000020"
    mem = handler._get_contact(phone, channel_id="default")
    conv_id = mem.add_message("user", "oi")["conversation_id"]

    conversation_repo.set_ai_active(conv_id, 1)
    assert _conversation_ai_active(mem) is True

    ctx = SimpleNamespace(contact=mem, tag_registry=handler.tag_registry)
    t2h.execute(ctx, {"reason": "humano"})
    assert conversation_repo.get(conv_id)["ai_active"] == 0
    assert _conversation_ai_active(mem) is False


# ── Guardrail anti-regressão (P4): sem resolvers channel-blind novos ────────

def test_guardrail_no_new_channel_blind_resolvers():
    """P4: proíbe NOVOS ``get_open_for_contact(``/``get_latest_for_contact(`` (sem
    ``_inbox``/``_scoped``) em caminhos sensíveis a canal. Os fallbacks legados
    intencionais (D2 fail-open / P3 por-phone) estão na allow-list por contagem —
    adicionar um resolver channel-blind novo num arquivo sensível quebra o teste."""
    import re
    from pathlib import Path

    from tests.paths import PROJECT_ROOT
    repo = PROJECT_ROOT
    # Arquivos/dirs sensíveis (onde channel-blind = bug). conversation_repo.py é o
    # site de DEFINIÇÃO dos resolvers + do fallback sancionado → fora do scan.
    roots = [
        repo / "agent",
        repo / "app" / "services",
        repo / "server" / "routes",
        repo / "server" / "system_notices.py",
    ]
    # Fallbacks legados INTENCIONAIS (contagem exata de linhas de CHAMADA por arquivo):
    #  - system_notices.py: ramo legado de resolve_conversation_for_contact (inbox_id None)
    #  - conversations.py:  fallback por-phone do GET /atendimento (P3)
    #  - tags.py:           âncora contact-global do card de tag (P3)
    allow = {
        "server/system_notices.py": 2,
        "server/routes/conversations.py": 3,
        "server/routes/tags.py": 2,
    }
    pat = re.compile(r"get_(?:open|latest)_for_contact\b(?!_inbox|_scoped)")

    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        else:
            files.extend(root.rglob("*.py"))

    offenders: dict[str, int] = {}
    for f in files:
        rel = str(f.relative_to(repo))
        count = 0
        for raw in f.read_text(encoding="utf-8").splitlines():
            code = raw.split("#", 1)[0]          # tira comentário inline
            if "``" in code:                      # refs em docstring (`` `nome` ``)
                continue
            if pat.search(code):
                count += 1
        if count:
            offenders[rel] = count

    # Todo arquivo com chamada channel-blind precisa bater EXATAMENTE a allow-list.
    unexpected = {k: v for k, v in offenders.items()
                  if k not in allow or allow[k] != v}
    assert not unexpected, (
        "Resolver channel-blind novo/alterado em caminho sensível (plano 37 P4). "
        f"Use *_inbox/*_scoped ou atualize a allow-list conscientemente: {unexpected}")
