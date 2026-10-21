"""Plano 178 / Fases 3–4 — religar a IA automaticamente (consulta + ``run_once``).

Cobre o que a metade pura (``tests/core/test_ai_reactivation.py``) não alcança:

* a consulta ``conversation_repo.ai_off_for_reactivation`` — só conversas ABERTAS,
  "última mensagem humana" por ``conversation_id``, resposta da IA e nota privada NÃO
  contam, grupo/arquivada/provisionamento fora;
* ``run_once`` — religa pelo MESMO ``conversation_service.set_ai`` do botão do painel
  (card, tag, eventos), respeita canal/``auto_reply`` desligados, e o modo agenda
  dispara uma única vez por horário.

    venv/bin/python -m pytest tests/integration/test_ai_reactivation.py -q
"""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest

from app.services import ai_reactivation_service as svc
from config.settings import TECHIFY_PROVISION_NUMBER
from db.engine import get_engine
from db.repositories import (config_repo, contact_inbox_repo, contact_repo,
                             conversation_repo, message_repo, tag_repo)
from db.tables import contacts as contacts_t, inboxes as inboxes_t, messages as messages_t

INBOX_ID = 1  # seeded by migration 0013 -> channel "default"
NOW = time.time()
HOUR = 3600.0

class _FakeWS:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def broadcast(self, event, data=None):
        self.events.append((event, data or {}))

class _Settings(dict):
    """``deps.settings``: o ``Settings`` real lê/grava no banco; aqui um dict basta."""

def _deps(**cfg):
    settings = _Settings({"auto_reply": True, **cfg})
    return SimpleNamespace(
        ws_manager=_FakeWS(), agent_handler=None, state=SimpleNamespace(),
        settings=settings)

# ``Settings.get(key, default)`` -> dict.get já serve; o laço só usa ``.get``.

@pytest.fixture(autouse=True)
def _db(_engine_ready):
    # Estado global que o serviço lê: zera para cada teste ficar hermético.
    from channels import ai_settings
    ai_settings.reset_cache()
    config_repo.set("ai_reactivation_last_run", 0.0)
    # ``auto_reply`` GLOBAL ligado: com ele desligado ``_insert_conversation`` força
    # ``ai_active=0`` e ``_conv(ai_active=1)`` nasceria desligada (e carimbada), então o
    # resultado dependeria da ordem da suíte. O valor anterior é restaurado no fim.
    previous = config_repo.get("auto_reply", None)
    config_repo.set("auto_reply", True)
    yield
    config_repo.set("auto_reply", True if previous is None else previous)

def _phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"

def _conv(*, ai_active=0, phone=None, group=False, archived=False,
          conv_archived=False, assignee=None, status="open") -> dict:
    phone = phone or _phone()
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    # Números fixos (provisionamento) são reusados entre testes: só pode haver UMA
    # conversa aberta por (contato, inbox), então fecha a de um teste anterior.
    with get_engine().begin() as conn:
        from db.tables import conversations as conversations_t
        conn.execute(conversations_t.update()
                     .where(conversations_t.c.contact_id == contact["id"],
                            conversations_t.c.status == "open")
                     .values(status="closed"))
    if group or archived:
        with get_engine().begin() as conn:
            conn.execute(contacts_t.update().where(contacts_t.c.id == contact["id"])
                         .values(is_group=1 if group else 0,
                                 is_archived=1 if archived else 0))
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"],
        ai_active=ai_active, is_archived=1 if conv_archived else 0, status=status)
    if assignee is not None:
        conversation_repo.set_assignee(conv["id"], assignee)
    conv = conversation_repo.get(conv["id"])
    conv["phone"] = phone
    return conv

def _msg(conv: dict, role: str, ts: float, *, status=None, content="x"):
    message_repo.add(conv["contact_id"], role, content, status=status,
                     conversation_id=conv["id"], ts=ts)

def _backdate_ai_off(conv_id: int, ts: float | None):
    """Define ``ai_disabled_at`` à mão (o carimbo real é 'agora')."""
    with get_engine().begin() as conn:
        from db.tables import conversations
        conn.execute(conversations.update().where(conversations.c.id == conv_id)
                     .values(ai_disabled_at=ts))

def _candidates(**kw) -> dict[int, dict]:
    rows = conversation_repo.ai_off_for_reactivation(kw.pop("groups", False), **kw)
    return {r["id"]: r for r in rows}

def _run(deps, **kw) -> int:
    return asyncio.run(svc.run_once(deps, **kw))

