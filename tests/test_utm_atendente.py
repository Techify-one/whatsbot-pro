"""Testes do plugin ``utm_atendente`` (plano 49) — UTM por atendente nos links de venda.

Três níveis (§7 do plano):

* **Puro** (sem DB): ``utm.apply_utm`` / ``utm.has_sales_link`` — substitui/anexa/preserva/
  ignora-fora-da-regex/idempotência/multi-URL/param+base custom.
* **Integração (Postgres)**: ``selection.select_term_for_phone`` — semeia contatos +
  atendimentos + mensagens (nota humana com ``sent_by_user_id``, nota da IA sem autor) e
  valida o gatilho (humano recente vence; IA ignorada; mapa vazio ⇒ None; grupo ⇒ None).
* **Integração (filtro)**: ``filters.rewrite_utm`` com ``source`` None/``private_ai``/
  ``private_ai_note`` (aplicar/aplicar/pular) + fail-open absoluto (nunca ``None``, nunca
  levanta) + smoke de rotas/screen via ``build_test_app``.

Roda contra o Postgres de teste (``WHATSBOT_TEST_DB_URL``; ver [[postgres-test-db-needs-utf8]]).
Rodar POR ARQUIVO (a coleção inteira quebra por scripts standalone — [[pytest-tests-nao-roda-inteiro]]):

    WHATSBOT_TEST_DB_URL="...whatsbot_test_49?sslmode=require" \
        venv/bin/python -m pytest tests/test_utm_atendente.py -q
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "storages" / "plugins" / "utm_atendente"

# ── Carregamento isolado dos módulos do plugin ──────────────────────────────
# Package sintético PRÓPRIO (``utm_ut_pkg``, não ``whatsbot_plugins.utm_atendente``)
# para (a) resolver os relativos ``from . import ...`` e (b) NÃO colidir com o pacote
# que o loader real (``build_test_app``) registra nos testes de rota/smoke.
_PKG = "utm_ut_pkg"


def _load(mod: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    name = f"{_PKG}.{mod}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN / f"{mod}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


utm = _load("utm")
config_store = _load("config_store")
selection = _load("selection")
filters = _load("filters")

SALES = re.compile(r"^https?://exemplo\.cc/")


def _ap(text: str, term: str = "ia-atendente", *, param: str = "utm_term", base: str = "ia",
        sales=SALES) -> str:
    return utm.apply_utm(text, term, param=param, base=base, sales_re=sales)


# ══════════════════════════════════════════════════════════════════════════
# Nível 1 — puro (sem DB)
# ══════════════════════════════════════════════════════════════════════════

def test_apply_utm_replaces_base():
    assert _ap("https://exemplo.cc/v7?utm_term=ia") == "https://exemplo.cc/v7?utm_term=ia-atendente"


def test_apply_utm_appends_when_missing_no_query():
    assert _ap("https://exemplo.cc/v7") == "https://exemplo.cc/v7?utm_term=ia-atendente"


def test_apply_utm_appends_when_missing_with_query():
    assert _ap("https://exemplo.cc/v7?a=1") == "https://exemplo.cc/v7?a=1&utm_term=ia-atendente"


def test_apply_utm_preserves_other_utm_term():
    assert _ap("https://exemplo.cc/v7?utm_term=other") == "https://exemplo.cc/v7?utm_term=other"


def test_apply_utm_ignores_non_sales_link():
    # Fora da regex de venda (google) → intacto, mesmo com utm_term=ia.
    assert _ap("https://google.com/?utm_term=ia") == "https://google.com/?utm_term=ia"


def test_apply_utm_idempotent():
    once = _ap("https://exemplo.cc/v7?utm_term=ia")
    assert _ap(once) == once  # reaplicar é no-op (R2)


def test_apply_utm_base_not_false_matched():
    # ``utm_term=ial`` NÃO deve casar a base ``ia`` (lookahead) — fica intacto.
    assert _ap("https://exemplo.cc/v7?utm_term=ial") == "https://exemplo.cc/v7?utm_term=ial"


def test_apply_utm_base_in_middle_and_end():
    assert _ap("https://exemplo.cc/v7?utm_term=ia&x=1") == "https://exemplo.cc/v7?utm_term=ia-atendente&x=1"
    assert _ap("https://exemplo.cc/v7?x=1&utm_term=ia") == "https://exemplo.cc/v7?x=1&utm_term=ia-atendente"


def test_apply_utm_multiple_urls_in_one_part():
    out = _ap("Veja https://exemplo.cc/a?utm_term=ia e https://exemplo.cc/b agora")
    assert out == "Veja https://exemplo.cc/a?utm_term=ia-atendente e https://exemplo.cc/b?utm_term=ia-atendente agora"


def test_apply_utm_no_link_unchanged():
    assert _ap("olá, tudo bem?") == "olá, tudo bem?"


def test_apply_utm_custom_param_and_base():
    sales = re.compile(r"^https?://loja\.x/")
    out = utm.apply_utm("https://loja.x/p?ref=bot", "vend-joao",
                        param="ref", base="bot", sales_re=sales)
    assert out == "https://loja.x/p?ref=vend-joao"
    # Anexa com o param custom quando ausente:
    out2 = utm.apply_utm("https://loja.x/p", "vend-joao",
                         param="ref", base="bot", sales_re=sales)
    assert out2 == "https://loja.x/p?ref=vend-joao"


def test_has_sales_link():
    assert utm.has_sales_link("x https://exemplo.cc/a y", SALES) is True
    assert utm.has_sales_link("x https://google.com/a y", SALES) is False
    assert utm.has_sales_link("sem url", SALES) is False
    assert utm.has_sales_link("", SALES) is False
    # link no fim de frase (com pontuação grudada) ainda conta como link de venda
    assert utm.has_sales_link("Compre: https://exemplo.cc/promo.", SALES) is True


def test_apply_utm_base_sub_with_trailing_punctuation():
    # A pontuação de fim de frase fica FORA da URL; a base é reescrita.
    assert _ap("Veja https://exemplo.cc/v7?utm_term=ia. Tchau") == \
        "Veja https://exemplo.cc/v7?utm_term=ia-atendente. Tchau"
    assert _ap("Compra: https://exemplo.cc/v7?utm_term=ia!") == \
        "Compra: https://exemplo.cc/v7?utm_term=ia-atendente!"
    assert _ap("Link https://exemplo.cc/v7?utm_term=ia, ok") == \
        "Link https://exemplo.cc/v7?utm_term=ia-atendente, ok"


def test_apply_utm_append_trailing_period_no_corruption():
    # Sem query, link no fim de frase: NÃO pode virar '/promo.?utm_term=...'.
    assert _ap("Compre agora: https://exemplo.cc/promo. Bom dia!") == \
        "Compre agora: https://exemplo.cc/promo?utm_term=ia-atendente. Bom dia!"


def test_apply_utm_fragment_param_before_hash():
    # O parâmetro entra ANTES do '#fragment' (ordem válida de URL).
    assert _ap("Acesse https://exemplo.cc/promo#top") == \
        "Acesse https://exemplo.cc/promo?utm_term=ia-atendente#top"
    assert _ap("x https://exemplo.cc/v7?utm_term=ia#top y") == \
        "x https://exemplo.cc/v7?utm_term=ia-atendente#top y"


def test_apply_utm_trailing_punct_is_idempotent():
    once = _ap("Veja https://exemplo.cc/promo. fim")
    assert _ap(once) == once


def test_default_domain_matcher_covers_both_sales_domains():
    # Regressão do caso real: a oferta OFERTAX usa exemplo.net (não exemplo.cc).
    # O matcher é DERIVADO da lista de domínios (config em banco), não hardcoded.
    default_re = config_store.build_domain_regex(config_store.DEFAULT_SALES_DOMAINS)

    def ap(t):
        return utm.apply_utm(t, "ia-atendente", param="utm_term", base="ia", sales_re=default_re)

    # exemplo.net (link sem utm_term) → anexa
    assert ap("Segue o link para garantir sua vaga: https://exemplo.net/online-ofertax") == \
        "Segue o link para garantir sua vaga: https://exemplo.net/online-ofertax?utm_term=ia-atendente"
    # exemplo.cc segue casando (substitui a base)
    assert ap("https://exemplo.cc/scripts-vd-ia?utm_term=ia") == \
        "https://exemplo.cc/scripts-vd-ia?utm_term=ia-atendente"
    assert ap("https://exemplo.cc/c.mtr-ia") == "https://exemplo.cc/c.mtr-ia?utm_term=ia-atendente"
    # subdomínio também casa (www.exemplo.cc)
    assert ap("https://www.exemplo.cc/x") == "https://www.exemplo.cc/x?utm_term=ia-atendente"
    # o checkout (ticto) e domínios de terceiros NÃO recebem UTM
    assert ap("http://checkout.ticto.app/O5428A72F") == "http://checkout.ticto.app/O5428A72F"
    assert ap("https://google.com/?utm_term=ia") == "https://google.com/?utm_term=ia"
    # anti-spoof: um domínio que só CONTÉM o alvo não casa
    assert ap("https://exemplo.cc.evil.com/x") == "https://exemplo.cc.evil.com/x"
    assert ap("https://notexemplo.cc/x") == "https://notexemplo.cc/x"


def test_normalize_domain():
    n = config_store.normalize_domain
    assert n("exemplo.cc") == "exemplo.cc"
    assert n("https://exemplo.cc/promo") == "exemplo.cc"
    assert n("www.exemplo.net") == "exemplo.net"
    assert n("  HTTP://WWW.Exemplo.CC:443/x?a=1 ") == "exemplo.cc"
    assert n("") == ""
    assert n("sem-ponto") == ""
    assert n("espaço ruim.com") == ""


def test_build_domain_regex_arbitrary_new_domain():
    # O ponto central do pedido: um domínio NOVO qualquer passa a funcionar.
    rx = config_store.build_domain_regex(["meudominio.com.br"])
    out = utm.apply_utm("Compre em https://meudominio.com.br/oferta", "ia-ze",
                        param="utm_term", base="ia", sales_re=rx)
    assert out == "Compre em https://meudominio.com.br/oferta?utm_term=ia-ze"
    # domínio não listado não recebe
    assert utm.apply_utm("https://exemplo.cc/x", "ia-ze", param="utm_term", base="ia",
                         sales_re=rx) == "https://exemplo.cc/x"


def test_effective_sales_regex_override_wins():
    from types import SimpleNamespace as NS
    # override cru presente → ignora a lista de domínios
    cfg = NS(sales_domains=["exemplo.cc"], sales_link_regex=r"^https?://so-esse\.com/")
    rx = config_store.effective_sales_regex(cfg)
    assert rx.search("https://so-esse.com/x")
    assert not rx.search("https://exemplo.cc/x")
    # override vazio → deriva da lista
    cfg2 = NS(sales_domains=["exemplo.cc"], sales_link_regex="")
    rx2 = config_store.effective_sales_regex(cfg2)
    assert rx2.search("https://exemplo.cc/x")
    # sem domínios e sem override → None (no-op)
    assert config_store.effective_sales_regex(NS(sales_domains=[], sales_link_regex="")) is None


def test_legacy_regex_override_auto_heals(_engine_ready):
    # Instalação que atualizou com a regex-default ANTIGA salva no override: o plugin
    # trata como vazio (usa a lista) — senão exemplo.net ficaria de fora (o bug real).
    from db.repositories import config_repo
    for legacy in (r"^https?://exemplo\.cc/", r"^https?://(www\.)?(exemplo\.cc|exemplo\.net)/"):
        config_repo.set_many({
            "plugin.utm_atendente.sales_domains": ["exemplo.cc", "exemplo.net"],
            "plugin.utm_atendente.sales_link_regex": legacy,
        })
        config_store.invalidate_cache()
        cfg = config_store.get_settings()
        assert cfg.sales_link_regex == ""                      # auto-heal → tela mostra vazio
        rx = config_store.effective_sales_regex(cfg)
        assert rx.search("https://exemplo.net/online-ofertax")  # a lista volta a valer
    # já um override REAL do operador é preservado
    config_repo.set("plugin.utm_atendente.sales_link_regex", r"^https?://meusite\.com/")
    config_store.invalidate_cache()
    assert config_store.get_settings().sales_link_regex == r"^https?://meusite\.com/"


def test_routes_put_legacy_regex_persists_as_empty(utm_app):
    c = utm_app.client
    put = c.put("/api/plugins/utm_atendente/mapping",
                json={"sales_link_regex": r"^https?://exemplo\.cc/"}).json()
    assert put["ok"] is True
    assert put["data"]["settings"]["sales_link_regex"] == ""   # persistido vazio (auto-heal)


# ══════════════════════════════════════════════════════════════════════════
# Helpers de integração (DB) — seed via ContactMemory (mesma via de produção)
# ══════════════════════════════════════════════════════════════════════════

def _seed_conversation(phone: str, notes: list[tuple], *, is_group: bool = False):
    """Cria contato + conversa + mensagens. ``notes`` = [(role, content, sent_by_user_id)].

    Sempre grava uma msg ``user`` primeiro (materializa a conversa). Retorna o contact_id.
    """
    from agent.memory import ContactMemory
    cm = ContactMemory(phone)
    cm.add_message("user", "quero informações")
    for role, content, uid in notes:
        cm.add_message(role, content, sent_by_user_id=uid,
                       sent_by_name=(f"user{uid}" if uid else None))
    if is_group:
        from sqlalchemy import text as _text
        from db.engine import get_engine
        with get_engine().begin() as conn:
            conn.execute(_text("UPDATE contacts SET is_group=1 WHERE id=:i"), {"i": cm.id})
    return cm.id


def _set_mapping(mapping: dict):
    from db.repositories import config_repo
    config_repo.set("plugin.utm_atendente.utm_mapping", mapping)
    config_store.invalidate_cache()


def _configure(*, enabled=True, mapping=None, lookback_messages=5,
               utm_param="utm_term", utm_base="ia",
               sales_domains=("exemplo.cc", "exemplo.net"), sales_link_regex=""):
    """Fixa TODOS os escalares + a lista de domínios + o mapa (order-independent).

    Reseta o conjunto inteiro (não só o que muda) para que um teste que grava, p.ex.,
    uma regex inválida não vaze para o próximo. Por padrão usa a LISTA de domínios
    (``sales_link_regex`` vazio) — o caminho normal, gerenciável no front.
    """
    from db.repositories import config_repo
    config_repo.set_many({
        "plugin.utm_atendente.enabled": enabled,
        "plugin.utm_atendente.lookback_messages": lookback_messages,
        "plugin.utm_atendente.utm_param": utm_param,
        "plugin.utm_atendente.utm_base": utm_base,
        "plugin.utm_atendente.sales_domains": list(sales_domains),
        "plugin.utm_atendente.sales_link_regex": sales_link_regex,
    })
    config_repo.set("plugin.utm_atendente.utm_mapping", mapping or {})
    config_store.invalidate_cache()


# ══════════════════════════════════════════════════════════════════════════
# Nível 2 — seleção (Postgres)
# ══════════════════════════════════════════════════════════════════════════

def test_selection_human_recent_wins(_engine_ready):
    phone = "5511900010001"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _set_mapping({"15": "ia-atendente"})
    assert selection.select_term_for_phone(phone, 5) == "ia-atendente"


def test_selection_two_humans_most_recent_wins(_engine_ready):
    phone = "5511900010002"
    _seed_conversation(phone, [
        ("private_note", "eu começo", 15),
        ("private_note", "eu assumo", 16),  # mais recente
    ])
    _set_mapping({"15": "ia-atendente", "16": "ia-bob"})
    assert selection.select_term_for_phone(phone, 5) == "ia-bob"


def test_selection_ai_note_ignored(_engine_ready):
    phone = "5511900010003"
    _seed_conversation(phone, [("private_note", "nota da IA", None)])  # sem autor
    _set_mapping({"15": "ia-atendente"})
    assert selection.select_term_for_phone(phone, 5) is None


def test_selection_unmapped_human_none(_engine_ready):
    phone = "5511900010004"
    _seed_conversation(phone, [("private_note", "manda o link", 77)])
    _set_mapping({"15": "ia-atendente"})
    assert selection.select_term_for_phone(phone, 5) is None


def test_selection_empty_mapping_none(_engine_ready):
    phone = "5511900010005"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _set_mapping({})
    assert selection.select_term_for_phone(phone, 5) is None


def test_selection_unknown_phone_none(_engine_ready):
    _set_mapping({"15": "ia-atendente"})
    assert selection.select_term_for_phone("5511000000000", 5) is None


def test_selection_group_none(_engine_ready):
    phone = "5511900010006"
    _seed_conversation(phone, [("private_note", "manda o link", 15)], is_group=True)
    _set_mapping({"15": "ia-atendente"})
    assert selection.select_term_for_phone(phone, 5) is None


def test_selection_lookback_window_excludes_old_note(_engine_ready):
    # Nota humana é a MAIS ANTIGA; com janela pequena (n=1) ela cai fora ⇒ None.
    phone = "5511900010007"
    _seed_conversation(phone, [
        ("private_note", "manda o link", 15),  # antiga
        ("assistant", "ok", None),
        ("user", "obrigado", None),
    ])
    _set_mapping({"15": "ia-atendente"})
    assert selection.select_term_for_phone(phone, 1) is None       # janela curta
    assert selection.select_term_for_phone(phone, 10) == "ia-atendente"  # janela larga


def test_selection_ignores_tool_call_noise(_engine_ready):
    # Os cards tool_call (painel-only) que a IA grava no turno NÃO podem empurrar a nota
    # humana para fora da janela N (senão a atribuição se perde no fluxo com ferramentas).
    phone = "5511900010008"
    notes = [("private_note", "manda o link", 15)] + [("tool_call", f"call{i}", None) for i in range(5)]
    _seed_conversation(phone, notes)
    _set_mapping({"15": "ia-atendente"})
    # Com N=5, os 5 tool_call encheriam a janela crua; excluídos, a nota humana vence.
    assert selection.select_term_for_phone(phone, 5) == "ia-atendente"


# ══════════════════════════════════════════════════════════════════════════
# Nível 3 — filtro (integração + fail-open)
# ══════════════════════════════════════════════════════════════════════════

def _ctx(**extras):
    return SimpleNamespace(extras=extras)


_LINK = "Segue o link: https://exemplo.cc/v7?utm_term=ia"


def test_filter_applies_source_none(_engine_ready):
    phone = "5511900020001"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(mapping={"15": "ia-atendente"})
    out = filters.rewrite_utm(_ctx(phone=phone), [_LINK])
    assert out == ["Segue o link: https://exemplo.cc/v7?utm_term=ia-atendente"]


def test_filter_applies_private_ai(_engine_ready):
    phone = "5511900020002"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(mapping={"15": "ia-atendente"})
    out = filters.rewrite_utm(_ctx(phone=phone, source="private_ai"), [_LINK])
    assert out == ["Segue o link: https://exemplo.cc/v7?utm_term=ia-atendente"]


def test_filter_skips_private_ai_note(_engine_ready):
    phone = "5511900020003"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(mapping={"15": "ia-atendente"})
    out = filters.rewrite_utm(_ctx(phone=phone, source="private_ai_note"), [_LINK])
    assert out == [_LINK]  # nota privada não vai ao WhatsApp → intacto (D3)


def test_filter_no_human_note_unchanged(_engine_ready):
    phone = "5511900020004"
    _seed_conversation(phone, [("assistant", "oi", None)])
    _configure(mapping={"15": "ia-atendente"})
    assert filters.rewrite_utm(_ctx(phone=phone), [_LINK]) == [_LINK]


def test_filter_disabled_unchanged(_engine_ready):
    phone = "5511900020005"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(enabled=False, mapping={"15": "ia-atendente"})
    assert filters.rewrite_utm(_ctx(phone=phone), [_LINK]) == [_LINK]


def test_filter_no_sales_link_unchanged(_engine_ready):
    phone = "5511900020006"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(mapping={"15": "ia-atendente"})
    parts = ["oi, tudo bem?", "https://google.com/x?utm_term=ia"]
    assert filters.rewrite_utm(_ctx(phone=phone), parts) == parts


def test_filter_failopen_when_selection_raises(_engine_ready, monkeypatch):
    phone = "5511900020007"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(mapping={"15": "ia-atendente"})

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(selection, "select_term_for_phone", _boom)
    out = filters.rewrite_utm(_ctx(phone=phone), [_LINK])
    assert out == [_LINK]      # fail-open: parts intacto…
    assert out is not None     # …e NUNCA None (P1)


def test_filter_failopen_on_invalid_regex(_engine_ready):
    phone = "5511900020008"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(mapping={"15": "ia-atendente"}, sales_link_regex="([unclosed")
    assert filters.rewrite_utm(_ctx(phone=phone), [_LINK]) == [_LINK]  # regex ruim ⇒ no-op (R5)


def test_filter_never_returns_none_on_empty(_engine_ready):
    _configure(mapping={"15": "ia-atendente"})
    assert filters.rewrite_utm(_ctx(phone="x"), []) == []


def test_filter_missing_phone_unchanged(_engine_ready):
    _configure(mapping={"15": "ia-atendente"})
    assert filters.rewrite_utm(_ctx(), [_LINK]) == [_LINK]  # sem phone no extras


def test_filter_applies_on_exemplo_via_domain_list(_engine_ready):
    # O caso real: link em exemplo.net (na lista de domínios) recebe a UTM.
    phone = "5511900020009"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(mapping={"15": "ia-atendente"})  # domínios default incluem exemplo.net
    part = "Segue o link para garantir sua vaga: https://exemplo.net/online-ofertax"
    out = filters.rewrite_utm(_ctx(phone=phone), [part])
    assert out == ["Segue o link para garantir sua vaga: https://exemplo.net/online-ofertax?utm_term=ia-atendente"]


def test_filter_applies_on_newly_added_domain(_engine_ready):
    # Adicionar um domínio novo pela config (o pedido do usuário) faz o link funcionar.
    phone = "5511900020010"
    _seed_conversation(phone, [("private_note", "manda o link", 15)])
    _configure(mapping={"15": "ia-atendente"}, sales_domains=["novaoferta.com.br"])
    out = filters.rewrite_utm(_ctx(phone=phone), ["Acesse https://novaoferta.com.br/x?utm_term=ia"])
    assert out == ["Acesse https://novaoferta.com.br/x?utm_term=ia-atendente"]
    # e um domínio fora da lista não recebe
    assert filters.rewrite_utm(_ctx(phone=phone), ["https://exemplo.cc/x"]) == ["https://exemplo.cc/x"]


# ══════════════════════════════════════════════════════════════════════════
# Nível 4 — smoke de plugin: carga sem load_error, rotas, screen
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def utm_app(_engine_ready):
    """App hermético com gowa + utm_atendente carregados (fonte = storages/plugins)."""
    import tests.support as support
    orig = support.REAL_PLUGIN_EXAMPLES
    support.REAL_PLUGIN_EXAMPLES = ROOT / "storages" / "plugins"
    built = support.build_test_app(["gowa", "utm_atendente"])
    try:
        yield built
    finally:
        support.REAL_PLUGIN_EXAMPLES = orig
        try:
            built.client.__exit__(None, None, None)
        except Exception:
            pass
        try:
            if built._tmp is not None:
                built._tmp.cleanup()
        except Exception:
            pass


def _plugins_list(client):
    data = client.get("/api/plugins").json()["data"]
    return data if isinstance(data, list) else data.get("plugins", data)


def test_plugin_loads_without_error(utm_app):
    row = next((p for p in _plugins_list(utm_app.client)
                if isinstance(p, dict) and p.get("id") == "utm_atendente"), None)
    assert row is not None
    assert row.get("enabled") is True
    assert not row.get("load_error")


def test_routes_mapping_roundtrip(utm_app):
    c = utm_app.client
    g = c.get("/api/plugins/utm_atendente/mapping").json()
    assert g["ok"] is True
    assert g["data"]["settings"]["utm_param"] == "utm_term"

    put = c.put("/api/plugins/utm_atendente/mapping", json={
        "enabled": True, "lookback_messages": 8, "utm_param": "utm_term",
        "utm_base": "ia", "sales_link_regex": r"^https?://exemplo\.cc/",
        "mapping": {"15": "ia-atendente", "16": "ia-bob"}}).json()
    assert put["ok"] is True
    assert put["data"]["mapping"] == {"15": "ia-atendente", "16": "ia-bob"}

    g2 = c.get("/api/plugins/utm_atendente/mapping").json()["data"]
    assert g2["settings"]["lookback_messages"] == 8
    assert g2["mapping"] == {"15": "ia-atendente", "16": "ia-bob"}


def test_routes_put_invalid_regex_400(utm_app):
    r = utm_app.client.put("/api/plugins/utm_atendente/mapping",
                           json={"sales_link_regex": "([unclosed"})
    assert r.status_code == 400
    assert "inválida" in (r.json().get("error") or "")


def test_routes_put_invalid_term_400(utm_app):
    r = utm_app.client.put("/api/plugins/utm_atendente/mapping",
                           json={"mapping": {"15": "ia anna & x"}})
    assert r.status_code == 400
    assert "utm_term" in (r.json().get("error") or "")


def test_routes_users_lists_seeded_user(utm_app):
    from db.repositories import user_repo
    user_repo.create(email="anna.utm@test.com", name="Atendente UTM", password_hash="x")
    u = utm_app.client.get("/api/plugins/utm_atendente/users").json()
    assert u["ok"] is True
    names = [x["name"] for x in u["data"]["users"]]
    assert "Atendente UTM" in names


def test_screen_served(utm_app):
    man = utm_app.client.get("/api/plugins/manifest").json()["data"]["plugins"]
    row = next(p for p in man if p["id"] == "utm_atendente")
    screen = row["screens"][0]
    assert screen["config"] is True
    assert screen["requires"] == "config"
    r = utm_app.client.get(screen["component"])
    assert r.status_code == 200
    assert "export default function UtmAtendenteConfig" in r.text


def test_routes_put_lookback_upper_bound_400(utm_app):
    r = utm_app.client.put("/api/plugins/utm_atendente/mapping",
                           json={"lookback_messages": 1000})
    assert r.status_code == 400
    assert "≤" in (r.json().get("error") or "")


def test_routes_trims_utm_param_and_base(utm_app):
    c = utm_app.client
    put = c.put("/api/plugins/utm_atendente/mapping",
                json={"utm_param": " utm_term ", "utm_base": " ia "}).json()
    assert put["ok"] is True
    # Persistido SEM os espaços das pontas (senão o match quebraria silenciosamente).
    assert put["data"]["settings"]["utm_param"] == "utm_term"
    assert put["data"]["settings"]["utm_base"] == "ia"
    g = c.get("/api/plugins/utm_atendente/mapping").json()["data"]
    assert g["settings"]["utm_param"] == "utm_term"
    assert g["settings"]["utm_base"] == "ia"


def test_routes_domains_roundtrip_and_normalize(utm_app):
    c = utm_app.client
    # Aceita URL/CSV/www e normaliza para o host puro (dedup).
    put = c.put("/api/plugins/utm_atendente/mapping", json={
        "sales_domains": ["https://exemplo.cc/x", "www.exemplo.net", "exemplo.cc"]}).json()
    assert put["ok"] is True
    assert put["data"]["settings"]["sales_domains"] == ["exemplo.cc", "exemplo.net"]
    g = c.get("/api/plugins/utm_atendente/mapping").json()["data"]
    assert g["settings"]["sales_domains"] == ["exemplo.cc", "exemplo.net"]


def test_routes_put_invalid_domain_400(utm_app):
    r = utm_app.client.put("/api/plugins/utm_atendente/mapping",
                           json={"sales_domains": ["exemplo.cc", "isto nao e dominio"]})
    assert r.status_code == 400
    assert "Domínio inválido" in (r.json().get("error") or "")


def test_routes_put_empty_domains_without_override_400(utm_app):
    r = utm_app.client.put("/api/plugins/utm_atendente/mapping",
                           json={"sales_domains": [], "sales_link_regex": ""})
    assert r.status_code == 400
    assert "domínio" in (r.json().get("error") or "").lower()


def test_routes_advanced_regex_override_optional(utm_app):
    c = utm_app.client
    # regex avançada vazia é OK (usa a lista); e uma regex válida é aceita como override
    assert c.put("/api/plugins/utm_atendente/mapping",
                 json={"sales_link_regex": ""}).json()["ok"] is True
    put = c.put("/api/plugins/utm_atendente/mapping",
                json={"sales_link_regex": r"^https?://so-esse\.com/"}).json()
    assert put["ok"] is True
    assert put["data"]["settings"]["sales_link_regex"] == r"^https?://so-esse\.com/"


# ── config_store: trim + clamp (unit) ───────────────────────────────────────

def test_config_store_trims_ident_and_clamps_lookback(_engine_ready):
    from db.repositories import config_repo
    config_repo.set("plugin.utm_atendente.utm_base", "ia ")       # espaço à direita
    config_repo.set("plugin.utm_atendente.utm_param", "  utm_term")
    config_repo.set("plugin.utm_atendente.lookback_messages", 999)  # acima do teto
    config_store.invalidate_cache()
    cfg = config_store.get_settings()
    assert cfg.utm_base == "ia"                       # trimado
    assert cfg.utm_param == "utm_term"                # trimado
    assert cfg.lookback_messages == config_store.LOOKBACK_MAX   # clampado a 100
