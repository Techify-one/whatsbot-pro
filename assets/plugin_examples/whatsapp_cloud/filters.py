"""Captura segura dos avisos da CONTA Meta pelo webhook cru (plano 84).

A Meta manda dois tipos de coisa no MESMO webhook, separados pelo
``change["field"]``: ``messages`` (conversa) e todo o resto (**a conta falando**:
template pausado, qualidade do número, tier de mensagens, conta restrita). O
WhatsBot só olhava o primeiro — o `parse_inbound` caminha `value.messages[]` e
`value.statuses[]`, então um aviso da conta produzia zero eventos e sumia.

Este observador usa `filter.webhook.payload`, o único gancho que enxerga o
payload antes do parse. O core entrega junto a procedência já resolvida
(`provider` + `channel_id`) e se a assinatura foi de fato autenticada. Um aviso
só sai quando os quatro guards casam: provider Cloud, canal Cloud ativo,
assinatura HMAC válida e `entry[].id` igual ao WABA ID daquele canal. Não existe
fallback para "único canal": uma rota pública não pode adivinhar o destino de um
efeito externo.

⚠️ CRÍTICO: devolver ``None`` neste filtro faz o core responder 200 **sem
processar**, ou seja, DESCARTA a mensagem. Este observador portanto **sempre**
devolve ``value`` intacto e engole toda exceção. Prioridade 9000 = roda por
último, nunca atrapalha outro filtro.

Custo no caminho quente: o guard é `raw.get("object")` + varredura dos `field`
dos `changes` — dois lookups de dict para o inbound normal, que é `messages` e
sai na primeira comparação. O trabalho de verdade (banco + rede) é **offloaded**
para fora do request, como no `janela_72h`.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Roda por último: observador nunca disputa com filtro que de fato transforma.
_PRIORITY = 9000

# O webhook já recebeu 200 quando esta task entrega ao Telegram. Uma falha
# transitória não terá retry da Meta, então fazemos duas novas tentativas curtas.
# Continua best-effort (sem outbox durável): restart no intervalo pode perder o
# evento, deliberadamente fora do escopo desta revisão.
_ACCOUNT_RETRY_DELAYS = (1.0, 3.0)

# Referências fortes das tasks fire-and-forget (o loop só guarda referência fraca).
_bg_tasks: set = set()


def account_changes(raw) -> list[dict]:
    """Extrai os avisos da CONTA de um payload cru da Meta (função PURA).

    Devolve uma lista de ``{field, value, waba_id}`` — um item por ``change``
    cujo ``field`` **não** seja ``messages``. Devolve ``[]`` para qualquer outra
    coisa (inbound normal, payload de GOWA/Telegram, lixo), e é isso que mantém o
    custo no caminho quente perto de zero.
    """
    if not isinstance(raw, dict):
        return []
    # Guard barato: só a Meta manda este envelope. GOWA/Telegram/website caem fora
    # aqui, na primeira comparação.
    if raw.get("object") != "whatsapp_business_account":
        return []
    out: list[dict] = []
    for entry in raw.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            field = (change.get("field") or "").strip()
            if not field or field == "messages":
                continue
            out.append({"field": field,
                        "value": change.get("value") or {},
                        "waba_id": str(entry.get("id") or "")})
    return out


def _authenticated_channel(source: dict, waba_id: str) -> str:
    """Canal autenticado para este aviso, ou vazio (fail-closed).

    A verificação criptográfica já ocorreu no provider usando os bytes crus. Aqui
    se faz defesa em profundidade contra contexto errado/forjado e se amarra o
    WABA do envelope à credencial exata do canal — sem fallback ambíguo.
    """
    source = source if isinstance(source, dict) else {}
    if source.get("provider") != "whatsapp_cloud":
        return ""
    if source.get("signature_authenticated") is not True:
        return ""
    channel_id = str(source.get("channel_id") or "").strip()
    waba_id = str(waba_id or "").strip()
    if not channel_id or not waba_id:
        return ""
    try:
        from db.repositories import channel_credential_repo, channel_repo

        row = channel_repo.get(channel_id) or {}
        if ((row.get("provider") or "") != "whatsapp_cloud"
                or not row.get("enabled", 1) or row.get("archived", 0)):
            return ""
        configured_waba = str(
            channel_credential_repo.get(channel_id, "waba_id") or ""
        ).strip()
    except Exception:  # core anterior/repositório indisponível: segurança fecha
        return ""
    return channel_id if configured_waba and configured_waba == waba_id else ""


async def _dispatch(hit: dict, source: dict) -> None:
    """Valida fora do request e só então entrega o alerta."""
    from . import alerts
    channel_id = await asyncio.to_thread(
        _authenticated_channel, source, hit.get("waba_id") or "")
    if not channel_id:
        logger.debug(
            "whatsapp_cloud: aviso de conta descartado (origem/assinatura/WABA "
            "não autenticados)"
        )
        return
    attempts = len(_ACCOUNT_RETRY_DELAYS) + 1
    for attempt in range(attempts):
        result = await alerts.handle_account_change(
            hit["field"], hit["value"], hit["waba_id"], channel_id)
        if result != "failed":
            return
        if attempt < len(_ACCOUNT_RETRY_DELAYS):
            await asyncio.sleep(_ACCOUNT_RETRY_DELAYS[attempt])
    logger.warning(
        "whatsapp_cloud: aviso de conta não entregue após %d tentativas (%s/%s)",
        attempts, channel_id, hit.get("field") or "unknown")


def _spawn(hit: dict, source: dict) -> None:
    """Manda validação+alerta para fora do request (o 200 da Meta é urgente)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        # Sem loop (filtro rodou num worker): nada a fazer aqui sem bloquear o
        # thread — o alerta é best-effort, então só registra.
        logger.debug("whatsapp_cloud: sem loop para despachar o alerta da conta")
        return
    task = loop.create_task(_dispatch(hit, dict(source or {})))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def observe(ctx, value):
    """Observador PASSA-TUDO: detecta o aviso da conta, despacha, devolve intacto."""
    try:
        hits = account_changes(value)
        if hits:
            extras = getattr(ctx, "extras", None) or {}
            for hit in hits:
                _spawn(hit, extras)
    except Exception:  # noqa: BLE001 — observador nunca atrapalha o webhook
        logger.debug("whatsapp_cloud: observe do aviso de conta falhou", exc_info=True)
    return value  # NUNCA None, NUNCA transformado


FILTERS = {"filter.webhook.payload": (observe, _PRIORITY)}