# ── a consulta ──────────────────────────────────────────────────────────────

def test_only_open_conversations_with_the_ai_off_are_candidates():
    on = _conv(ai_active=1)
    off = _conv(ai_active=0)
    closed = _conv(ai_active=0, status="closed")
    got = _candidates()
    assert off["id"] in got
    assert on["id"] not in got
    assert closed["id"] not in got

def test_a_human_assignee_makes_it_a_candidate_even_with_the_ai_flag_on():
    """Humano no comando cala a IA mesmo com ``ai_active=1`` (plano 96)."""
    c = _conv(ai_active=1, assignee=7)
    assert c["id"] in _candidates()

def test_last_human_message_ignores_the_ai_reply_and_private_notes():
    c = _conv()
    _msg(c, "user", NOW - 5000)                                   # cliente
    _msg(c, "assistant", NOW - 4000, status="operator")           # atendente
    _msg(c, "assistant", NOW - 100, status="sent")                # resposta da IA
    _msg(c, "private_note", NOW - 90)
    _msg(c, "transcription", NOW - 80)
    _msg(c, "system_notice", NOW - 70)
    _msg(c, "conversation_event", NOW - 60)
    _msg(c, "tool_call", NOW - 50)
    row = _candidates()[c["id"]]
    assert row["last_role"] == "assistant"
    assert row["last_ts"] == pytest.approx(NOW - 4000)

def test_last_role_is_the_customer_when_the_customer_spoke_last():
    c = _conv()
    _msg(c, "assistant", NOW - 5000, status="operator")
    _msg(c, "user", NOW - 3000)
    _msg(c, "assistant", NOW - 10, status="sent")                 # IA não conta
    row = _candidates()[c["id"]]
    assert row["last_role"] == "user"
    assert row["last_ts"] == pytest.approx(NOW - 3000)

def test_no_human_message_means_no_role():
    c = _conv()
    row = _candidates()[c["id"]]
    assert row["last_role"] is None and row["last_ts"] is None

def test_messages_are_scoped_to_the_conversation_not_the_contact():
    """Mensagem do MESMO contato em OUTRA conversa não conta."""
    a = _conv()
    other = _conv()
    _msg(other, "user", NOW - 10)
    # Mensagem de outro contato/conversa jamais aparece na conversa ``a``.
    assert _candidates()[a["id"]]["last_ts"] is None

def test_groups_and_archived_only_when_asked():
    plain = _conv()
    grp = _conv(group=True)
    arch_contact = _conv(archived=True)
    arch_conv = _conv(conv_archived=True)
    default = _candidates()
    assert plain["id"] in default
    for hidden in (grp, arch_contact, arch_conv):
        assert hidden["id"] not in default
    everything = _candidates(groups=True)
    for shown in (plain, grp, arch_contact, arch_conv):
        assert shown["id"] in everything

def test_provisioning_number_and_its_br_variants_are_excluded():
    # O contato é guardado numa forma (aqui a de 13 dígitos, com o 9); o número de
    # provisionamento pode vir na OUTRA (12 dígitos) — as duas variantes são excluídas.
    stored, other_form = "5513981744038", "551381744038"
    a = _conv(phone=stored)
    ok = _conv()
    for excluded in (stored, other_form):
        got = _candidates(exclude_phones=[excluded])
        assert a["id"] not in got, excluded
        assert ok["id"] in got
    # sem exclusão, ele aparece
    assert a["id"] in _candidates()

# ── run_once ────────────────────────────────────────────────────────────────

def _time_cfg(**kw):
    base = {"ai_reactivation_enabled": True, "ai_reactivation_unit": "hours",
            "ai_reactivation_value": 2}
    base.update(kw)
    return base

def test_disabled_option_does_nothing():
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    assert _run(_deps(ai_reactivation_enabled=False)) == 0
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_global_auto_reply_off_does_nothing():
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    assert _run(_deps(**_time_cfg(), auto_reply=False)) == 0
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_quiet_conversation_is_turned_back_on_through_set_ai(monkeypatch):
    from app.services import messaging_service
    bus: list[tuple[str, dict]] = []

    async def spy(deps, ws_event, bus_event, payload, **kw):
        bus.append((bus_event, payload))

    monkeypatch.setattr(messaging_service, "broadcast_and_emit", spy)
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    deps = _deps(**_time_cfg())
    assert _run(deps) >= 1
    after = conversation_repo.get(c["id"])
    assert after["ai_active"] == 1
    assert after["assignee_user_id"] is None
    assert after["ai_disabled_at"] is None            # religar zera o carimbo
    # o mesmo caminho do botão do painel: o evento do barramento sai de ``set_ai``
    assert "conversation.ai_toggled" in [n for n, _ in bus]

