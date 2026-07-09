"""Plano 38 F1/F2 — seed do ``ai_active`` de uma conversa nova é POR-CANAL.

Bug (Problema 1): o ``ai_active`` de uma conversa nova era semeado SÓ pela config
global ``default_ai_enabled`` — o toggle "IA por padrão para novos contatos" DO
CANAL era lido (``ContactMemory._default_ai_enabled``) mas nunca descia até o
INSERT. Com a global OFF e o toggle de um canal (Telegram/Cloud) ON, toda conversa
nova nascia ``ai_active=0`` e a IA nunca falava.

Estas asserções refletem o comportamento CORRETO (pós-F1): o seed honra o override
por-canal (``ai_settings.value(channel_id, "default_ai_enabled", <global>)``),
com a global como fallback. Reopen NÃO re-semeia (preserva pausa manual).

    venv/bin/python -m pytest tests/test_seed_ai_active_per_channel.py -q
"""

from __future__ import annotations

import json

import pytest

from db.repositories import conversation_repo, config_repo, channel_repo, inbox_repo
from channels import ai_settings


def _mk_channel(channel_id: str, *, default_ai_enabled: bool | None):
    """Cria (idempotente) um canal + inbox, opcionalmente com o override
    ``config['ai']['default_ai_enabled']``. ``None`` = canal sem override."""
    cfg = None
    if default_ai_enabled is not None:
        cfg = json.dumps({"ai": {"default_ai_enabled": default_ai_enabled}})
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1, config=cfg)
    else:
        channel_repo.update(channel_id, config=cfg)
    if inbox_repo.get_by_channel(channel_id) is None:
        inbox_repo.create(channel_id=channel_id, name=channel_id)
    ai_settings.reset_cache(channel_id)


def _set_global(built, enabled: bool) -> None:
    """Ajusta o default GLOBAL de "IA para novos contatos". O ``handler`` cacheia
    esse valor em ``default_ai_enabled`` (fallback do ``_get_contact`` quando o
    canal não overrida) — em produção sincronizado do config no save; no teste
    setamos os dois."""
    config_repo.set("default_ai_enabled", enabled)
    built.agent_handler.default_ai_enabled = enabled


def _seed_ai_active(handler, phone: str, channel_id: str) -> int:
    """Cria uma conversa nova para ``phone`` no ``channel_id`` e devolve o
    ``ai_active`` resultante."""
    mem = handler._get_contact(phone, channel_id=channel_id)
    saved = mem.add_message("user", "oi")
    conv = conversation_repo.get(saved["conversation_id"])
    return conv["ai_active"]


def test_global_off_channel_on_seeds_active(build_app):
    """(global OFF × canal ON) ⇒ ai_active=1 — o bug original."""
    built = build_app(["gowa"])
    _set_global(built, False)
    _mk_channel("seed_ch_on", default_ai_enabled=True)
    assert _seed_ai_active(built.agent_handler, "5511960000001", "seed_ch_on") == 1


def test_global_off_channel_absent_seeds_inactive(build_app):
    """(global OFF × canal sem override) ⇒ ai_active=0 — herda a global."""
    built = build_app(["gowa"])
    _set_global(built, False)
    _mk_channel("seed_ch_none", default_ai_enabled=None)
    assert _seed_ai_active(built.agent_handler, "5511960000002", "seed_ch_none") == 0


def test_global_on_channel_off_seeds_inactive(build_app):
    """(global ON × canal OFF) ⇒ ai_active=0 — o canal desliga apesar da global."""
    built = build_app(["gowa"])
    _set_global(built, True)
    _mk_channel("seed_ch_off", default_ai_enabled=False)
    assert _seed_ai_active(built.agent_handler, "5511960000003", "seed_ch_off") == 0


def test_global_on_channel_absent_seeds_active(build_app):
    """(global ON × sem override) ⇒ ai_active=1 — o default herdado (GOWA inalterado)."""
    built = build_app(["gowa"])
    _set_global(built, True)
    _mk_channel("seed_ch_inherit", default_ai_enabled=None)
    assert _seed_ai_active(built.agent_handler, "5511960000004", "seed_ch_inherit") == 1


def test_reopen_does_not_reseed(build_app):
    """Reabrir uma conversa fechada NÃO re-semeia o ai_active (preserva pausa manual).
    Com o canal ON, fecho a conversa com ai_active=0 e uma nova mensagem reabre
    sem religar a IA."""
    built = build_app(["gowa"])
    _set_global(built, False)
    _mk_channel("seed_ch_reopen", default_ai_enabled=True)
    phone = "5511960000005"
    mem = built.agent_handler._get_contact(phone, channel_id="seed_ch_reopen")
    conv_id = mem.add_message("user", "oi")["conversation_id"]
    assert conversation_repo.get(conv_id)["ai_active"] == 1  # nasceu ligada (canal ON)

    # Pausa manual + fecha; reabrir por nova mensagem preserva a pausa.
    conversation_repo.set_ai_active(conv_id, 0)
    conversation_repo.set_status(conv_id, "closed")
    mem.add_message("user", "de novo")
    assert conversation_repo.get(conv_id)["ai_active"] == 0  # reopen não re-semeou
