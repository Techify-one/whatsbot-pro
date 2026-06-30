"""Filters do plugin Atendimentos.

``filter.conversation.before_status``: recusa fechar a conversa (HTTP 403) se os
campos OBRIGATÓRIOS de resolução (escopo ``conversa``) não estiverem gravados no
vínculo. Pareia com o popup do frontend, que grava os campos ANTES de chamar
``/status`` — então o caminho normal passa e uma chamada direta à API é barrada.
Desligável via setting ``enforce_backend``. Reabrir nunca é bloqueado.
"""

from . import logic

FILTERS = {
    "filter.conversation.before_status": logic.before_status,
}
