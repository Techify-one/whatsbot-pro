"""Política transversal de paginação — teto em toda leitura de coleção (plano 50).

Ponto ÚNICO que capa o ``limit`` de input e nomeia os defaults, para todo endpoint
reusar. A política:

- **Todo endpoint que lista coleção** passa o ``limit`` recebido por :func:`clamp_limit`
  antes de chegar ao banco — ``?limit=99999`` nunca excede o ``cap``.
- **Coleções que crescem sem teto** (mensagens de uma conversa, contatos, uso por
  contato) usam **paginação real** (keyset ``before_id`` para o chat; ``limit/offset``
  para listas/relatórios), não "buscar tudo".
- Callers internos (motor LLM, manutenção) que não passam ``limit`` seguem no caminho
  legado (sem teto) — as assinaturas de repo ganham parâmetros **opcionais**.

O molde de coerção é o mesmo de ``db/filters/spec.py`` (``_coerce_int``): coage para
int, clampa no intervalo e cai no default quando o valor não parseia.
"""

from __future__ import annotations

# Defaults nomeados — mensagens (chat, keyset) e listas/relatórios (limit/offset).
PAGE_MSGS, CAP_MSGS = 50, 200
PAGE_LIST, CAP_LIST = 50, 200

# Teto de segurança do offset (evita ?offset=1e18 estourar a query).
MAX_OFFSET = 10_000_000


def clamp_limit(value, default: int, cap: int) -> int:
    """Capa um ``limit`` de input no intervalo ``[1, cap]``.

    ``None``/vazio/não-numérico ⇒ ``default``. Valor abaixo de 1 (0/negativo) sobe
    para 1 — uma página nunca é vazia por parâmetro. Acima de ``cap`` desce para ``cap``.

    >>> clamp_limit(9_000_000, 50, 200)
    200
    >>> clamp_limit(None, 50, 200)
    50
    >>> clamp_limit(-5, 50, 200)
    1
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, cap))


def clamp_offset(value, default: int = 0) -> int:
    """Capa um ``offset`` de input no intervalo ``[0, MAX_OFFSET]``.

    ``None``/vazio/não-numérico ⇒ ``default`` (0). Negativo sobe para 0.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(n, MAX_OFFSET))
