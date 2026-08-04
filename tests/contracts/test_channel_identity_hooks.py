"""Unit tests for the per-provider account-identity hooks (plano 32 F5).

Each provider declares its own identity; the core stays generic. No DB / no
network — cloud/telegram read in-memory credentials, GOWA uses a stub client.
"""
from __future__ import annotations

from channels.base import AccountIdentity, Channel
from channels.providers.gowa_channel import GOWAChannel, _canonical_phone
from tests.plugin_test_utils import load_plugin_module


# ── GOWA (live, kind="phone", canonical BR) ────────────────────────────────
class _StubClient:
    def __init__(self, number: str):
        self._number = number

    def get_own_number(self) -> str:
        return self._number


def test_gowa_account_identity_canonical_phone():
    ch = GOWAChannel("g1", gowa_client=_StubClient("5511987654321"))
    ident = ch.account_identity()
    assert ident == AccountIdentity("phone", _canonical_phone("5511987654321"))
    assert ident.kind == "phone"


def test_gowa_12_and_13_digit_forms_collapse():
    """Same BR line reported as 12- or 13-digit must yield the same dedup key."""
    a = GOWAChannel("g1", gowa_client=_StubClient("5511987654321")).account_identity()
    b = GOWAChannel("g2", gowa_client=_StubClient("551187654321")).account_identity()
    assert a == b


def test_gowa_empty_number_is_none():
    assert GOWAChannel("g1", gowa_client=_StubClient("")).account_identity() is None


def test_gowa_identity_from_credentials_is_none():
    # GOWA has no identity at create time (only after QR).
    assert GOWAChannel.identity_from_credentials({}) is None


# ── WhatsApp Cloud (create-time, kind="phone_number_id") ───────────────────
def _cloud_cls():
    mod = load_plugin_module(
        "whatsapp_cloud", "channels", package_name="channel_identity_cloud",
    )
    return mod.WhatsAppCloudChannel


def test_cloud_identity_from_credentials():
    cls = _cloud_cls()
    assert cls.identity_from_credentials({"phone_number_id": " 123456789 "}) == \
        AccountIdentity("phone_number_id", "123456789")


def test_cloud_identity_missing_is_none():
    cls = _cloud_cls()
    assert cls.identity_from_credentials({}) is None
    assert cls.identity_from_credentials({"phone_number_id": "   "}) is None


# ── Telegram (bot_id from token, consistent kind) ──────────────────────────
def _tg_cls():
    mod = load_plugin_module(
        "telegram", "channels", package_name="channel_identity_telegram",
    )
    return mod.TelegramChannel


def test_telegram_identity_from_token_bot_id():
    cls = _tg_cls()
    ident = cls.identity_from_credentials({"bot_token": "123456789:AAF-abcDEF"})
    assert ident == AccountIdentity("bot_id", "123456789")


def test_telegram_live_and_create_same_kind_and_value():
    cls = _tg_cls()
    creds = {"bot_token": "987654321:XYZ"}
    live = cls("t1", credentials=creds).account_identity()
    create = cls.identity_from_credentials(creds)
    assert live == create == AccountIdentity("bot_id", "987654321")


def test_telegram_malformed_token_is_none():
    cls = _tg_cls()
    assert cls.identity_from_credentials({"bot_token": "no-colon-here"}) is None
    assert cls.identity_from_credentials({"bot_token": ""}) is None


# ── test provider (opts out → None) ────────────────────────────────────────
class _FakeChan(Channel):
    provider = "test"

    def status(self):  # pragma: no cover - trivial
        return {}

    def send_text(self, *a, **k):  # pragma: no cover
        return None

    def send_media(self, *a, **k):  # pragma: no cover
        return None

    def parse_inbound(self, raw):  # pragma: no cover
        return []


def test_test_provider_has_no_identity():
    ch = _FakeChan("x")
    assert ch.account_identity() is None
    assert _FakeChan.identity_from_credentials({}) is None
