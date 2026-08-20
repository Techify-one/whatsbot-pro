"""Webhooks de SAÍDA: captura no barramento + entrega com retry (fase 8).

Dois pedaços, deliberadamente separados:

* :func:`webhook_event_handler` — um subscriber ``*`` no barramento, no molde do
  :mod:`server.audit_listener`. Ele só faz o barato: confere a allowlist,
  descobre quais endpoints assinam aquele evento e **enfileira** uma linha por
  endpoint. Nada de rede no caminho da request.
* :func:`deliver_due` — o worker (rodado pelo loop supervisionado
  ``webhook_delivery`` em :mod:`server.background`): pega as entregas vencidas,
  POSTa com assinatura HMAC e re-agenda com backoff, mandando para a dead-letter
  quando esgota.

**Eventos de plugin viajam de graça**: plugin emite no MESMO barramento, então
``protocolos``, ``retornos`` e companhia entregam eventos sem escrever transporte
nenhum. Um plugin que precise de formato de terceiro implementa o seu e não passa
por aqui.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import hmac
import json
import logging
import secrets
import time

from db.repositories import webhook_repo

logger = logging.getLogger(__name__)

_CORE_PLUGIN_ID = "__core_webhooks__"

TIMEOUT_SECONDS = 10.0
SIGNATURE_HEADER = "X-Whatsbot-Signature-256"
EVENT_HEADER = "X-Whatsbot-Event"
DELIVERY_HEADER = "X-Whatsbot-Delivery"

# Allowlist do que PODE sair da instalação. Curada de propósito — ``KNOWN_EVENTS``
# inteiro incluiria coisas que não são de negócio e que um endpoint externo não
# tem por que ver (``llm.before``/``llm.after`` carregam o histórico da conversa
# e o prompt; ``presence.changed``/``receipt.changed`` são altíssimo volume;
# ``task.crashed``/``subprocess.*`` são diagnóstico interno).
#
# Eventos de PLUGIN não estão listados e são aceitos por PADRÃO de outra forma:
# o endpoint precisa nomeá-los explicitamente (ou por curinga, ex.
# ``protocolos.*``). É o que mantém ``["*"]`` restrito ao conjunto curado abaixo.
EXPORTABLE_EVENTS = frozenset({
    "message.saved", "message.sent", "message.failed",
    "message.reaction", "message.edited", "message.revoked", "message.deleted",
    "contact.updated", "contact.ai_toggled", "contact.tagged", "contact.untagged",
    "conversation.created", "conversation.status_changed", "conversation.reopened",
    "conversation.assigned", "conversation.unassigned", "conversation.archived",
    "conversation.ai_toggled", "conversation.ai_takeover",
    "conversation.transferred_to_human", "conversation.agent_changed",
    "conversation.attribute_set", "conversation.labeled", "conversation.deleted",
    "tag.created", "tag.updated", "tag.deleted",
    "channel.created", "channel.updated", "channel.deleted",
    "channel.session_action", "channel.duplicate_refused",
    "connection.changed",
})

# Cache dos endpoints ATIVOS. O handler roda em TODO evento do barramento
# (inclusive ``message.received``, que é caminho quente), e sem isto ele faria uma
# consulta por evento só para descobrir, quase sempre, que não há endpoint
# nenhum. TTL curto + invalidação explícita nas escritas: cadastrar/editar/
# remover vale na hora, e o pior caso é 5s de atraso se outra réplica escreveu.
_CACHE_TTL = 5.0
_cache: dict = {"at": 0.0, "rows": []}


def invalidate_cache() -> None:
    """Chamada pelas rotas de gestão — o cadastro novo vale imediatamente."""
    _cache["at"] = 0.0


def _enabled_endpoints() -> list[dict]:
    now = time.time()
    if now - _cache["at"] > _CACHE_TTL:
        _cache["rows"] = webhook_repo.list_endpoints(only_enabled=True)
        _cache["at"] = now
    return _cache["rows"]


# Chaves que NUNCA saem no corpo. O ``raw`` do provedor pode carregar base64 de
# mídia inteira (payload de megabytes) e as demais são segredo por definição.
_STRIPPED_KEYS = frozenset({
    "raw", "access_token", "api_key", "apikey", "password", "password_hash",
    "token", "secret", "credentials", "authorization", "verify_token",
    "client_secret", "private_key", "openrouter_api_key",
})


def generate_secret() -> str:
    """Segredo do HMAC de um endpoint novo (mostrado uma vez, como a chave de API)."""
    return f"whsec_{secrets.token_urlsafe(32)}"


def sanitize(payload) -> dict:
    """Remove segredo e volume do corpo, recursivamente. Também tira as chaves
    reservadas ``_audit_*`` (contrato interno do listener de auditoria)."""
    if isinstance(payload, dict):
        return {k: sanitize(v) for k, v in payload.items()
                if not (isinstance(k, str)
                        and (k.lower() in _STRIPPED_KEYS or k.startswith("_audit_")))}
    if isinstance(payload, (list, tuple)):
        return [sanitize(v) for v in payload]
    return payload


def event_allowed(event: str, subscriptions) -> bool:
    """O endpoint assina este evento?

    ``"*"`` significa "todo o conjunto curado" (:data:`EXPORTABLE_EVENTS`) — NÃO
    "qualquer coisa que passar pelo barramento". Um evento de plugin precisa ser
    nomeado, direto (``protocolos.ciclo.aberto``) ou por curinga
    (``protocolos.*``). É o que impede que um endpoint cadastrado hoje comece a
    receber, num upgrade, um evento novo que ninguém revisou.
    """
    for sub in (subscriptions or []):
        sub = str(sub)
        if sub == "*":
            if event in EXPORTABLE_EVENTS:
                return True
            continue
        if sub == event:
            return True
        if ("*" in sub or "?" in sub) and fnmatch.fnmatchcase(event, sub):
            return True
    return False


def signature_for(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` — HMAC-SHA256 do CORPO EXATO que foi enviado.

    O destinatário recalcula sobre os bytes crus recebidos; re-serializar o JSON
    do outro lado quebraria a comparação (mesma armadilha do webhook da Meta).
    """
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


