"""Veredito de saúde do plugin — uma implementação só.

Extraído de ``routes.health`` (F2 do plano "Trackify vira o único portão") para
que a rota HTTP e a op de serviço ``status()`` (``services.py``) contem a MESMA
história em vez de duas leituras que podem divergir.

Distingue "não configurado" de "inalcançável" de "alcançável mas não vai
funcionar" — sem isso, um escopo faltando na chave viraria uma tela vazia sem
explicação. Nunca levanta: reportar "não configurado" É o trabalho dela.
"""

from __future__ import annotations

import asyncio

from . import _config
from . import client as tk_client

# Escopos sem os quais a sincronização de campos não escreve nada.
REQUIRED_SCOPES = ("read", "contacts:write")


async def build(http) -> dict:
    """O dicionário de saúde. ``http`` é um ``httpx.AsyncClient`` já aberto."""
    configured = await asyncio.to_thread(tk_client.is_configured)
    reachable, message = False, "API key do Trackify não configurada."
    escopos: list = []
    if configured:
        res = await tk_client.whoami(http)
        reachable = res.ok
        if res.ok:
            escopos = list((res.data or {}).get("scopes") or [])
            message = ""
        else:
            message = res.error

    # "Schema ok" virou "a chave tem os escopos de que o plugin precisa": o
    # equivalente honesto agora que o plugin não conhece mais tabela nenhuma do
    # CDP. Continua distinguindo "não configurado" de "inalcançável" de
    # "alcançável mas não vai funcionar" — que é o ponto da rota.
    faltando = [e for e in REQUIRED_SCOPES if e not in escopos] if reachable else []
    return {
        "configured": configured,
        "reachable": reachable,
        "message": message,
        "schema_ok": reachable and not faltando,
        "schema_missing": faltando,
        "scopes": escopos,
        "base_url_set": bool(_config.nexus_base_url()),
        "mirror_enabled": bool(_config.setting("mirror_enabled", False)),
        "field_sync_enabled": bool(_config.setting("field_sync_enabled", False)),
        "field_sync_credential_set": await asyncio.to_thread(_config.credential_set),
    }
