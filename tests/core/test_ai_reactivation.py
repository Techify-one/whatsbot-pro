"""Plano 178 / Fase 3 — a regra PURA de religar a IA automaticamente.

Sem banco, sem relógio próprio, sem laço: tudo recebe ``now`` e ``tz`` por argumento.

    venv/bin/python -m pytest tests/core/test_ai_reactivation.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services import ai_reactivation_service as svc
from app.services.ai_reactivation_service import INVALID

BRT = ZoneInfo("America/Sao_Paulo")
UTC = timezone.utc

def _ts(y, mo, d, h=0, mi=0, tz=UTC) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()

# ── clean ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "ai_reactivation_enabled", "ai_reactivation_include_groups_archived"])
def test_bool_keys_are_strict(key):
    assert svc.clean(key, True) is True
    assert svc.clean(key, False) is False
    for junk in ("true", 1, 0, None, "", [], {}):
        assert svc.clean(key, junk) is INVALID

def test_unit_must_be_in_the_list():
    for ok in ("minutes", "hours", "days", "schedule"):
        assert svc.clean("ai_reactivation_unit", ok) == ok
    for junk in ("weeks", "", None, 3, "HOURS"):
        assert svc.clean("ai_reactivation_unit", junk) is INVALID

def test_last_sender_must_be_in_the_list():
    for ok in ("both", "customer", "attendant"):
        assert svc.clean("ai_reactivation_last_sender", ok) == ok
    assert svc.clean("ai_reactivation_last_sender", "ai") is INVALID

def test_value_is_clamped_not_rejected():
    clean = lambda v: svc.clean("ai_reactivation_value", v)  # noqa: E731
    assert clean(24) == 24
    assert clean("7") == 7
    assert clean(0) == 1
    assert clean(-5) == 1
    assert clean(10**9) == svc.MAX_VALUE
    assert clean(3.9) == 3

def test_value_rejects_non_numbers_and_bools():
    for junk in ("abc", None, [], {}, True, False):
        assert svc.clean("ai_reactivation_value", junk) is INVALID

def test_weekdays_discard_out_of_range_and_dedupe():
    clean = lambda v: svc.clean("ai_reactivation_weekdays", v)  # noqa: E731
    assert clean([4, 0, 0, 2]) == [0, 2, 4]
    assert clean([-1, 7, 3, 99]) == [3]
    assert clean(["1", 2, True, None, 2.0]) == [2]
    assert clean([]) == []
    assert clean("0,1") is INVALID
    assert clean(None) is INVALID

def test_time_is_hh_mm_24h():
    clean = lambda v: svc.clean("ai_reactivation_time", v)  # noqa: E731
    assert clean("08:00") == "08:00"
    assert clean("23:59") == "23:59"
    assert clean("00:00") == "00:00"
    for junk in ("25:00", "24:00", "8:00", "08:60", "08-00", "", None, 800):
        assert clean(junk) is INVALID

def test_timezone_is_an_iana_shaped_name_or_empty():
    clean = lambda v: svc.clean("ai_reactivation_timezone", v)  # noqa: E731
    assert clean("") == ""
    assert clean("America/Sao_Paulo") == "America/Sao_Paulo"
    assert clean("UTC") == "UTC"
    assert clean("America/Argentina/Buenos_Aires") == "America/Argentina/Buenos_Aires"
    for junk in ("../etc/passwd", "a b", "x" * 80, "/Sao_Paulo", None, 3, "A/B/C/D"):
        assert clean(junk) is INVALID

def test_last_run_and_unknown_keys_are_not_editable():
    assert svc.clean("ai_reactivation_last_run", 123.0) is INVALID
    assert svc.clean("ai_reactivation_nope", True) is INVALID

# ── read_config ─────────────────────────────────────────────────────────────

def test_read_config_defaults():
    cfg = svc.read_config({})
    assert cfg == {
        "enabled": False, "unit": "hours", "value": 24,
        "weekdays": [0, 1, 2, 3, 4], "time": "08:00", "last_sender": "both",
        "include_groups_archived": False, "timezone": "",
    }

def test_read_config_falls_back_per_key_on_junk():
    cfg = svc.read_config({
        "ai_reactivation_enabled": "yes",        # junk -> default False
        "ai_reactivation_unit": "days",          # good
        "ai_reactivation_value": 0,              # clamped to 1
        "ai_reactivation_time": "99:99",         # junk -> default
    })
    assert cfg["enabled"] is False
    assert cfg["unit"] == "days"
    assert cfg["value"] == 1
    assert cfg["time"] == "08:00"

# ── owner_allows (truth table) ──────────────────────────────────────────────

@pytest.mark.parametrize("owner,role,expected", [
    ("both", "user", True), ("both", "assistant", True), ("both", None, True),
    ("customer", "user", True), ("customer", "assistant", False),
    ("customer", None, False),
    ("attendant", "assistant", True), ("attendant", "user", False),
    ("attendant", None, False),
])
def test_owner_allows(owner, role, expected):
    assert svc.owner_allows(owner, role) is expected

# ── reference_ts ────────────────────────────────────────────────────────────

def test_reference_is_the_later_of_last_message_and_ai_off():
    assert svc.reference_ts({"last_ts": 100.0, "ai_disabled_at": 500.0}) == 500.0
    assert svc.reference_ts({"last_ts": 900.0, "ai_disabled_at": 500.0}) == 900.0

def test_reference_without_ai_disabled_at_falls_back_to_last_message():
    """Conversa legada (NULL) ou liberada só por responsável humano."""
    assert svc.reference_ts({"last_ts": 100.0, "ai_disabled_at": None}) == 100.0
    assert svc.reference_ts({"last_ts": 100.0}) == 100.0

def test_reference_without_any_human_message_uses_ai_off_then_creation():
    assert svc.reference_ts({"last_ts": None, "ai_disabled_at": 50.0,
                             "created_at": 10.0}) == 50.0
    assert svc.reference_ts({"last_ts": None, "ai_disabled_at": None,
                             "created_at": 10.0}) == 10.0
    assert svc.reference_ts({}) == 0.0

# ── latest_slot ─────────────────────────────────────────────────────────────

def test_latest_slot_today_when_the_time_has_passed():
    # Wed 2026-10-21 10:00 BRT; rule Mon-Fri 08:00 -> today 08:00.
    now = _ts(2026, 10, 21, 10, 0, BRT)
    assert svc.latest_slot([0, 1, 2, 3, 4], "08:00", now, BRT) == _ts(2026, 10, 21, 8, 0, BRT)

def test_latest_slot_yesterday_when_the_time_has_not_come_yet():
    now = _ts(2026, 10, 21, 7, 59, BRT)  # Wed, before 08:00
    assert svc.latest_slot([0, 1, 2, 3, 4], "08:00", now, BRT) == _ts(2026, 10, 20, 8, 0, BRT)

def test_latest_slot_crosses_the_weekend():
    now = _ts(2026, 10, 26, 7, 0, BRT)  # Mon 07:00 -> last slot is Fri 23rd 08:00
    assert svc.latest_slot([0, 1, 2, 3, 4], "08:00", now, BRT) == _ts(2026, 10, 23, 8, 0, BRT)

def test_latest_slot_single_weekday_looks_back_a_week():
    now = _ts(2026, 10, 21, 12, 0, BRT)  # Wed
    # Only Wednesdays, at 13:00: today's has not come -> a week ago.
    assert svc.latest_slot([2], "13:00", now, BRT) == _ts(2026, 10, 14, 13, 0, BRT)

def test_latest_slot_is_inclusive_at_the_exact_minute():
    now = _ts(2026, 10, 21, 8, 0, BRT)
    assert svc.latest_slot([2], "08:00", now, BRT) == now

def test_latest_slot_none_without_weekdays():
    assert svc.latest_slot([], "08:00", _ts(2026, 10, 21, 10, 0, BRT), BRT) is None

def test_latest_slot_reads_the_clock_in_the_given_zone():
    """11:30 UTC is 08:30 in BRT (UTC-3): the 08:00 BRT slot of the SAME day."""
    now = _ts(2026, 10, 21, 11, 30, UTC)
    assert svc.latest_slot([2], "08:00", now, BRT) == _ts(2026, 10, 21, 8, 0, BRT)
    # In UTC the same instant is 11:30, so the 08:00 slot is 3.5h earlier.
    assert svc.latest_slot([2], "08:00", now, UTC) == _ts(2026, 10, 21, 8, 0, UTC)

# ── schedule_due ────────────────────────────────────────────────────────────

CFG = {"weekdays": [0, 1, 2, 3, 4], "time": "08:00"}

def test_schedule_due_inside_the_grace_returns_the_slot():
    slot = _ts(2026, 10, 21, 8, 0, BRT)
    now = slot + 600
    assert svc.schedule_due(CFG, now, 0.0, BRT) == slot

def test_schedule_due_at_the_edge_of_the_grace():
    slot = _ts(2026, 10, 21, 8, 0, BRT)
    assert svc.schedule_due(CFG, slot + svc.SCHEDULE_GRACE, 0.0, BRT) == slot
    assert svc.schedule_due(CFG, slot + svc.SCHEDULE_GRACE + 1, 0.0, BRT) is None

def test_schedule_due_not_before_the_slot():
    """Quarta 07:59: o último horário é o de terça 08:00, 24h atrás — fora da tolerância."""
    assert svc.schedule_due(CFG, _ts(2026, 10, 21, 7, 59, BRT), 0.0, BRT) is None

def test_schedule_due_not_twice_for_the_same_slot():
    """``last_run`` já gravado depois do slot: um restart na hora de tolerância não
    dispara de novo."""
    slot = _ts(2026, 10, 21, 8, 0, BRT)
    assert svc.schedule_due(CFG, slot + 300, slot + 120, BRT) is None

def test_schedule_due_late_in_the_day_is_not_served():
    """23:00 não serve o slot das 08:00 — passou da tolerância."""
    assert svc.schedule_due(CFG, _ts(2026, 10, 21, 23, 0, BRT), 0.0, BRT) is None

def test_schedule_due_off_day():
    # Sat 2026-10-24 08:10, rule Mon-Fri: last slot is Fri -> far beyond the grace.
    assert svc.schedule_due(CFG, _ts(2026, 10, 24, 8, 10, BRT), 0.0, BRT) is None

# ── pick_rows ───────────────────────────────────────────────────────────────

NOW = 1_000_000.0

def _row(rid, *, last_ts=None, ai_off=None, role="user", created=1.0):
    return {"id": rid, "last_ts": last_ts, "ai_disabled_at": ai_off,
            "last_role": role, "created_at": created}

def _cfg(**kw):
    base = {"unit": "hours", "value": 2, "last_sender": "both"}
    base.update(kw)
    return base

def test_time_rule_needs_the_full_wait():
    rows = [_row(1, last_ts=NOW - 7200),        # exactly 2h -> due
            _row(2, last_ts=NOW - 7199),        # 1s short
            _row(3, last_ts=NOW - 86400)]
    assert [r["id"] for r in svc.pick_rows(rows, _cfg(), NOW)] == [3, 1]

def test_time_rule_counts_from_when_the_ai_went_off():
    """Pausada à mão há 1 min com última mensagem de 3 dias: NÃO religa na hora."""
    rows = [_row(1, last_ts=NOW - 3 * 86400, ai_off=NOW - 60)]
    assert svc.pick_rows(rows, _cfg(unit="minutes", value=30), NOW) == []
    assert len(svc.pick_rows(rows, _cfg(unit="minutes", value=1), NOW)) == 1

def test_a_new_customer_message_restarts_the_wait():
    rows = [_row(1, last_ts=NOW - 60, ai_off=NOW - 86400)]
    assert svc.pick_rows(rows, _cfg(), NOW) == []

def test_units():
    rows = [_row(1, last_ts=NOW - 90 * 60)]  # 1.5h
    assert svc.pick_rows(rows, _cfg(unit="minutes", value=90), NOW)
    assert not svc.pick_rows(rows, _cfg(unit="minutes", value=91), NOW)
    assert svc.pick_rows(rows, _cfg(unit="hours", value=1), NOW)
    assert not svc.pick_rows(rows, _cfg(unit="days", value=1), NOW)

def test_sender_filter_applies_to_both_rules():
    rows = [_row(1, last_ts=NOW - 10**5, role="user"),
            _row(2, last_ts=NOW - 10**5, role="assistant"),
            _row(3, last_ts=None, ai_off=NOW - 10**5, role=None)]
    ids = lambda cfg: sorted(r["id"] for r in svc.pick_rows(rows, cfg, NOW))  # noqa: E731
    assert ids(_cfg(last_sender="customer")) == [1]
    assert ids(_cfg(last_sender="attendant")) == [2]
    assert ids(_cfg(last_sender="both")) == [1, 2, 3]
    assert ids(_cfg(unit="schedule", last_sender="customer")) == [1]
    assert ids(_cfg(unit="schedule", last_sender="both")) == [1, 2, 3]

def test_schedule_rule_ignores_how_recent_the_conversation_is():
    rows = [_row(1, last_ts=NOW - 5)]
    assert len(svc.pick_rows(rows, _cfg(unit="schedule"), NOW)) == 1

def test_oldest_wait_first():
    rows = [_row(1, last_ts=NOW - 100), _row(2, last_ts=NOW - 900),
            _row(3, last_ts=NOW - 500)]
    assert [r["id"] for r in svc.pick_rows(rows, _cfg(unit="schedule"), NOW)] == [2, 3, 1]

# ── resolve_tz ──────────────────────────────────────────────────────────────

def test_resolve_tz_known_name():
    assert svc.resolve_tz("America/Sao_Paulo").key == "America/Sao_Paulo"

def test_resolve_tz_unknown_falls_back_without_raising():
    assert svc.resolve_tz("Not/AZone") is not None
    assert svc.resolve_tz("") is not None
