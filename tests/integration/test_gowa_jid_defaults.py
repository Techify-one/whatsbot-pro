"""Plano 103 — canal GOWA NOVO nasce sem "Grupo / Comunidade" marcado.

Existem DOIS defaults parecidos, em arquivos diferentes, com efeitos OPOSTOS —
e o par de asserções abaixo é justamente o que impede alguém de "consertar" um
achando que é o outro:

  * ``GOWA_DEFAULT_JID_TYPES`` (:mod:`channels.providers.gowa_channel`) — default
    de **CRIAÇÃO**: semeia o formulário de um canal novo via
    ``config_fields[].default`` do descriptor. Nasce SEM ``group``, porque um
    número de atendimento individual materializava todo grupo de que participa
    (118 contatos fantasma no incidente do plano 102).
  * ``DEFAULT_ALLOWED_JID_TYPES`` (:mod:`channels.jid`) — fallback de **RUNTIME**:
    vale para canal que nunca gravou ``config.allowed_jid_types``. Continua COM
    ``group``: tirá-lo dali seria retroativo e calaria grupos em canais legados
    (plano 103 D2).

    venv/bin/python -m pytest tests/integration/test_gowa_jid_defaults.py -q
"""

from __future__ import annotations

import json

from channels import jid as _jid
from channels.providers.gowa_channel import GOWAChannel


def _jid_field(descriptor: dict) -> dict:
    """O ``config_field`` do multiselect de tipos de JID, pelo nome da chave."""
    fields = [f for f in descriptor.get("config_fields", [])
              if f.get("key") == "allowed_jid_types"]
    assert len(fields) == 1, \
        f"esperado 1 config_field allowed_jid_types, achei {len(fields)}"
    return fields[0]


# ── Lado 1: default de CRIAÇÃO (o que este plano mudou) ──────────────────────

def test_descriptor_default_has_no_group():
    """Canal GOWA novo nasce sem grupo: o descriptor não oferece ``group`` no
    ``default`` do multiselect (o formulário é semeado dele — plano 33)."""
    field = _jid_field(GOWAChannel.provider_descriptor())
    assert field["type"] == "multiselect"
    assert field["default"] == ["person", "person_lid"], \
        "default de CRIAÇÃO mudou — canal novo voltaria a nascer com grupo"
    assert "group" not in field["default"]


def test_descriptor_still_offers_group_as_option():
    """A opção continua EXISTINDO e visível (D3) — só não vem marcada. Mudar o
    default nunca pode virar 'sumiu com o checkbox'."""
    field = _jid_field(GOWAChannel.provider_descriptor())
    values = [o["value"] for o in field["options"]]
    assert "group" in values
    # Todo valor do catálogo é um tipo conhecido do runtime (evita default/opção
    # órfã que o ``normalize_allowed_types`` descartaria em silêncio).
    assert set(values) <= set(_jid.ALL_JID_TYPES)
    assert set(field["default"]) <= set(values)


# ── Lado 2: fallback de RUNTIME (o que NÃO pode mudar — D2) ──────────────────

def test_runtime_fallback_still_allows_group():
    """Canal legado sem a chave salva continua recebendo grupo. Mudar isto seria
    RETROATIVO — o oposto do pedido."""
    assert "group" in _jid.DEFAULT_ALLOWED_JID_TYPES
    assert _jid.DEFAULT_ALLOWED_JID_TYPES == ["person", "person_lid", "group"]
    # É o valor que ``normalize_allowed_types`` devolve para config ausente/lixo.
    assert _jid.normalize_allowed_types(None) == ["person", "person_lid", "group"]
    assert _jid.is_allowed("group", _jid.normalize_allowed_types(None)) is True


def test_the_two_defaults_are_distinct():
    """A asserção que amarra o par: se alguém alinhar os dois valores, este teste
    cai — não importa em qual dos dois arquivos o 'conserto' foi feito."""
    from channels.providers.gowa_channel import GOWA_DEFAULT_JID_TYPES
    assert list(GOWA_DEFAULT_JID_TYPES) != list(_jid.DEFAULT_ALLOWED_JID_TYPES)
    assert "group" not in GOWA_DEFAULT_JID_TYPES
    assert "group" in _jid.DEFAULT_ALLOWED_JID_TYPES


# ── Ponta a ponta: criar pela API grava o default sem grupo ──────────────────

def test_create_channel_persists_default_without_group(build_app):
    """Ponta a ponta do caminho da UI: ``GET /api/channels/providers`` →
    ``initialConfigValues`` (o cliente copia o ``default``) → ``POST
    /api/channels``. A row gravada tem ``allowed_jid_types`` SEM grupo."""
    built = build_app(["gowa"])

    r = built.client.get("/api/channels/providers")
    assert r.status_code == 200, r.text
    providers = {p["provider"]: p for p in r.json()["data"]["providers"]}
    assert "gowa" in providers, "gowa deve estar registrado no app de teste"
    seeded = _jid_field(providers["gowa"])["default"]
    assert seeded == ["person", "person_lid"]

    # O que o formulário monta: os configValues semeados do descriptor.
    r = built.client.post("/api/channels", json={
        "provider": "gowa",
        "display_name": "Canal novo (plano 103)",
        "credentials": {},
        "config": {"allowed_jid_types": list(seeded), "ai": {}},
    })
    assert r.status_code == 200, r.text
    cid = r.json()["data"]["id"]

    from db.repositories import channel_repo
    row = channel_repo.get(cid)
    assert row is not None
    cfg = row["config"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    assert cfg["allowed_jid_types"] == ["person", "person_lid"], \
        "canal criado pela UI não pode nascer com grupo"
