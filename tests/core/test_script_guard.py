"""Testes do detector puro de escrita não-latina (plano 167 · Fase 1).

Allowlist, não denylist: só LETRA de escrita não-latina reprova. Emoji,
símbolos, invisíveis e inglês técnico passam sempre — isto é uma trava de
ESCRITA, nunca de IDIOMA. Ver agent/script_guard.py e docs/IA.md
§"Motor de agente (AGNO)".

    venv/bin/python -m pytest tests/core/test_script_guard.py -q
"""

from __future__ import annotations

import pytest

from agent.script_guard import foreign_letters, foreign_sample, has_foreign_script


# ── Reprova: 20 escritas não-latinas + letra isolada + caso real da BIA ─────

_MUST_FAIL = {
    "armenio": "Qual seu objetivo com o combo այսօր?",
    "russo": "Isso é сегодня em russo.",
    "japones_kanji": "Hoje é 今日 em japonês.",
    "japones_hiragana": "Hoje é きょう em japonês.",
    "japones_katakana": "Isso é ミクロ em japonês.",
    "arabe": "Hoje é اليوم em árabe.",
    "hebraico": "Hoje é היום em hebraico.",
    "coreano": "Hoje é 오늘 em coreano.",
    "chines": "Hoje é 今天 em chinês.",
    "grego": "Hoje é σήμερα em grego.",
    "hindi": "Hoje é आज em hindi.",
    "tailandes": "Hoje é วันนี้ em tailandês.",
    "georgiano": "Hoje é დღეს em georgiano.",
    "bengali": "Hoje é আজ em bengali.",
    "tamil": "Hoje é இன்று em tâmil.",
    "amarico": "Hoje é ዛሬ em amárico.",
    "cherokee": "Isso é ᏣᎳᎩ em cheroqui.",
    "birmanes": "Isso é မြန်မာ em birmanês.",
    "khmer": "Isso é ខ្មែរ em khmer.",
    "cingales": "Isso é සිංහල em cingalês.",
    "mongol": "Isso é ᠮᠣᠩᠭᠣᠯ em mongol.",
    "letra_cirilica_isolada_em_frase_pt": "Qual seu objetivo сom o combo?",  # с U+0441
    "caso_real_bia": "Qual seu objetivo com o combo այսօր?",
}


@pytest.mark.parametrize("texto", _MUST_FAIL.values(), ids=list(_MUST_FAIL.keys()))
def test_reprova_escrita_nao_latina(texto):
    assert has_foreign_script(texto) is True
    assert foreign_letters(texto)  # ao menos uma letra reportada


# ── Passa: latim, inglês técnico, emoji, invisíveis, formatação ─────────────

_MUST_PASS = {
    "acentos_e_cedilha": "Configuração pronta: café, ação, órgão, não, pêssego, à vista.",
    "ingles_tecnico": (
        "Configure o MikroTik com WireGuard, depois teste o firewall "
        "antes do checkout."
    ),
    "emoji_simples": "Perfeito! 😊👋🚀",
    "emoji_composto_zwj": "\U0001f477\U0001f3fd‍♂️ Chegando!",
    "emoji_numerado_keycap": "1️⃣ Primeiro passo",
    "ordinal_a_o": "1ª posição, 3º lugar",
    "invisiveis_zwsp_bom": "Ol​á tudo bem﻿?",
    "aspas_curvas": "“Tudo certo”, ela disse — combinado’",
    "url_com_query_string": (
        "Acesse https://exemplo.com/painel?ref=abc&utm_source=whatsapp&id=123 "
        "para continuar."
    ),
    "array_json_do_split": (
        '["Primeira parte da resposta.", "Segunda parte, com café e ação."]'
    ),
    "texto_vazio": "",
}


@pytest.mark.parametrize("texto", _MUST_PASS.values(), ids=list(_MUST_PASS.keys()))
def test_passa_escrita_latina_e_nao_letras(texto):
    assert has_foreign_script(texto) is False
    assert foreign_letters(texto) == []


# ── Bordas do detector (§4.2 do plano 167 — P1 opção (a), recomendada) ─────

_EDGE_MUST_PASS = {
    "micro_sign": "A latência foi de 10 µs no teste.",
    "modifier_apostrophe": "Uso do modificador ʼ isolado.",
    "superscript_latin": "O valor xⁿ cresce mais rápido que aᵃ.",
    "mathematical_bold_latin": "Teste com \U0001d401old em negrito matemático.",
    "fullwidth_latin": "Campo Ａ em largura total.",
    "script_small_l": "Comprou 5ℓ de combustível.",
    "latin_digraph_and_ligature": "Palavra ǅ, ẞ maiúsculo alemão, ﬁ ligadura.",
    "symbols_not_letters": "Produto™ registrado a 25° com área em m².",
}


@pytest.mark.parametrize("texto", _EDGE_MUST_PASS.values(), ids=list(_EDGE_MUST_PASS.keys()))
def test_bordas_tipograficas_passam_apos_p1(texto):
    assert has_foreign_script(texto) is False


def test_borda_grego_continua_reprovando():
    """NFKC não aproxima grego de latim — Ω continua uma escrita estrangeira."""
    assert has_foreign_script("Resistência de 100 Ω medida.") is True


# ── foreign_sample: determinístico e respeita o limite ──────────────────────

def test_foreign_sample_respeita_limit_e_e_ordenado():
    texto = "Isso usa Ω, Β e Α gregos junto com texto normal."
    assert foreign_sample(texto, limit=1) == "Α"  # Α — menor codepoint
    assert foreign_sample(texto, limit=2) == "ΑΒ"  # Α, Β
    assert foreign_sample(texto, limit=8) == "ΑΒΩ"  # Α, Β, Ω — únicas
    assert foreign_sample(texto, limit=8) == foreign_sample(texto, limit=8)


def test_foreign_sample_vazio_quando_nao_ha_letra_estrangeira():
    assert foreign_sample("café com ☕ emoji e MikroTik") == ""
