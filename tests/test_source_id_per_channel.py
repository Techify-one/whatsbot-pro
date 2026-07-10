"""Plano 42 A0/A1 — ``contact_inboxes.source_id`` é resolvido PELO PROVIDER.

Antes (bug residual do Defeito #1): ``ContactMemory._jid`` reconstruía sempre
``phone@s.whatsapp.net`` / ``@g.us`` — para QUALQUER canal — então o ``source_id``
do inbox de um contato Telegram/Cloud nascia com cara de JID WhatsApp.

Agora (plano 42 A1): a forma do ``source_id`` é responsabilidade do PROVIDER
(``Channel.source_id_for``): GOWA mantém o sufixo WhatsApp (byte-idêntico ao
backfill 0013); providers de id nativo (Telegram/Cloud) usam o id cru (bare).
A resolução passa pelo ChannelRegistry wired; quando ele NÃO está wired (default
no harness de teste / legado) o comportamento cai no fail-safe = sufixo WhatsApp,
preservando o GOWA de produção sem regressão.

    venv/bin/python -m pytest tests/test_source_id_per_channel.py -q
"""

from __future__ import annotations

import pytest

from channels.base import Channel, SendResult
from channels.providers.gowa_channel import GOWAChannel


# ── Stub de provider não-GOWA (herda source_id_for bare da base) ────────────
class _CloudStub(Channel):
    provider = "whatsapp_cloud"

    def status(self) -> dict:
        return {"connected": False, "logged_in": False, "needs_qr": False, "error": ""}

    def send_text(self, chat_id, text, *, reply_to=None, mentions=None) -> SendResult:
        return SendResult(ok=True)

    def send_media(self, chat_id, kind, path_or_url, *, caption="", filename=None) -> SendResult:
        return SendResult(ok=True)

    def parse_inbound(self, raw):
        return []


def _mk_provider_channel(channel_id: str, provider: str) -> None:
    """Cria (idempotente) um canal de ``provider`` + a sua inbox."""
    from db.repositories import channel_repo, inbox_repo
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider=provider,
                            display_name=channel_id, enabled=1)
    if inbox_repo.get_by_channel(channel_id) is None:
        inbox_repo.create(channel_id=channel_id, name=channel_id)


def _source_id_of(conversation_id: int) -> str:
    """O ``contact_inboxes.source_id`` da conversa recém-resolvida."""
    from db.repositories import conversation_repo, contact_inbox_repo
    conv = conversation_repo.get(conversation_id)
    return contact_inbox_repo.get(conv["contact_inbox_id"])["source_id"]


# ── Testes puros do classmethod (sem DB) ────────────────────────────────────
def test_base_source_id_for_is_bare():
    assert Channel.source_id_for("5511999990001", False) == "5511999990001"
    assert Channel.source_id_for("120363000000000000", True) == "120363000000000000"


def test_gowa_source_id_for_appends_wa_suffix():
    assert GOWAChannel.source_id_for("5511999990001", False) == "5511999990001@s.whatsapp.net"
    assert GOWAChannel.source_id_for("120363000000000000", True) == "120363000000000000@g.us"


# ── Integração: resolução ponta a ponta pelo provider do canal ──────────────
@pytest.fixture
def sid_env(build_app):
    """App + runtime de canal wired (GOWA + stub Cloud). Salva/restaura o runtime
    global e limpa o cache de provider por-canal para não vazar entre testes."""
    from channels.registry import ChannelRegistry
    from plugins.context import set_channel_runtime, get_channel_runtime
    import agent.memory as _mem

    built = build_app(["gowa"])
    _mem._PROVIDER_BY_CHANNEL.clear()
    _mk_provider_channel("sid_gowa", "gowa")
    _mk_provider_channel("sid_cloud", "whatsapp_cloud")

    reg = ChannelRegistry()
    reg.register_provider(GOWAChannel)
    reg.register_provider(_CloudStub)
    prev = get_channel_runtime()
    set_channel_runtime(reg, None, None)
    try:
        yield built
    finally:
        set_channel_runtime(*prev)
        _mem._PROVIDER_BY_CHANNEL.clear()


def test_gowa_channel_source_id_keeps_wa_suffix(sid_env):
    """Canal GOWA: source_id byte-idêntico ao histórico (@s.whatsapp.net)."""
    mem = sid_env.agent_handler._get_contact("5511955550001", channel_id="sid_gowa")
    saved = mem.add_message("user", "oi")
    assert _source_id_of(saved["conversation_id"]) == "5511955550001@s.whatsapp.net"


def test_non_gowa_channel_source_id_is_bare(sid_env):
    """Canal não-GOWA (Cloud): source_id é o id nativo cru, SEM sufixo WhatsApp."""
    mem = sid_env.agent_handler._get_contact("5511955550002", channel_id="sid_cloud")
    saved = mem.add_message("user", "oi")
    assert _source_id_of(saved["conversation_id"]) == "5511955550002"


def test_unwired_runtime_falls_back_to_wa_suffix(build_app):
    """Fail-safe: runtime NÃO wired (default no harness) ⇒ até um canal não-GOWA
    mantém a forma JID WhatsApp — byte-idêntico ao pré-plano-42 (zero regressão)."""
    import agent.memory as _mem
    from plugins.context import set_channel_runtime, get_channel_runtime

    built = build_app(["gowa"])
    _mem._PROVIDER_BY_CHANNEL.clear()
    _mk_provider_channel("sid_cloud_unwired", "whatsapp_cloud")
    prev = get_channel_runtime()
    set_channel_runtime(None, None, None)  # explicitamente sem runtime
    try:
        mem = built.agent_handler._get_contact("5511955550003",
                                               channel_id="sid_cloud_unwired")
        saved = mem.add_message("user", "oi")
        assert _source_id_of(saved["conversation_id"]) == "5511955550003@s.whatsapp.net"
    finally:
        set_channel_runtime(*prev)
        _mem._PROVIDER_BY_CHANNEL.clear()