def test_recent_conversation_is_left_alone():
    c = _conv()
    _msg(c, "user", NOW - 60)
    assert _run(_deps(**_time_cfg())) >= 0
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_wait_is_counted_from_when_the_ai_went_off_not_from_the_old_message():
    """Pausada há 1 min, última mensagem de 3 dias: NÃO religa."""
    c = _conv()
    _msg(c, "user", NOW - 72 * HOUR)
    _backdate_ai_off(c["id"], NOW - 60)
    _run(_deps(**_time_cfg()))
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_legacy_row_without_ai_disabled_at_falls_back_to_the_last_message():
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], None)
    _run(_deps(**_time_cfg()))
    assert conversation_repo.get(c["id"])["ai_active"] == 1

def test_sender_filter_customer_skips_attendant_last():
    by_customer = _conv()
    by_attendant = _conv()
    _msg(by_customer, "user", NOW - 10 * HOUR)
    _msg(by_attendant, "assistant", NOW - 10 * HOUR, status="operator")
    for c in (by_customer, by_attendant):
        _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    _run(_deps(**_time_cfg(ai_reactivation_last_sender="customer")))
    assert conversation_repo.get(by_customer["id"])["ai_active"] == 1
    assert conversation_repo.get(by_attendant["id"])["ai_active"] == 0

def test_conversation_with_a_human_assignee_is_taken_back_from_the_human():
    """P2 = religar todas: ``set_ai(1)`` zera o responsável."""
    c = _conv(ai_active=1, assignee=7)
    _msg(c, "user", NOW - 10 * HOUR)
    _run(_deps(**_time_cfg()))
    after = conversation_repo.get(c["id"])
    assert after["assignee_user_id"] is None and after["ai_active"] == 1

def test_the_transfer_tag_is_removed():
    from agent.tools.transfer_to_human import TRANSFER_TAG
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    if tag_repo.get_by_name(TRANSFER_TAG) is None:
        tag_repo.create(TRANSFER_TAG, "#ef4444")
    tag_repo.add_contact_tag(c["contact_id"], TRANSFER_TAG)
    assert TRANSFER_TAG in tag_repo.get_contact_tags(c["contact_id"])
    _run(_deps(**_time_cfg()))
    assert conversation_repo.get(c["id"])["ai_active"] == 1
    assert TRANSFER_TAG not in tag_repo.get_contact_tags(c["contact_id"])

def test_channel_with_the_ai_off_is_skipped():
    from channels import ai_settings
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    channel_id = "default"
    ai_settings._CACHE[channel_id] = ({"ai_enabled": False}, time.time())
    try:
        _run(_deps(**_time_cfg()))
    finally:
        ai_settings.reset_cache()
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_inbox_with_the_bot_gate_off_is_skipped():
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    with get_engine().begin() as conn:
        conn.execute(inboxes_t.update().where(inboxes_t.c.id == INBOX_ID)
                     .values(agent_bot_enabled=0))
    try:
        _run(_deps(**_time_cfg()))
    finally:
        with get_engine().begin() as conn:
            conn.execute(inboxes_t.update().where(inboxes_t.c.id == INBOX_ID)
                         .values(agent_bot_enabled=1))
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_provisioning_contact_is_never_turned_back_on():
    c = _conv(phone=TECHIFY_PROVISION_NUMBER)
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    _run(_deps(**_time_cfg()))
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_a_number_saved_in_provisioning_phones_is_never_turned_back_on():
    rotated = "5511900000001"
    c = _conv(phone=rotated)
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    _run(_deps(**_time_cfg(), provisioning_phones=[rotated]))
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_a_conversation_closed_after_the_query_is_not_reopened_to_the_ai(monkeypatch):
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    real = conversation_repo.ai_off_for_reactivation

    def stale(*a, **kw):
        rows = real(*a, **kw)
        conversation_repo.set_status(c["id"], "closed")   # a person closes it meanwhile
        return rows

    monkeypatch.setattr(conversation_repo, "ai_off_for_reactivation", stale)
    _run(_deps(**_time_cfg()))
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_a_failure_on_one_conversation_does_not_stop_the_others(monkeypatch):
    from app.services import conversation_service
    a, b = _conv(), _conv()
    for c in (a, b):
        _msg(c, "user", NOW - 10 * HOUR)
        _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    real = conversation_service.set_ai

    async def flaky(deps, conv, active, **kw):
        if conv["id"] == a["id"]:
            raise RuntimeError("boom")
        return await real(deps, conv, active, **kw)

    monkeypatch.setattr(conversation_service, "set_ai", flaky)
    _run(_deps(**_time_cfg()))
    assert conversation_repo.get(a["id"])["ai_active"] == 0
    assert conversation_repo.get(b["id"])["ai_active"] == 1

