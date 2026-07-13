"""Resolução do TIPO DE CONTATO de um canal (plano tipos-de-contato).

O tipo é declarado pelo provider (``Channel.contact_type()``) e gravado em
``contacts.contact_type`` no INSERT do contato. Este helper faz a ponte "canal →
tipo" para os call sites do CORE que criam contatos FORA do fluxo inbound (que já
é coberto por ``ContactMemory``): check-phone, import CSV, auto-create no detalhe.

Genérico (sem ``if provider ==``): resolve a CLASSE do provider do canal via o
ChannelRegistry cabeado e lê ``contact_type()`` dela. Fail-open para ``"outros"``
sempre que o canal/provider não resolve (registry não cabeado, canal inexistente).
``channel_id`` vazio/None cai no canal ``default`` — que é o GOWA single-channel do
inbound —, então um contato criado sem canal explícito herda o tipo do canal padrão
em vez de um ``"outros"`` genérico.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_CONTACT_TYPE = "outros"
DEFAULT_CHANNEL_ID = "default"


def resolve_contact_type(channel_id: str | None) -> str:
    """Tipo de contato declarado pelo provider que dona ``channel_id``.

    Retorna ``"outros"`` quando o provider não pode ser resolvido — o mesmo
    fallback do ``source_id``/``ContactMemory``.
    """
    cid = (channel_id or "").strip() or DEFAULT_CHANNEL_ID
    try:
        from db.repositories import channel_repo
        row = channel_repo.get(cid)
        provider = (row or {}).get("provider")
    except Exception:
        logger.exception("Falha ao resolver canal %s para contact_type", cid)
        provider = None
    if not provider:
        return DEFAULT_CONTACT_TYPE
    try:
        from plugins.context import get_channel_runtime
        registry, _outbound, _ingest = get_channel_runtime()
        cls = registry.get_provider(provider) if registry is not None else None
        if cls is not None:
            return cls.contact_type() or DEFAULT_CONTACT_TYPE
    except Exception:
        logger.exception("Falha ao resolver contact_type do canal %s", cid)
    return DEFAULT_CONTACT_TYPE
