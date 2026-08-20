"""Unidades de ``server.api_keys`` — geração, resolução, expiração, revogação.

Testável SEM servidor de propósito (é o motivo de a lógica da chave morar num
módulo puro, irmão de ``server/auth.py``). Só :func:`resolve_api_key` toca o
banco, e aqui ele é substituído por dublês — o que esta suíte trava é a REGRA,
não o SQL.
"""

from __future__ import annotations

import time

import pytest

from server import api_keys as keylib


def test_generate_key_shape():
    raw, prefix, key_hash = keylib.generate_key()
    assert raw.startswith("wsk_live_")
    assert prefix and prefix in raw
    # O SEGREDO nunca é o hash, e o hash nunca contém o segredo.
    assert key_hash != raw
    assert raw.split(".")[-1] not in key_hash


def test_generate_key_survives_the_base64url_alphabet():
    """O separador não pode pertencer ao alfabeto do segredo.

    ``token_urlsafe`` sorteia ``-`` e ``_``; enquanto ``_`` separava os campos,
    ~1 em 3 chaves nascia "malformada" e era recusada de forma aleatória. Este
    teste roda muitas vezes de propósito — o bug era probabilístico.
    """
    for _ in range(200):
        raw, prefix, _ = keylib.generate_key()
        assert keylib.split_key(raw) == (prefix, raw.split(".", 1)[1])


def test_generate_key_is_unique():
    a, _, _ = keylib.generate_key()
    b, _, _ = keylib.generate_key()
    assert a != b


@pytest.mark.parametrize("bad", [
    "", None, "wsk_live", "wsk_live_abc", "wsk_test_abc.def",
    "Bearer wsk_live_a.b", "wsk_live_.x", "wsk_live_x.",
])
def test_split_key_rejects_malformed(bad):
    assert keylib.split_key(bad) == (None, None)


def test_split_key_roundtrip():
    raw, prefix, _ = keylib.generate_key()
    got_prefix, secret = keylib.split_key(raw)
    assert got_prefix == prefix
    assert raw.endswith(secret)


def test_is_usable_states():
    now = time.time()
    assert keylib.is_usable({"revoked_at": None, "expires_at": None}, now=now)
    assert keylib.is_usable({"revoked_at": None, "expires_at": now + 60}, now=now)
    assert not keylib.is_usable({"revoked_at": now - 1, "expires_at": None}, now=now)
    assert not keylib.is_usable({"revoked_at": None, "expires_at": now - 1}, now=now)


def test_public_view_never_leaks_the_secret():
    raw, prefix, key_hash = keylib.generate_key()
    view = keylib.public_view({
        "id": 1, "user_id": 2, "label": "CRM", "prefix": prefix,
        "last4": keylib.last4(raw), "key_hash": key_hash,
        "created_at": 1.0, "expires_at": None, "revoked_at": None,
    })
    blob = repr(view)
    assert key_hash not in blob
    assert raw not in blob
    assert view["masked"].startswith("wsk_live_")


# ── resolve_api_key: a regra, com o banco dublado ───────────────────────────

class _FakeKeyRepo:
    def __init__(self, row):
        self.row = row
        self.touched = []

    def get_by_prefix(self, prefix):
        return self.row if self.row and self.row["prefix"] == prefix else None

    def touch_last_used(self, key_id):
        self.touched.append(key_id)


class _FakeUserRepo:
    def __init__(self, user):
        self.user = user

    def get(self, user_id):
        return self.user if self.user and self.user["id"] == user_id else None


@pytest.fixture
def wired(monkeypatch):
    """Substitui os dois repos que ``resolve_api_key`` importa preguiçosamente."""
    import db.repositories as repos

    def _wire(row, user):
        fake_keys, fake_users = _FakeKeyRepo(row), _FakeUserRepo(user)
        monkeypatch.setattr(repos, "api_key_repo", fake_keys, raising=False)
        monkeypatch.setattr(repos, "user_repo", fake_users, raising=False)
        return fake_keys

    return _wire


def _row(raw, prefix, key_hash, **over):
    row = {"id": 7, "user_id": 42, "prefix": prefix, "key_hash": key_hash,
           "revoked_at": None, "expires_at": None}
    row.update(over)
    return row


ACTIVE_USER = {"id": 42, "name": "API — CRM", "is_active": 1}


def test_resolve_happy_path(wired):
    keylib._verify_cache.clear()
    raw, prefix, key_hash = keylib.generate_key()
    fake = wired(_row(raw, prefix, key_hash), ACTIVE_USER)
    user, row = keylib.resolve_api_key(raw)
    assert user == ACTIVE_USER
    assert row["id"] == 7
    assert fake.touched == [7]     # last_used_at é best-effort mas acontece


def test_resolve_rejects_wrong_secret_with_right_prefix(wired):
    keylib._verify_cache.clear()
    raw, prefix, key_hash = keylib.generate_key()
    wired(_row(raw, prefix, key_hash), ACTIVE_USER)
    other, _, _ = keylib.generate_key()
    forged = f"wsk_live_{prefix}.{other.split('.')[-1]}"
    assert keylib.resolve_api_key(forged) == (None, None)


def test_resolve_rejects_revoked(wired):
    keylib._verify_cache.clear()
    raw, prefix, key_hash = keylib.generate_key()
    wired(_row(raw, prefix, key_hash, revoked_at=time.time() - 1), ACTIVE_USER)
    assert keylib.resolve_api_key(raw) == (None, None)


def test_resolve_rejects_expired(wired):
    keylib._verify_cache.clear()
    raw, prefix, key_hash = keylib.generate_key()
    wired(_row(raw, prefix, key_hash, expires_at=time.time() - 1), ACTIVE_USER)
    assert keylib.resolve_api_key(raw) == (None, None)


def test_resolve_rejects_inactive_owner(wired):
    keylib._verify_cache.clear()
    raw, prefix, key_hash = keylib.generate_key()
    wired(_row(raw, prefix, key_hash), {"id": 42, "name": "x", "is_active": 0})
    assert keylib.resolve_api_key(raw) == (None, None)


def test_resolve_rejects_unknown_prefix(wired):
    keylib._verify_cache.clear()
    raw, prefix, key_hash = keylib.generate_key()
    wired(_row(raw, prefix, key_hash), ACTIVE_USER)
    stranger, _, _ = keylib.generate_key()
    assert keylib.resolve_api_key(stranger) == (None, None)


def test_verify_cache_does_not_bypass_revocation(wired):
    """O cache guarda só o COMPARE do Argon2 — a autorização é relida sempre.

    Sem isso, revogar uma chave levaria até 60s para valer, que é exatamente o
    contrário do que "revogar" significa.
    """
    keylib._verify_cache.clear()
    raw, prefix, key_hash = keylib.generate_key()
    row = _row(raw, prefix, key_hash)
    wired(row, ACTIVE_USER)
    assert keylib.resolve_api_key(raw)[0] is not None   # popula o cache
    row["revoked_at"] = time.time()
    assert keylib.resolve_api_key(raw) == (None, None)  # e mesmo assim recusa