def test_contact_ai_toggled_is_emitted_with_the_auto_source(monkeypatch):
    seen: list[tuple[str, dict]] = []

    async def fake_emit(name, payload):
        seen.append((name, payload))

    monkeypatch.setattr(svc, "emit_with_filter", fake_emit)
    c = _conv()
    _msg(c, "user", NOW - 10 * HOUR)
    _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    _run(_deps(**_time_cfg()))
    mine = [p for n, p in seen if n == "contact.ai_toggled" and p["phone"] == c["phone"]]
    assert len(mine) == 1
    assert mine[0]["source"] == "auto_reactivation" and mine[0]["ai_enabled"] is True

def test_one_pass_is_capped(monkeypatch):
    monkeypatch.setattr(svc, "MAX_PER_PASS", 2)
    convs = [_conv() for _ in range(4)]
    for c in convs:
        _msg(c, "user", NOW - 10 * HOUR)
        _backdate_ai_off(c["id"], NOW - 9 * HOUR)
    first = _run(_deps(**_time_cfg()))
    assert first == 2
    # a segunda passada pega o restante
    assert _run(_deps(**_time_cfg())) >= 2
    assert all(conversation_repo.get(c["id"])["ai_active"] == 1 for c in convs)

# ── modo agenda ─────────────────────────────────────────────────────────────

def _schedule_cfg(now: float, **kw):
    """Agenda com o horário = 5 min antes de ``now``, no dia de hoje, em UTC."""
    from datetime import datetime, timezone
    slot_dt = datetime.fromtimestamp(now - 300, timezone.utc)
    cfg = {"ai_reactivation_enabled": True, "ai_reactivation_unit": "schedule",
           "ai_reactivation_weekdays": [slot_dt.weekday()],
           "ai_reactivation_time": slot_dt.strftime("%H:%M"),
           "ai_reactivation_timezone": "UTC"}
    cfg.update(kw)
    return cfg

def test_schedule_fires_once_per_slot():
    now = time.time()
    # evita a virada de minuto: o slot tem de estar dentro da tolerância
    c = _conv()
    _msg(c, "user", now - 5 * HOUR)
    deps = _deps(**_schedule_cfg(now))
    assert _run(deps, now=now) >= 1
    assert conversation_repo.get(c["id"])["ai_active"] == 1
    # ``last_run`` foi gravado no banco -> um restart no mesmo horário não repete
    assert float(config_repo.get("ai_reactivation_last_run", 0)) > 0
    again = _conv()
    _msg(again, "user", now - 5 * HOUR)
    assert _run(_deps(**_schedule_cfg(now)), now=now + 30) == 0
    assert conversation_repo.get(again["id"])["ai_active"] == 0

def test_schedule_outside_the_grace_does_nothing():
    now = time.time()
    c = _conv()
    _msg(c, "user", now - 5 * HOUR)
    cfg = _schedule_cfg(now)
    assert _run(_deps(**cfg), now=now + svc.SCHEDULE_GRACE + 600) == 0
    assert conversation_repo.get(c["id"])["ai_active"] == 0

def test_advance_if_older_lets_only_one_caller_claim_a_slot():
    key = "ai_reactivation_last_run"
    config_repo.set(key, 0.0)
    assert config_repo.advance_if_older(key, 100.0, 150.0) is True
    assert config_repo.advance_if_older(key, 100.0, 160.0) is False   # já servido
    assert config_repo.advance_if_older(key, 200.0, 250.0) is True    # slot novo
    assert float(config_repo.get(key)) == 250.0

# ── config: GET normalizado e PUT que descarta lixo ─────────────────────────

def test_provisioning_remembers_the_resolved_number_without_duplicates():
    from app.services import provisioning_service
    config_repo.set("provisioning_phones", [])
    provisioning_service._remember_provision_phone("5511911112222")
    provisioning_service._remember_provision_phone("5511911112222")
    provisioning_service._remember_provision_phone("5511933334444")
    assert config_repo.get("provisioning_phones") == ["5511911112222", "5511933334444"]
