"""Unidades do cache-folha de ``allowed_jid_types`` (plano 173 Fase 1).

``channels.jid_allowed`` precisa de duas garantias, sem banco nem rede:

  * ``peek`` NUNCA faz I/O — cache frio/expirado devolve ``None`` (o chamador
    tem de abrir mão, nunca descartar);
  * ``get_sync`` preenche o cache com UMA leitura e ``reset`` o esvazia.

O fallback (canal sem a chave salva -> ``DEFAULT_ALLOWED_JID_TYPES``, COM
``group``) é o mesmo do cache antigo em ``message_ingest_service`` — plano 103
D2 proíbe trocá-lo (seria retroativo, calaria grupo em canal legado).
"""

from __future__ import annotations

import json

import pytest

from channels import jid as jid_classifier
from channels import jid_allowed


@pytest.fixture(autouse=True)
def _clean_cache():
    jid_allowed.reset()
    yield
    jid_allowed.reset()


class _ExplodingChannelRepo:
    """Stand-in for ``db.repositories.channel_repo`` that fails the test if
    touched — proves ``peek`` never does I/O."""

    def get(self, channel_id):  # noqa: D401
        raise AssertionError("peek() não pode tocar o banco (channel_repo.get)")


def test_peek_cold_never_touches_db(monkeypatch):
    import db.repositories.channel_repo as real_channel_repo

    monkeypatch.setattr(real_channel_repo, "get", _ExplodingChannelRepo().get)
    assert jid_allowed.peek("canal-frio") is None


def test_get_sync_fills_cache_then_peek_hits_memory(monkeypatch):
    import db.repositories.channel_repo as real_channel_repo

    calls = []

    def _fake_get(channel_id):
        calls.append(channel_id)
        return {"config": json.dumps({"allowed_jid_types": ["person", "group"]})}

    monkeypatch.setattr(real_channel_repo, "get", _fake_get)

    types = jid_allowed.get_sync("canal-1")
    assert types == ["person", "group"]
    assert calls == ["canal-1"], "get_sync deve ler o banco na 1ª chamada (miss)"

    # 2ª leitura: cache quente, sem tocar o banco de novo.
    assert jid_allowed.peek("canal-1") == ["person", "group"]
    assert jid_allowed.get_sync("canal-1") == ["person", "group"]
    assert calls == ["canal-1"], "cache quente não deve reconsultar o banco"


def test_reset_clears_cache(monkeypatch):
    import db.repositories.channel_repo as real_channel_repo

    monkeypatch.setattr(
        real_channel_repo, "get",
        lambda cid: {"config": json.dumps({"allowed_jid_types": ["person"]})})

    jid_allowed.get_sync("canal-2")
    assert jid_allowed.peek("canal-2") == ["person"]

    jid_allowed.reset()
    assert jid_allowed.peek("canal-2") is None


def test_channel_without_saved_key_falls_back_to_runtime_default(monkeypatch):
    """Canal sem ``allowed_jid_types`` salvo cai no fallback de RUNTIME — que
    inclui ``group`` (plano 103 D2), nunca no default de criação (sem group)."""
    import db.repositories.channel_repo as real_channel_repo

    monkeypatch.setattr(real_channel_repo, "get", lambda cid: {"config": "{}"})

    types = jid_allowed.get_sync("canal-legado")
    assert types == list(jid_classifier.DEFAULT_ALLOWED_JID_TYPES)
    assert "group" in types


def test_channel_row_missing_also_falls_back(monkeypatch):
    import db.repositories.channel_repo as real_channel_repo

    monkeypatch.setattr(real_channel_repo, "get", lambda cid: None)

    types = jid_allowed.get_sync("canal-inexistente")
    assert types == list(jid_classifier.DEFAULT_ALLOWED_JID_TYPES)


def test_db_error_fails_open_to_runtime_default(monkeypatch):
    import db.repositories.channel_repo as real_channel_repo

    def _boom(channel_id):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(real_channel_repo, "get", _boom)

    types = jid_allowed.get_sync("canal-com-erro")
    assert types == list(jid_classifier.DEFAULT_ALLOWED_JID_TYPES)


def test_ttl_expiry_makes_peek_cold_again(monkeypatch):
    import db.repositories.channel_repo as real_channel_repo

    monkeypatch.setattr(
        real_channel_repo, "get",
        lambda cid: {"config": json.dumps({"allowed_jid_types": ["person"]})})

    jid_allowed.get_sync("canal-ttl")
    assert jid_allowed.peek("canal-ttl") == ["person"]

    # Volta o relógio da entrada cacheada para além do TTL (30s).
    types, _ts = jid_allowed._CACHE["canal-ttl"]
    jid_allowed._CACHE["canal-ttl"] = (types, _ts - jid_allowed._TTL - 1)

    assert jid_allowed.peek("canal-ttl") is None
