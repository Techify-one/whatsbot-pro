"""Plano 178 / Fase 1 — ``atendimentos.ai_disabled_at``.

A coluna é a âncora da espera da religação automática da IA: "desde quando a IA está
parada NESTA conversa". Os três escritores de ``ai_active`` têm de carimbá-la pela
MESMA regra (1→0 carimba, 0→1 limpa, 0→0 mantém):

* ``conversation_repo._update`` (cobre ``set_ai_active``, ``assign_agent``, a tool
  ``transfer_to_human``, o provisionamento e os plugins);
* ``conversation_repo._insert_conversation`` (conversa que NASCE com a IA desligada);
* ``team_routing_repo._write_conversation`` (roteamento por time).

    venv/bin/python -m pytest tests/integration/test_ai_disabled_at.py -q
"""

from __future__ import annotations

import time
import uuid

import pytest

from db.engine import get_engine
from db.repositories import config_repo, contact_repo, contact_inbox_repo, conversation_repo
from db.tables import team_members, teams, users, inbox_members

INBOX_ID = 1  # seeded by migration 0013

def _new_conv(*, ai_active: int | None = None) -> dict:
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    return conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"],
        ai_active=ai_active)

def _stamp(conv_id: int):
    return conversation_repo.get(conv_id)["ai_disabled_at"]

@pytest.fixture(autouse=True)
def _db(_engine_ready):
    """Hermético quanto ao interruptor GLOBAL ``auto_reply``.

    ``_insert_conversation`` força ``ai_active=0`` quando ele está desligado ("nenhuma IA
    vai responder, então a conversa nasce sem IA"), e outros testes o deixam em ``False``
    no banco global — sem isto, ``create(ai_active=1)`` nasce desligada e estes testes
    passam ou falham conforme a ORDEM da suíte. Restaura o valor anterior no fim."""
    previous = config_repo.get("auto_reply", None)
    config_repo.set("auto_reply", True)
    yield
    if previous is None:
        config_repo.set("auto_reply", True)
    else:
        config_repo.set("auto_reply", previous)

@pytest.fixture
def _cleanup():
    """Remove o que os testes de roteamento por time criam.

    ⚠️ Não é cosmético: com QUALQUER usuário no banco a API deixa de ser "aberta" e todo
    teste posterior sem login recebe 401. O banco de teste é global ao processo, então um
    usuário esquecido aqui derruba dezenas de testes de outros arquivos."""
    users_made: list[int] = []
    teams_made: list[int] = []
    yield users_made, teams_made
    with get_engine().begin() as conn:
        if users_made:
            conn.execute(users.delete().where(users.c.id.in_(users_made)))
        if teams_made:
            conn.execute(teams.delete().where(teams.c.id.in_(teams_made)))

# ── _update: pausar / religar ───────────────────────────────────────────────

def test_born_with_ai_on_has_no_stamp():
    conv = _new_conv(ai_active=1)
    assert conv["ai_active"] == 1
    assert _stamp(conv["id"]) is None

def test_switching_off_stamps_now():
    conv = _new_conv(ai_active=1)
    before = time.time()
    conversation_repo.set_ai_active(conv["id"], 0)
    ts = _stamp(conv["id"])
    assert ts is not None and before - 1 <= ts <= time.time() + 1

def test_switching_on_clears_the_stamp():
    conv = _new_conv(ai_active=1)
    conversation_repo.set_ai_active(conv["id"], 0)
    assert _stamp(conv["id"]) is not None
    conversation_repo.set_ai_active(conv["id"], 1)
    assert _stamp(conv["id"]) is None

def test_switching_off_twice_does_not_restart_the_wait(monkeypatch):
    """0→0 mantém o carimbo: pausar de novo não pode reiniciar a contagem."""
    conv = _new_conv(ai_active=1)
    conversation_repo.set_ai_active(conv["id"], 0)
    first = _stamp(conv["id"])
    time.sleep(0.05)
    conversation_repo.set_ai_active(conv["id"], 0)
    assert _stamp(conv["id"]) == first

def test_switching_on_twice_stays_clear():
    conv = _new_conv(ai_active=1)
    conversation_repo.set_ai_active(conv["id"], 1)
    conversation_repo.set_ai_active(conv["id"], 1)
    assert _stamp(conv["id"]) is None

def test_update_without_ai_active_leaves_the_stamp_alone():
    conv = _new_conv(ai_active=1)
    conversation_repo.set_ai_active(conv["id"], 0)
    first = _stamp(conv["id"])
    conversation_repo.set_agent(conv["id"], "algum_agente")
    conversation_repo.set_assignee(conv["id"], 7)
    assert _stamp(conv["id"]) == first

def test_assign_agent_stamps_when_it_turns_the_ai_off():
    """O caminho de ``transfer_to_human`` e de ``conversation_service._transfer``."""
    conv = _new_conv(ai_active=1)
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=3, active_agent_key=None, ai_active=0)
    assert _stamp(conv["id"]) is not None

def test_assign_agent_with_ai_active_none_does_not_touch_the_stamp():
    conv = _new_conv(ai_active=1)
    conversation_repo.set_ai_active(conv["id"], 0)
    first = _stamp(conv["id"])
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=3, active_agent_key=None, ai_active=None)
    assert _stamp(conv["id"]) == first

# ── INSERT: conversa que nasce desligada ────────────────────────────────────

def test_born_with_ai_off_is_stamped_at_creation():
    before = time.time()
    conv = _new_conv(ai_active=0)
    assert conv["ai_active"] == 0
    ts = _stamp(conv["id"])
    assert ts is not None and before - 1 <= ts <= time.time() + 1

# ── roteamento por time ─────────────────────────────────────────────────────

def _make_team(mode: str, **extra) -> int:
    with get_engine().begin() as conn:
        row = conn.execute(teams.insert().values(
            name=f"t178-{uuid.uuid4().hex[:8]}", routing_mode=mode, is_active=1,
            created_at=time.time(), updated_at=time.time(), **extra
        ).returning(teams.c.id)).first()
    return int(row[0])

def _make_member(team_id: int) -> int:
    with get_engine().begin() as conn:
        uid = int(conn.execute(users.insert().values(
            email=f"u{uuid.uuid4().hex[:10]}@t.local", name="u178",
            password_hash="x", is_active=1,
            created_at=time.time(), updated_at=time.time(),
        ).returning(users.c.id)).scalar())
        conn.execute(team_members.insert().values(
            team_id=team_id, user_id=uid, created_at=time.time()))
        conn.execute(inbox_members.insert().values(
            inbox_id=INBOX_ID, user_id=uid, created_at=time.time()))
    return uid

def test_team_routing_to_a_human_stamps_when_it_turns_the_ai_off(_cleanup):
    from db.repositories import team_routing_repo
    team_id = _make_team("round_robin")
    _cleanup[1].append(team_id)
    _cleanup[0].append(_make_member(team_id))
    conv = _new_conv(ai_active=1)
    assert _stamp(conv["id"]) is None
    result = team_routing_repo.route_to_team(conv["id"], team_id)
    assert result.after.ai_active is False
    assert _stamp(conv["id"]) is not None

def test_team_routing_keeps_the_stamp_when_the_ai_was_already_off(_cleanup):
    from db.repositories import team_routing_repo
    team_id = _make_team("round_robin")
    _cleanup[1].append(team_id)
    _cleanup[0].append(_make_member(team_id))
    conv = _new_conv(ai_active=0)
    first = _stamp(conv["id"])
    time.sleep(0.05)
    team_routing_repo.route_to_team(conv["id"], team_id)
    assert _stamp(conv["id"]) == first
