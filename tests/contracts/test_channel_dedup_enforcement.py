"""DB-backed tests for the account-identity sweep + refuse (plano 32 F4).

Exercises ``app.services.channel_identity.sweep_channel`` directly (the sweep loop
never runs under the hermetic TestClient), against the Postgres test DB. The
create/update 409 enforcement is covered end-to-end in ``test_endpoints.py`` (F8).
"""
from __future__ import annotations

import pytest

from app.services import channel_identity as ci
from db.repositories import channel_repo


class _FakeInst:
    def __init__(self, st: dict, identity):
        self._st = st
        self._identity = identity
        self.rejected = False

    def status(self):
        return self._st

    def account_identity(self):
        return self._identity

    def reject_duplicate(self):
        self.rejected = True


def _mk(cid: str, **status):
    channel_repo.create(id=cid, provider="gowa", display_name=cid)
    if status:
        channel_repo.set_status(cid, **status)


@pytest.fixture(autouse=True)
def _clean(_engine_ready):
    # Isolate each test: drop any channel rows created here (keep 'default').
    yield
    for row in channel_repo.list_all(include_archived=True):
        if row["id"].startswith("p32_"):
            channel_repo.delete(row["id"])


from channels.base import AccountIdentity


def test_sweep_persists_identity_when_changed():
    _mk("p32_a")
    inst = _FakeInst({"connected": True, "logged_in": True, "own_phone": "5511999990001"},
                     AccountIdentity("phone", "551199990001"))
    ci.sweep_channel("p32_a", inst)
    row = channel_repo.get("p32_a")
    assert row["account_identity"] == "551199990001"
    assert row["account_identity_kind"] == "phone"
    assert row["logged_in"] == 1
    assert row["own_phone"] == "5511999990001"
    assert inst.rejected is False


def test_sweep_refuses_conflict():
    # p32_owner already owns the identity; p32_dup pairs to the same one.
    _mk("p32_owner")
    channel_repo.set_status("p32_owner", account_identity="551199990001",
                            account_identity_kind="phone", logged_in=1)
    _mk("p32_dup")
    inst = _FakeInst({"connected": True, "logged_in": True, "own_phone": "5511999990001"},
                     AccountIdentity("phone", "551199990001"))
    ci.sweep_channel("p32_dup", inst)
    row = channel_repo.get("p32_dup")
    assert inst.rejected is True                       # session severed
    assert row["account_identity"] in (None, "")       # identity NOT persisted
    assert row["logged_in"] == 0                        # marked logged out
    assert "p32_owner" in (row["last_error"] or "")     # names the conflict
    assert row["enabled"] == 1                          # non-destructive


def test_sweep_no_conflict_for_self_reidentify():
    # Re-sweeping the SAME channel with its own identity must not refuse itself.
    _mk("p32_self")
    channel_repo.set_status("p32_self", account_identity="551199990001",
                            account_identity_kind="phone")
    inst = _FakeInst({"connected": True, "logged_in": True},
                     AccountIdentity("phone", "551199990001"))
    ci.sweep_channel("p32_self", inst)
    row = channel_repo.get("p32_self")
    assert inst.rejected is False
    assert row["account_identity"] == "551199990001"


def test_sweep_none_identity_is_noop_on_identity():
    _mk("p32_none")
    inst = _FakeInst({"connected": True, "logged_in": False}, None)
    ci.sweep_channel("p32_none", inst)
    row = channel_repo.get("p32_none")
    assert row["account_identity"] in (None, "")
    assert inst.rejected is False


# ── Create/update guard (credential identity → 409 decision) ────────────────
from app.services import channel_service as cs
from tests.plugin_test_utils import load_plugin_module


class _FakeRegistry:
    def __init__(self, mapping):
        self._m = mapping

    def get_provider(self, name):
        return self._m.get(name)


class _FakeDeps:
    def __init__(self, registry):
        self.channel_registry = registry


def _cloud_cls():
    m = load_plugin_module(
        "whatsapp_cloud", "channels", package_name="channel_dedup_cloud",
    )
    return m.WhatsAppCloudChannel


def test_guard_raises_on_duplicate_credentials():
    deps = _FakeDeps(_FakeRegistry({"whatsapp_cloud": _cloud_cls()}))
    channel_repo.create(id="p32_cloud1", provider="whatsapp_cloud", display_name="c1")
    channel_repo.set_status("p32_cloud1", account_identity="PNID99",
                            account_identity_kind="phone_number_id")
    with pytest.raises(cs.DuplicateChannelError) as ei:
        cs._guard_duplicate(deps, "whatsapp_cloud", {"phone_number_id": "PNID99"},
                            exclude_channel_id=None)
    assert ei.value.existing_channel_id == "p32_cloud1"


def test_guard_allows_unique_and_returns_identity():
    deps = _FakeDeps(_FakeRegistry({"whatsapp_cloud": _cloud_cls()}))
    ident = cs._guard_duplicate(deps, "whatsapp_cloud", {"phone_number_id": "FRESH1"},
                                exclude_channel_id=None)
    assert ident is not None and ident.value == "FRESH1"


def test_guard_excludes_self_on_update():
    deps = _FakeDeps(_FakeRegistry({"whatsapp_cloud": _cloud_cls()}))
    channel_repo.create(id="p32_cloud2", provider="whatsapp_cloud", display_name="c2")
    channel_repo.set_status("p32_cloud2", account_identity="SELF1",
                            account_identity_kind="phone_number_id")
    # editing p32_cloud2 to its OWN identity must not 409
    ident = cs._guard_duplicate(deps, "whatsapp_cloud", {"phone_number_id": "SELF1"},
                                exclude_channel_id="p32_cloud2")
    assert ident.value == "SELF1"


# ── Partial unique index (the DB backstop) ──────────────────────────────────
from sqlalchemy.exc import IntegrityError


def test_index_rejects_two_enabled_same_identity():
    _mk("p32_ix1")
    channel_repo.set_status("p32_ix1", account_identity="IX", account_identity_kind="phone")
    _mk("p32_ix2")
    with pytest.raises(IntegrityError):
        channel_repo.set_status("p32_ix2", account_identity="IX",
                                account_identity_kind="phone")


def test_index_allows_archived_and_disabled():
    _mk("p32_ix3")
    channel_repo.set_status("p32_ix3", account_identity="IX2", account_identity_kind="phone")
    # archived and disabled rows are outside the partial index → no violation.
    _mk("p32_ix4")
    channel_repo.set_status("p32_ix4", archived=1, account_identity="IX2",
                            account_identity_kind="phone")
    _mk("p32_ix5")
    channel_repo.set_status("p32_ix5", enabled=0, account_identity="IX2",
                            account_identity_kind="phone")
    assert channel_repo.get("p32_ix4")["account_identity"] == "IX2"
    assert channel_repo.get("p32_ix5")["account_identity"] == "IX2"


def test_index_allows_cross_provider_same_identity():
    _mk("p32_ix6")
    channel_repo.set_status("p32_ix6", account_identity="IX3", account_identity_kind="phone")
    channel_repo.create(id="p32_ix7", provider="whatsapp_cloud", display_name="x")
    channel_repo.set_status("p32_ix7", account_identity="IX3",
                            account_identity_kind="phone")
    assert channel_repo.get("p32_ix7")["account_identity"] == "IX3"
