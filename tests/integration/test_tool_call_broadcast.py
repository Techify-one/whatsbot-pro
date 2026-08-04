"""Plano 30 · F1 (WS1) — card painel-only ``tool_call`` broadcastado ao vivo.

O card deve carregar o ``conversation_id`` da LINHA REALMENTE SALVA (resolvida
inbox-aware por ``ContactMemory.add_message``), não o de
``conversation_repo.get_open_for_contact`` — que devolve a conversa aberta mais
recente de QUALQUER canal. Com o contato tendo ≥2 conversas abertas em canais
diferentes, o id divergia e o painel descartava o card silenciosamente (ele só
reaparecia no F5, lendo do banco). Espelha o padrão ``private_note``
(``server/routes/contacts.py``): ``conversation_id``/``ts``/``_id`` vêm da row
salva.

Rodar: venv/bin/python -m pytest tests/integration/test_tool_call_broadcast.py -q
"""

from __future__ import annotations

import asyncio


def _capture_broadcasts(ws_manager, events: list):
    async def _capture(event, data):
        events.append((event, data))
    original = ws_manager.broadcast
    ws_manager.broadcast = _capture
    return original


def test_tool_call_card_uses_saved_conversation_id(build_app):
    """Multi-conversa: o payload ao vivo aponta pra thread da linha salva."""
    from db.repositories import (
        channel_repo, conversation_repo, inbox_repo, message_repo,
    )

    built = build_app(["gowa"])
    deps = built.app.state.deps
    handler = built.agent_handler
    phone = "5511930000031"

    # Conversa A: canal default — a thread onde a tool realmente roda/salva.
    contact = handler._get_contact(phone)
    contact.add_message("user", "oi")
    conv_a = conversation_repo.get_open_for_contact_inbox(
        contact.id, contact.inbox_id)
    assert conv_a is not None

    # Conversa B: segundo canal/inbox, aberta DEPOIS → é a mais recente por
    # last_activity_at e é o que get_open_for_contact (canal-cego) devolve.
    if not channel_repo.get("tg_p30"):
        channel_repo.create(id="tg_p30", provider="telegram",
                            display_name="Telegram P30")
    inbox_b = inbox_repo.get_or_create_for_channel("tg_p30", name="Telegram P30")
    conv_b, _ = conversation_repo.resolve_for_contact_ex(
        contact.id, phone, inbox_id=inbox_b["id"])
    conversation_repo.touch_activity(conv_b["id"])
    newest = conversation_repo.get_open_for_contact(contact.id)
    assert newest and newest["id"] == conv_b["id"], (
        "pré-condição: a conversa de outro canal é a aberta mais recente")

    events: list = []
    original = _capture_broadcasts(deps.ws_manager, events)
    try:
        asyncio.run(deps.broadcast_tool_calls(
            phone,
            [{"tool": "save_contact_info", "args": {"name": "Maria"},
              "result": "Dados salvos."}],
            None))
    finally:
        deps.ws_manager.broadcast = original

    cards = [d for e, d in events
             if e == "new_message" and (d.get("message") or {}).get("role") == "tool_call"]
    assert len(cards) == 1, f"esperava 1 card tool_call, veio {len(cards)}"
    payload = cards[0]
    msg = payload["message"]
    assert payload.get("phone") == phone
    assert payload.get("channel_id") == "default"

    saved = message_repo.get_last(contact.id)
    assert saved and saved["role"] == "tool_call"
    assert saved["conversation_id"] == conv_a["id"], (
        "add_message é inbox-aware: a row salva pertence à conversa do canal")

    # O payload ao vivo deve apontar pra MESMA thread da linha salva…
    assert msg.get("conversation_id") == saved["conversation_id"], (
        "card ao vivo divergiu da row salva → o painel descarta e o card só "
        "aparece no F5")
    # …e nunca pra conversa mais recente de OUTRO canal.
    assert msg.get("conversation_id") != conv_b["id"]
    # ts/_id vêm da row salva (padrão private_note). ts do banco também evita
    # o colapso no dedupe ts+role do painel.
    assert msg.get("ts") == saved["ts"]
    assert msg.get("_id") == saved["_id"]


def test_tool_call_card_single_channel_still_routes(build_app):
    """Single-channel (caso comum): card continua com o id da conversa aberta."""
    from db.repositories import conversation_repo, message_repo

    built = build_app(["gowa"])
    deps = built.app.state.deps
    handler = built.agent_handler
    phone = "5511930000032"

    contact = handler._get_contact(phone)
    contact.add_message("user", "olá")
    conv = conversation_repo.get_open_for_contact_inbox(
        contact.id, contact.inbox_id)
    assert conv is not None

    events: list = []
    original = _capture_broadcasts(deps.ws_manager, events)
    try:
        asyncio.run(deps.broadcast_tool_calls(
            phone,
            [{"tool": "set_custom_attribute",
              "args": {"key": "plano", "value": "pro"},
              "result": "Atributo salvo."}],
            None))
    finally:
        deps.ws_manager.broadcast = original

    cards = [d for e, d in events
             if e == "new_message" and (d.get("message") or {}).get("role") == "tool_call"]
    assert len(cards) == 1
    msg = cards[0]["message"]
    saved = message_repo.get_last(contact.id)
    assert saved and saved["role"] == "tool_call"
    assert msg.get("conversation_id") == conv["id"] == saved["conversation_id"]
    assert "🔧 set_custom_attribute" in msg.get("content", "")
