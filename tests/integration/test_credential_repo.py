"""Plano 177 · F1 — repositório do cofre de credenciais das tools de IA.

Cobre a semântica manter/gravar/remover do ``PUT``, a regra de que a leitura
pública nunca devolve valor secreto, o vínculo (sem FK para ``ai_tools``) e o
único caminho que devolve valor: ``for_tool``.
"""

from __future__ import annotations

import pytest

from db.repositories import credential_repo as repo

NAME = "p177_cred"
TOOL = "p177_tool"
SENTINEL = "SEGREDO-p177-8f3a91"


@pytest.fixture(autouse=True)
def _clean(_engine_ready):
    repo.delete(NAME)
    repo.delete(NAME + "_b")
    yield
    repo.delete(NAME)
    repo.delete(NAME + "_b")


def _save(fields, name=NAME, **kw):
    meta, change = repo.upsert_credential(name, "desc", fields, **kw)
    return meta, change


def _by_key(meta):
    return {f["key"]: f for f in meta["fields"]}


# ── merge_fields (puro) ──────────────────────────────────────────────────────

def test_merge_new_field_requires_a_value():
    with pytest.raises(ValueError, match="informe o valor"):
        repo.merge_fields({}, [{"key": "chave", "secret": True}])


def test_merge_empty_list_and_bad_names_are_rejected():
    with pytest.raises(ValueError):
        repo.merge_fields({}, [])
    with pytest.raises(ValueError):
        repo.merge_fields({}, [{"key": "Chave", "value": "x"}])
    with pytest.raises(ValueError, match="repetido"):
        repo.merge_fields({}, [{"key": "a", "value": "1"}, {"key": "a", "value": "2"}])


def test_merge_limits_fields_and_value_size():
    many = [{"key": f"c{i}", "value": "x"} for i in range(repo.MAX_FIELDS + 1)]
    with pytest.raises(ValueError, match="No máximo"):
        repo.merge_fields({}, many)
    with pytest.raises(ValueError, match="bytes"):
        repo.merge_fields({}, [{"key": "a", "value": "é" * repo.MAX_VALUE_BYTES}])


def test_merge_secret_must_be_boolean_and_value_text():
    with pytest.raises(ValueError, match="secret"):
        repo.merge_fields({}, [{"key": "a", "value": "1", "secret": "sim"}])
    with pytest.raises(ValueError, match="texto"):
        repo.merge_fields({}, [{"key": "a", "value": 12345}])


def test_merge_default_is_secret():
    merged = repo.merge_fields({}, [{"key": "a", "value": "1"}])
    assert merged["a"] == {"value": "1", "secret": True}


# ── gravar / manter / remover ────────────────────────────────────────────────

def test_create_then_list_never_returns_a_secret_value():
    meta, change = _save([
        {"key": "url", "secret": False, "value": "https://api.exemplo.com"},
        {"key": "chave", "secret": True, "value": SENTINEL},
    ], actor_id=7, actor_name="Ana")
    assert change == {"created": True, "campos": ["chave", "url"],
                      "definidos": ["chave", "url"], "removidos": []}
    fields = _by_key(meta)
    assert fields["url"] == {"key": "url", "secret": False, "has_value": True,
                             "value": "https://api.exemplo.com"}
    assert fields["chave"] == {"key": "chave", "secret": True, "has_value": True}
    assert meta["updated_by"] == 7 and meta["updated_by_name"] == "Ana"
    for dump in (repr(meta), repr(repo.list_meta()), repr(repo.get_meta(NAME))):
        assert SENTINEL not in dump


def test_edit_without_value_keeps_the_current_one():
    _save([{"key": "url", "secret": False, "value": "https://a"},
           {"key": "chave", "secret": True, "value": SENTINEL}])
    meta, change = _save([{"key": "url", "secret": False, "value": "https://b"},
                          {"key": "chave", "secret": True}])
    assert change["definidos"] == ["url"] and change["removidos"] == []
    creds, secrets = repo.for_tool("nenhuma")  # sem vínculo → nada
    assert creds == {} and secrets == []
    repo.set_tool_links(NAME, [TOOL])
    creds, secrets = repo.for_tool(TOOL)
    assert creds[NAME] == {"url": "https://b", "chave": SENTINEL}
    assert secrets == [SENTINEL]


def test_empty_value_means_keep_not_overwrite():
    _save([{"key": "chave", "value": SENTINEL}])
    _save([{"key": "chave", "value": ""}])
    repo.set_tool_links(NAME, [TOOL])
    assert repo.for_tool(TOOL)[0][NAME]["chave"] == SENTINEL


def test_field_absent_from_list_is_removed():
    _save([{"key": "url", "secret": False, "value": "https://a"},
           {"key": "chave", "value": SENTINEL}])
    meta, change = _save([{"key": "url", "secret": False}])
    assert change["removidos"] == ["chave"]
    assert list(_by_key(meta)) == ["url"]


