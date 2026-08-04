"""Escrita de campos do contato no Trackify.

Ficou como **fachada fina** sobre ``client``: a mecânica de HTTP, a taxonomia de
veredito e a conferência campo a campo migraram para lá quando o plugin passou a
falar só HTTP autenticado por API key. Este módulo continua existindo porque os
call sites (``push``, testes) já raciocinam nos nomes de veredito publicados aqui,
e porque um nome estável custa menos que uma renomeação em cascata.

A ingestão (``POST /ingestion/...``, usada pelo espelho de eventos) continua não
servindo para este caminho, por duas razões estruturais que não mudaram: campo
identificador lá é "preenche-se-vazio" e nunca sobrescrito, e valor vazio é
recusado — ou seja, não dá para corrigir um e-mail nem para apagar um campo.
"""

from __future__ import annotations

from . import client

# Vereditos republicados a partir do cliente — ponto único de definição.
OK = client.OK
CONFLICT = client.CONFLICT
UNLINKED = client.UNLINKED
BLOCKED = client.BLOCKED
THROTTLED = client.THROTTLED
UNAUTHORIZED = client.UNAUTHORIZED
RETRY = client.RETRY

PutResult = client.Result


async def put_contact(http, trackify_contact_id: str, field_values: dict):
    """Grava ``{slug: valor}``. ``""`` APAGA o campo, chave omitida não é tocada.

    ⚠️ A assinatura perdeu o parâmetro ``session``: não há mais cookie a carregar,
    a credencial vive no ``client``.
    """
    return await client.put_contact(http, trackify_contact_id, field_values)
