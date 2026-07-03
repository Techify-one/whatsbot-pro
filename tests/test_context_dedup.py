"""12.2 (plano 31 F5) — dedup de contexto do LLM.

Respostas ``assistant`` textualmente idênticas e ADJACENTES são colapsadas SÓ
na montagem do contexto (``ContactMemory.get_context_messages``); o histórico
persistido (``message_repo.get_all`` / painel) não muda. Idênticas mas NÃO
adjacentes (com turno de usuário no meio) são preservadas.
"""

from __future__ import annotations


def test_adjacent_identical_assistant_collapse(build_app):
    build_app(["gowa"])
    from agent.memory import ContactMemory
    from db.repositories import message_repo

    phone = "5511931000001"
    mem = ContactMemory(phone)
    mem.add_message("user", "oi")
    mem.add_message("assistant", "mesma resposta")
    mem.add_message("assistant", "mesma resposta")
    mem.add_message("assistant", "mesma resposta")
    mem.add_message("user", "e agora?")
    mem.add_message("assistant", "outra resposta")

    ctx = mem.get_context_messages(50)
    assistants = [m["content"] for m in ctx if m["role"] == "assistant"]
    assert assistants == ["mesma resposta", "outra resposta"], assistants

    # Histórico persistido inalterado: as 3 linhas idênticas continuam lá.
    rows = message_repo.get_all(mem.id)
    saved = [r["content"] for r in rows if r["role"] == "assistant"]
    assert saved.count("mesma resposta") == 3


def test_non_adjacent_identical_assistant_kept(build_app):
    build_app(["gowa"])
    from agent.memory import ContactMemory

    phone = "5511931000002"
    mem = ContactMemory(phone)
    mem.add_message("assistant", "resposta X")
    mem.add_message("user", "outra pergunta")
    mem.add_message("assistant", "resposta X")

    ctx = mem.get_context_messages(50)
    assistants = [m["content"] for m in ctx if m["role"] == "assistant"]
    assert assistants == ["resposta X", "resposta X"], assistants