def test_secret_cannot_become_public_without_retyping_the_value():
    _save([{"key": "chave", "secret": True, "value": SENTINEL}])
    with pytest.raises(ValueError, match="público"):
        _save([{"key": "chave", "secret": False}])
    meta, _ = _save([{"key": "chave", "secret": False, "value": "agora-publico"}])
    assert _by_key(meta)["chave"]["value"] == "agora-publico"


def test_public_can_become_secret_and_the_value_disappears_from_the_listing():
    _save([{"key": "url", "secret": False, "value": "https://a"}])
    meta, _ = _save([{"key": "url", "secret": True}])
    assert "value" not in _by_key(meta)["url"]
    assert "https://a" not in repr(repo.list_meta())


def test_invalid_credential_name_is_rejected():
    with pytest.raises(ValueError, match="Nome de credencial"):
        repo.upsert_credential("Ruim Nome", "", [{"key": "a", "value": "1"}])


def test_failed_edit_does_not_change_what_was_stored():
    _save([{"key": "chave", "value": SENTINEL}])
    with pytest.raises(ValueError):
        _save([{"key": "chave"}, {"key": "nova"}])  # "nova" sem valor
    repo.set_tool_links(NAME, [TOOL])
    assert repo.for_tool(TOOL)[0][NAME] == {"chave": SENTINEL}


# ── vínculo ──────────────────────────────────────────────────────────────────

def test_links_roundtrip_from_both_sides():
    _save([{"key": "a", "value": "1"}])
    _save([{"key": "a", "value": "2"}], name=NAME + "_b")
    assert repo.set_tool_links(NAME, [TOOL, "p177_outra"]) == ["p177_outra", TOOL]
    assert repo.links_for_tool(TOOL) == [NAME]
    assert repo.get_meta(NAME)["tools"] == ["p177_outra", TOOL]

    assert repo.set_credential_links(TOOL, [NAME, NAME + "_b"]) == [NAME, NAME + "_b"]
    assert repo.links_for_tool(TOOL) == [NAME, NAME + "_b"]
    assert repo.set_credential_links(TOOL, []) == []
    assert repo.links_for_tool(TOOL) == []


def test_set_tool_links_on_missing_credential_returns_none():
    assert repo.set_tool_links("p177_inexistente", [TOOL]) is None


def test_set_credential_links_rejects_unknown_credential_and_keeps_old_links():
    _save([{"key": "a", "value": "1"}])
    repo.set_credential_links(TOOL, [NAME])
    with pytest.raises(ValueError, match="inexistente"):
        repo.set_credential_links(TOOL, [NAME, "p177_fantasma"])
    assert repo.links_for_tool(TOOL) == [NAME]


def test_link_survives_deleting_and_recreating_the_tool():
    """P4: sem FK para ai_tools — o vínculo é só um nome."""
    from db.repositories import tool_repo

    _save([{"key": "a", "value": "1"}])
    repo.set_tool_links(NAME, [TOOL])
    tool_repo.save(TOOL, description="x", code="x", dependencies=[], enabled=False)
    tool_repo.delete(TOOL)
    assert repo.links_for_tool(TOOL) == [NAME]
    tool_repo.save(TOOL, description="x", code="x", dependencies=[], enabled=False)
    try:
        assert repo.links_for_tool(TOOL) == [NAME]
    finally:
        tool_repo.delete(TOOL)


def test_deleting_the_credential_takes_its_links():
    _save([{"key": "a", "value": "1"}])
    repo.set_tool_links(NAME, [TOOL])
    assert repo.delete(NAME) == 1
    assert repo.links_for_tool(TOOL) == []
    assert repo.get_meta(NAME) is None
    assert repo.delete(NAME) == 0


# ── for_tool: o único caminho com valor ──────────────────────────────────────

def test_for_tool_only_returns_credentials_linked_to_that_tool():
    _save([{"key": "chave", "value": SENTINEL}])
    _save([{"key": "chave", "value": "OUTRO-SEGREDO"}], name=NAME + "_b")
    repo.set_tool_links(NAME, [TOOL])
    creds, secrets = repo.for_tool(TOOL)
    assert list(creds) == [NAME]
    assert secrets == [SENTINEL]
    assert "OUTRO-SEGREDO" not in repr(creds) + repr(secrets)


def test_for_tool_redaction_set_has_only_nonempty_secret_values():
    _save([{"key": "url", "secret": False, "value": "https://publica"},
           {"key": "pin", "secret": True, "value": "48213"},   # 5 dígitos (D13)
           ])
    repo.set_tool_links(NAME, [TOOL])
    creds, secrets = repo.for_tool(TOOL)
    assert creds[NAME]["url"] == "https://publica"      # entregue à tool...
    assert secrets == ["48213"]                          # ...mas só o secreto é redigido
    assert "" not in secrets


def test_list_meta_is_ordered_and_includes_tools():
    _save([{"key": "a", "value": "1"}], name=NAME + "_b")
    _save([{"key": "a", "value": "1"}])
    repo.set_tool_links(NAME, [TOOL])
    names = [m["name"] for m in repo.list_meta() if m["name"].startswith(NAME)]
    assert names == [NAME, NAME + "_b"]
    assert next(m for m in repo.list_meta() if m["name"] == NAME)["tools"] == [TOOL]
