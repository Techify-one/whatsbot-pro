"""Plano 43 — filtro de lista-negra por regex do histórico da IA.

Cobre o módulo puro ``agent/history_filter.py``: match por âncora de role
(``^private_note\\t``), por conteúdo em qualquer role (``Protocolo aberto``),
``filter_rows``, regex inválida ignorada (fail-open) e o cache de compile lido da
config (via ``build_app`` para ter o engine Postgres de teste).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_history_filter_config():
    """Restaura ``ai_history_exclude_patterns`` para ``[]`` + limpa o cache após cada
    teste. O engine é process-global (uma DB compartilhada na sessão) — sem isto um
    padrão deixado por um teste vazaria para os seguintes (ex.: test_context_dedup)."""
    yield
    from agent import history_filter as hf
    hf.reset_cache()
    try:
        from db.repositories import config_repo
        config_repo.set(hf.CONFIG_KEY, [])
    except Exception:
        pass  # engine não inicializado (testes puros isolados) → nada a limpar
    hf.reset_cache()


def _fake_llm_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class _FakeClient:
    def __init__(self, response):
        self.create_calls: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.create_calls.append(kwargs)
                return response

        self.chat = SimpleNamespace(completions=_Completions())


# ── Puros (sem DB) ──────────────────────────────────────────────────────────

def test_matches_no_patterns_is_false():
    from agent import history_filter as hf
    assert hf.matches("user", "qualquer coisa", []) is False


def test_matches_role_anchor():
    from agent import history_filter as hf
    compiled = hf._compile([r"^private_note\t"])
    # Casa a nota privada (âncora de role no início do alvo role<TAB>content).
    assert hf.matches("private_note", "🔖 Protocolo aberto · PROT-1", compiled) is True
    # NÃO casa quando "private_note" aparece só no conteúdo de outro role.
    assert hf.matches("user", "private_note isto é texto", compiled) is False


def test_matches_content_anywhere():
    from agent import history_filter as hf
    compiled = hf._compile(["Protocolo aberto"])
    assert hf.matches("assistant", "acabei de abrir: Protocolo aberto agora", compiled) is True
    assert hf.matches("private_note", "🔖 Protocolo aberto · PROT-1", compiled) is True
    assert hf.matches("user", "oi, tudo bem?", compiled) is False


def test_filter_rows_role_anchor_cuts_only_private_notes():
    from agent import history_filter as hf
    compiled = hf._compile([r"^private_note\t"])
    rows = [
        {"role": "user", "content": "oi"},
        {"role": "private_note", "content": "🔖 Protocolo aberto · PROT-1"},
        {"role": "assistant", "content": "olá! como posso ajudar?"},
        {"role": "private_note", "content": "nota humana útil"},
    ]
    out = hf.filter_rows(rows, compiled)
    assert [r["role"] for r in out] == ["user", "assistant"]


def test_filter_rows_no_patterns_returns_same_object():
    from agent import history_filter as hf
    rows = [{"role": "user", "content": "oi"}]
    assert hf.filter_rows(rows, []) is rows


def test_filter_rows_tolerates_none_and_missing_content():
    from agent import history_filter as hf
    compiled = hf._compile(["PROT-"])
    rows = [{"role": "user", "content": None}, {"role": "assistant"},
            {"role": "private_note", "content": "PROT-42"}]
    out = hf.filter_rows(rows, compiled)
    # Só a linha PROT-42 é cortada; None/ausente não quebram nem casam.
    assert [r["role"] for r in out] == ["user", "assistant"]


def test_compile_skips_invalid_keeps_valid():
    from agent import history_filter as hf
    compiled = hf._compile(["[unclosed", r"^assistant\t.*PROT-"])
    assert len(compiled) == 1


# ── load_compiled + cache (precisa do engine → build_app) ───────────────────

def test_load_compiled_reads_config_and_caches(build_app):
    build_app(["gowa"])
    from agent import history_filter as hf
    from db.repositories import config_repo

    hf.reset_cache()
    config_repo.set(hf.CONFIG_KEY, [r"^private_note\t", "Protocolo aberto"])
    compiled = hf.load_compiled()
    assert len(compiled) == 2
    # Segunda chamada dentro do TTL reusa o MESMO objeto compilado (não relê a config).
    assert hf.load_compiled() is compiled
    hf.reset_cache()


def test_load_compiled_empty_default_means_no_filter(build_app):
    build_app(["gowa"])
    from agent import history_filter as hf
    from db.repositories import config_repo

    hf.reset_cache()
    config_repo.set(hf.CONFIG_KEY, [])
    assert hf.load_compiled() == []
    hf.reset_cache()


def test_load_compiled_skips_invalid_regex_fail_open(build_app):
    build_app(["gowa"])
    from agent import history_filter as hf
    from db.repositories import config_repo

    hf.reset_cache()
    config_repo.set(hf.CONFIG_KEY, ["[bad(regex", "valido.*"])
    compiled = hf.load_compiled()
    assert len(compiled) == 1
    hf.reset_cache()


# ── Fase A1 — wiring no repo + memory (fim-a-fim) ──────────────────────────

def test_get_context_messages_cuts_private_note(build_app):
    build_app(["gowa"])
    from agent.memory import ContactMemory
    from agent import history_filter as hf
    from db.repositories import config_repo

    hf.reset_cache()
    config_repo.set(hf.CONFIG_KEY, [r"^private_note\t"])
    mem = ContactMemory("5511932000001")
    mem.add_message("user", "oi")
    mem.add_message("private_note", "🔖 Protocolo aberto · PROT-1")
    mem.add_message("assistant", "olá! como posso ajudar?")

    ctx = mem.get_context_messages(50)
    # A nota privada NÃO chega ao LLM (nem embrulhada como "[Nota privada…]").
    assert all("[Nota privada do operador]" not in (m.get("content") or "") for m in ctx)
    assert [m["role"] for m in ctx] == ["user", "assistant"]
    hf.reset_cache()


def test_get_context_messages_empty_keeps_private_note(build_app):
    """Regressão: lista vazia = comportamento atual (nota privada vira '[Nota privada…]')."""
    build_app(["gowa"])
    from agent.memory import ContactMemory
    from agent import history_filter as hf
    from db.repositories import config_repo

    hf.reset_cache()
    config_repo.set(hf.CONFIG_KEY, [])
    mem = ContactMemory("5511932000002")
    mem.add_message("user", "oi")
    mem.add_message("private_note", "nota do atendente humano")
    mem.add_message("assistant", "olá!")

    ctx = mem.get_context_messages(50)
    assert any((m.get("content") or "") == "[Nota privada do operador]: nota do atendente humano"
               for m in ctx)
    hf.reset_cache()


def test_get_context_over_fetch_preserves_n(build_app):
    """D4: cortar linhas NÃO encolhe a janela — over-fetch entrega ``limit`` sobreviventes."""
    build_app(["gowa"])
    from agent import history_filter as hf
    from db.repositories import contact_repo, message_repo

    c = contact_repo.get_or_create("5511932000003")
    cid = c["id"]
    base = 1_000_000.0  # ts determinístico (Date.now indisponível não importa aqui)
    for i in range(10):
        message_repo.add(cid, "user", f"msg{i}", ts=base + i * 2)
        message_repo.add(cid, "private_note", f"🔖 nota{i}", ts=base + i * 2 + 1)

    compiled = hf._compile([r"^private_note\t"])
    rows = message_repo.get_context(cid, 5, exclude=compiled)
    # Sem over-fetch, pegar as 5 linhas mais recentes traria notas e sobrariam ~2 users.
    assert len(rows) == 5
    assert all(r["role"] == "user" for r in rows)
    assert [r["content"] for r in rows] == ["msg5", "msg6", "msg7", "msg8", "msg9"]


def test_get_context_no_exclude_byte_identical(build_app):
    """exclude=None ⇒ mesmíssimo resultado de antes (mesma contagem/ordem)."""
    build_app(["gowa"])
    from db.repositories import contact_repo, message_repo

    c = contact_repo.get_or_create("5511932000004")
    cid = c["id"]
    base = 2_000_000.0
    for i in range(6):
        message_repo.add(cid, "user", f"u{i}", ts=base + i)

    without = message_repo.get_context(cid, 4)
    none_explicit = message_repo.get_context(cid, 4, exclude=None)
    empty = message_repo.get_context(cid, 4, exclude=[])
    assert [m["content"] for m in without] == ["u2", "u3", "u4", "u5"]
    assert without == none_explicit == empty


# ── Fase E0 — endpoint round-trip + improvement (alvo protegido) ────────────

def test_config_endpoint_round_trip(client):
    """PUT persiste a lista de regex; o GET seguinte a devolve igual."""
    patterns = [r"^private_note\t", r"PROT-\d{8}"]
    r = client.put("/api/config", json={"ai_history_exclude_patterns": patterns})
    assert r.status_code == 200
    r = client.get("/api/config")
    assert r.json()["data"]["ai_history_exclude_patterns"] == patterns
