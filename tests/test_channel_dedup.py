"""Unit tests for the generic dedup engine + AccountIdentity (plano 32 F2).

``find_conflict`` reads channels via ``channel_repo.list_all``; here it is
monkeypatched so the engine is exercised in isolation (no DB). The DB-backed
unique-index behaviour is covered separately in the endpoint suite (F8).
"""
from __future__ import annotations

import channels.dedup as dedup
from channels.base import AccountIdentity


# ── same() ────────────────────────────────────────────────────────────────
def test_same_equal_canonical_matches():
    assert dedup.same(AccountIdentity("phone", "5511999990001"),
                      AccountIdentity("phone", "5511999990001")) is True


def test_same_none_never_matches():
    assert dedup.same(None, AccountIdentity("phone", "x")) is False
    assert dedup.same(AccountIdentity("phone", "x"), None) is False
    assert dedup.same(None, None) is False


def test_same_empty_value_never_matches():
    assert dedup.same(AccountIdentity("phone", ""),
                      AccountIdentity("phone", "")) is False


def test_same_different_kind_never_matches():
    assert dedup.same(AccountIdentity("phone", "123"),
                      AccountIdentity("bot_id", "123")) is False


# ── find_conflict() ───────────────────────────────────────────────────────
def _rows(*rows):
    return list(rows)


def _row(id, provider="gowa", enabled=1, archived=0,
         identity=None, kind=None):
    return {"id": id, "provider": provider, "enabled": enabled,
            "archived": archived, "account_identity": identity,
            "account_identity_kind": kind}


def _patch_rows(monkeypatch, rows):
    from db.repositories import channel_repo
    monkeypatch.setattr(channel_repo, "list_all",
                        lambda include_archived=False: rows)


def test_find_conflict_hit(monkeypatch):
    _patch_rows(monkeypatch, _rows(
        _row("chA", identity="5511999990001", kind="phone")))
    got = dedup.find_conflict("gowa", AccountIdentity("phone", "5511999990001"),
                              exclude_channel_id="chB")
    assert got == "chA"


def test_find_conflict_excludes_self(monkeypatch):
    _patch_rows(monkeypatch, _rows(
        _row("chA", identity="5511999990001", kind="phone")))
    assert dedup.find_conflict("gowa", AccountIdentity("phone", "5511999990001"),
                               exclude_channel_id="chA") is None


def test_find_conflict_different_provider(monkeypatch):
    _patch_rows(monkeypatch, _rows(
        _row("chA", provider="telegram", identity="5511999990001", kind="phone")))
    assert dedup.find_conflict("gowa", AccountIdentity("phone", "5511999990001"),
                               exclude_channel_id="chB") is None


def test_find_conflict_disabled_and_null_ignored(monkeypatch):
    _patch_rows(monkeypatch, _rows(
        _row("chDisabled", enabled=0, identity="5511999990001", kind="phone"),
        _row("chNull", identity=None, kind=None),
        _row("chEmpty", identity="", kind=""),
    ))
    assert dedup.find_conflict("gowa", AccountIdentity("phone", "5511999990001"),
                               exclude_channel_id="chB") is None


def test_find_conflict_none_identity(monkeypatch):
    _patch_rows(monkeypatch, _rows(
        _row("chA", identity="5511999990001", kind="phone")))
    assert dedup.find_conflict("gowa", None, exclude_channel_id="chB") is None


def test_find_conflict_kind_mismatch(monkeypatch):
    _patch_rows(monkeypatch, _rows(
        _row("chA", identity="123", kind="bot_token")))
    assert dedup.find_conflict("telegram", AccountIdentity("bot_id", "123"),
                               exclude_channel_id="chB") is None