# ── Captura (barramento) ────────────────────────────────────────────────────

def webhook_event_handler(ctx, payload: dict) -> None:
    """Enfileira o evento para cada endpoint que o assina. Nunca levanta no bus."""
    try:
        event = getattr(ctx, "event_name", "") or ""
        if not event:
            return
        endpoints = _enabled_endpoints()
        if not endpoints:
            return
        body = None
        for ep in endpoints:
            if not event_allowed(event, ep.get("events")):
                continue
            if body is None:   # só serializa se ALGUÉM assina (caminho quente)
                body = sanitize(payload or {})
            webhook_repo.enqueue(ep["id"], event, body)
    except Exception as e:  # noqa: BLE001 — entrega nunca quebra o barramento
        logger.warning("[Webhook] captura falhou para %s: %s",
                       getattr(ctx, "event_name", "?"), e)


def register_webhook_listener() -> None:
    """Liga o handler ``*``. Chamar uma vez no lifespan, junto do de auditoria."""
    from plugins.events import register
    register(_CORE_PLUGIN_ID, "*", webhook_event_handler)
    logger.info("Core webhook dispatcher registered (%d eventos exportáveis)",
                len(EXPORTABLE_EVENTS))


# ── Entrega (worker) ────────────────────────────────────────────────────────

async def _post(client, endpoint: dict, delivery: dict) -> tuple[bool, int | None, str]:
    envelope = {
        "id": delivery["id"],
        "event": delivery["event"],
        "created_at": delivery.get("created_at"),
        "data": delivery.get("payload") or {},
    }
    body = json.dumps(envelope, ensure_ascii=False, default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: delivery["event"],
        DELIVERY_HEADER: str(delivery["id"]),
        SIGNATURE_HEADER: signature_for(endpoint["secret"], body),
        "User-Agent": "WhatsBot-Pro-Webhook/1",
    }
    try:
        resp = await client.post(endpoint["url"], content=body, headers=headers,
                                 timeout=TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001 — rede fora do ar é falha comum, não bug
        return False, None, f"{type(e).__name__}: {e}"
    ok = 200 <= resp.status_code < 300
    return ok, resp.status_code, ("" if ok else f"HTTP {resp.status_code}")


async def deliver_due(limit: int = 50) -> int:
    """Tenta as entregas vencidas. Devolve quantas foram processadas."""
    import httpx

    due = await asyncio.to_thread(webhook_repo.due_deliveries, limit)
    if not due:
        return 0
    endpoints = {e["id"]: e for e in await asyncio.to_thread(webhook_repo.list_endpoints)}
    processed = 0
    async with httpx.AsyncClient(follow_redirects=False) as client:
        for delivery in due:
            endpoint = endpoints.get(delivery["endpoint_id"])
            if endpoint is None or not endpoint.get("enabled"):
                # Endpoint apagado/desligado no meio do caminho: a entrega vira
                # dead-letter em vez de ficar re-tentando para sempre.
                await asyncio.to_thread(
                    webhook_repo.mark_failed, delivery["id"],
                    webhook_repo.MAX_ATTEMPTS,
                    error="Endpoint desabilitado ou removido.")
                processed += 1
                continue
            ok, status, error = await _post(client, endpoint, delivery)
            if ok:
                await asyncio.to_thread(webhook_repo.mark_delivered, delivery["id"], status)
            else:
                new_status = await asyncio.to_thread(
                    webhook_repo.mark_failed, delivery["id"],
                    int(delivery.get("attempts") or 0) + 1,
                    error=error, response_status=status)
                if new_status == "dead":
                    logger.warning("[Webhook] entrega %s (%s) para %s virou dead-letter: %s",
                                   delivery["id"], delivery["event"],
                                   endpoint["url"], error)
            streak = await asyncio.to_thread(
                webhook_repo.record_endpoint_result, endpoint["id"],
                ok=ok, response_status=status)
            if streak >= webhook_repo.AUTO_DISABLE_STREAK:
                # O repo acabou de desligar o endpoint — o cache tem de saber, ou
                # ele seguiria enfileirando para um destino já desativado.
                invalidate_cache()
                logger.warning("[Webhook] endpoint %s desligado após %d falhas seguidas",
                               endpoint["id"], streak)
            processed += 1
    return processed


async def send_test(endpoint: dict) -> dict:
    """Dispara um ``webhook.test`` sincronamente — o "testar" da tela."""
    import httpx

    delivery = {"id": 0, "event": "webhook.test", "created_at": time.time(),
                "payload": {"message": "Teste de webhook do WhatsBot."}}
    async with httpx.AsyncClient(follow_redirects=False) as client:
        ok, status, error = await _post(client, endpoint, delivery)
    return {"ok": ok, "status": status, "error": error}
