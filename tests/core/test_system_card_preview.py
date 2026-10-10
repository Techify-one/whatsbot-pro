"""Preview da lista para o card `system` notificado por plugin.

Puro (sem banco): ``system_card_preview`` extrai o texto da dúvida do card.
"""

from __future__ import annotations

from db.repositories.conversation_query import system_card_preview


def test_usa_o_trecho_citado_em_uma_linha():
    card = ("🎓 Dúvida de aula · Maria\nCurso › Aula\n[[quote]]\nNão entendi o NAT.\n\n"
            "Pode explicar?\n[[/quote]]\n[[cta:Abrir comentário|https://x.com/a]]")
    assert system_card_preview(card) == "Não entendi o NAT. Pode explicar?"


def test_sem_citacao_tira_os_tokens():
    card = "🎓 Dúvida de aula · Maria\n[[cta:Abrir comentário|https://x.com/a]]"
    assert system_card_preview(card) == "🎓 Dúvida de aula · Maria"


def test_vazio_ou_nulo():
    assert system_card_preview(None) == ""
    assert system_card_preview("") == ""
