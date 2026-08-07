"""Busca de mensagens DENTRO de uma conversa (plano 99 · F1).

Irmã escopada da busca global da sidebar. As duas procuram a mesma coisa e no
mesmo índice, mas respondem a perguntas diferentes:

* :mod:`db.search.contact_search` — "**quais conversas** falam disto?" Devolve
  ``DISTINCT ON (contact_id)``, ou seja **um** hit por contato, porque a sidebar
  mostra uma linha por contato. Este módulo **não a altera** (plano 99 · D1).
* aqui — "**onde**, dentro desta conversa, isto foi dito?" Devolve a LISTA de
  ocorrências, mais recente primeiro (a ordem que o WhatsApp usa), para o
  operador navegar com ⌃/⌄.

Tudo o que decide *o que casa* é IMPORTADO de ``contact_search``, nunca
reescrito: a expressão dobrada (``f_unaccent(lower(col)) ILIKE …``) precisa
casar **byte a byte** com o índice parcial ``idx_msg_content_trgm`` (migration
0060) — qualquer divergência torna o índice inaplicável e a busca vira seq scan
em silêncio. O mesmo vale para ``SEARCH_EXCLUDED_ROLES``, que é literalmente o
predicado desse índice.

Alvo da busca (P4 · v1): só ``messages.content``. Ele já é COMPOSTO — a descrição
de imagem e a extração de documento reescrevem o content — então legenda e
descrição já são alcançáveis por ele. Ampliar para ``media_caption`` é aditivo e
fica para quando pedirem.
"""

from __future__ import annotations

from sqlalchemy import and_, func, select

from db.engine import get_engine
from db.search.contact_search import (SEARCH_EXCLUDED_ROLES, TRIGRAM_MIN_LEN,
                                      _folded_match, _folded_pattern, fold,
                                      match_snippet)
from db.tables import messages

# Teto de ocorrências por página. O contador ("3 de 12") usa o ``total``, que é
# uma contagem separada e exata — a página é só o que a navegação consome.
DEFAULT_LIMIT = 50


def _thread_cond(conversation_id: int | None, contact_id: int | None):
    if conversation_id is not None:
        return messages.c.conversation_id == conversation_id
    if contact_id is not None:
        return messages.c.contact_id == contact_id
    raise ValueError("informe conversation_id ou contact_id")


def _scoped(conversation_id: int | None, contact_id: int | None):
    """A thread, já sem as linhas que a busca nunca enxerga, atrás de uma CERCA
    de otimização (``OFFSET 0``) — plano 99 · F5·1.

    Medido em 600 mil mensagens (uma conversa de 15 mil), o Postgres escolhia
    ``idx_msg_content_trgm`` e só DEPOIS filtrava por conversa. Com um termo raro
    isso é ótimo (4 ms); com um termo comum ele varria 160 mil linhas para achar
    4 mil da conversa e levava **1,3 s**. Ou seja: o custo escalava com a
    frequência do termo no BANCO INTEIRO — um eixo que o operador não controla e
    que só piora conforme o banco cresce.

    ``OFFSET 0`` impede o achatamento da subconsulta, então o escopo da conversa
    é resolvido primeiro (por ``idx_msg_conversation_ts``, que já existe — **sem
    DDL nova**, que é a resposta medida para a P3). O custo passa a escalar com o
    tamanho da CONVERSA: ~50 ms, constante, para qualquer termo. Paga-se ~45 ms a
    mais no termo raro para eliminar o pico de 1,3 s — troca fácil num campo de
    busca com debounce de 300 ms.
    """
    return (
        select(messages.c.id, messages.c.ts, messages.c.role, messages.c.content)
        .where(and_(
            _thread_cond(conversation_id, contact_id),
            messages.c.content != "",
            messages.c.role.notin_(SEARCH_EXCLUDED_ROLES),
        ))
        .offset(0)
        .subquery("thread")
    )


def search_in_conversation(*, q: str, conversation_id: int | None = None,
                           contact_id: int | None = None,
                           limit: int = DEFAULT_LIMIT,
                           offset: int = 0) -> dict:
    """Ocorrências de ``q`` na thread, mais recente primeiro.

    Devolve ``{"matches": [{"id", "ts", "role", "snippet"}], "total": int}``.

    ``q`` abaixo de ``TRIGRAM_MIN_LEN`` (3) devolve **vazio explícito**, não erro
    — o MESMO piso do ramo de conteúdo da busca global, mantido aqui por dois
    motivos: uma busca de 1-2 letras casaria quase toda mensagem da thread (ruído
    caro, não resultado), e os dois campos de busca do painel discordarem sobre o
    que é um termo válido seria confuso. Aqui o piso não é uma restrição do
    índice: com a cerca de :func:`_scoped` a varredura já é da conversa, não do
    trigram global.

    O ``snippet`` é recortado em Python (±40 chars em torno do 1º casamento,
    preservando os acentos ORIGINAIS) e só para as linhas da página — nunca para
    a thread inteira.
    """
    folded = fold(q or "")
    if len(folded) < TRIGRAM_MIN_LEN:
        return {"matches": [], "total": 0}

    thread = _scoped(conversation_id, contact_id)
    match = _folded_match(thread.c.content, _folded_pattern(q))

    with get_engine().connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(thread).where(match)
        ).scalar() or 0
        rows = conn.execute(
            select(thread.c.id, thread.c.ts, thread.c.role, thread.c.content)
            .where(match)
            .order_by(thread.c.ts.desc(), thread.c.id.desc())
            .limit(limit).offset(offset)
        ).mappings().all()

    return {
        "matches": [{
            "id": r["id"],
            "ts": r["ts"],
            "role": r["role"],
            "snippet": match_snippet(r["content"] or "", folded),
        } for r in rows],
        "total": int(total),
    }
