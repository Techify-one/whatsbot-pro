"""Mascaramento da trilha de auditoria (``audit_repo._sanitize``).

O casamento é por nome EXATO de chave: um segredo sob um nome que ninguém listou
entra na trilha em claro (foi o caso do ``nexus_dsn``). Estes testes travam os
nomes que já causaram ou poderiam causar o incidente, sem tocar no banco.
"""
import json

import pytest

from db.repositories import audit_repo


@pytest.mark.parametrize("key", [
    "nexus_dsn",
    "plataforma_api_key",
    "checkout_api_key",
    "openrouter_api_key",
    "password",
])
def test_secret_key_is_redacted(key):
    assert audit_repo._sanitize({key: "valor-secreto"}) == {key: "***"}


def test_redaction_is_case_insensitive_and_recursive():
    out = audit_repo._sanitize({"cfg": [{"Checkout_API_Key": "ck_live_x", "ok": 1}]})
    assert out == {"cfg": [{"Checkout_API_Key": "***", "ok": 1}]}


def test_non_secret_keys_are_kept():
    data = {"secret_definido": True, "checkout_base_url": "https://exemplo.test"}
    assert audit_repo._sanitize(data) == data


def test_dump_never_serialises_the_secret():
    dumped = audit_repo._dump({"checkout_api_key": "ck_live_naoVazar"})
    assert "ck_live_naoVazar" not in dumped
    assert json.loads(dumped) == {"checkout_api_key": "***"}
