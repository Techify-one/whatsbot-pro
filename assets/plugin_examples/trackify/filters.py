"""Um único filtro, e só para OBSERVAR: capturar quem fechou o atendimento.

Por que existe: o core zera ``assignee_user_id`` ANTES de emitir
``conversation.status_changed``, então "quem fechou" não chega no payload do
evento. Mas ``filter.conversation.clear_assignee_on_close`` é chamado com
``ctx_extras={"conversation_id", "user_id"}``, e esse ``user_id`` é o ATOR — quem
clicou em Resolver — que é uma informação melhor que o assignee.

⚠️ O filtro DEVOLVE O VALOR QUE RECEBEU, sempre. O plugin ``protocolos`` está na
mesma cadeia e pode legitimamente devolver ``False`` (a opção "manter o atendente
ao resolver"); devolver ``True`` fixo aqui mudaria o comportamento dele em
silêncio. Este plugin observa, nunca altera.
"""

from __future__ import annotations

import logging

from . import mirror

logger = logging.getLogger("plugins.trackify.filters")


def capture_closer(ctx, value):
    """Anota quem fechou e DEVOLVE ``value`` intacto. Nunca levanta."""
    try:
        extras = getattr(ctx, "extras", None) or {}
        mirror.remember_closer(extras.get("conversation_id"), extras.get("user_id"))
    except Exception:  # noqa: BLE001 — observar nunca pode quebrar o fechamento
        logger.debug("trackify: falha ao capturar quem fechou", exc_info=True)
    return value


# Prioridade MAIOR = roda por último (o core ordena ascendente). Queremos ver a
# decisão já tomada pelos outros e passá-la adiante sem tocar.
FILTERS = {
    "filter.conversation.clear_assignee_on_close": (capture_closer, 900),
}
