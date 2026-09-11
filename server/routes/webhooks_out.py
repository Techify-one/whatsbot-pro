"""Gestão dos webhooks de SAÍDA no painel (fase 8 do plano de API).

Cadastrar endpoint, escolher os eventos, testar, ver as últimas entregas e
remover. Gated por ``webhook.manage`` — admin-only pelo mesmo motivo da chave de
API: quem cadastra um endpoint escolhe para onde os eventos da instalação vão.

O SEGREDO do HMAC aparece **uma única vez**, na criação, como o segredo da chave
— e por isso é regenerável (``POST /{id}/rotate-secret``), já que não há como
recuperá-lo.

⚠️ Não confundir com ``/api/webhook/{provider}/{channel_id}``, que é o webhook de
ENTRADA (o provedor nos chamando). Aqui é o contrário: nós chamando o integrador.
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import Depends, Request

from db import audit_actions
from db.repositories import webhook_repo
from server import audit_listener, webhook_dispatcher
from server.authz import current_user
from server.deps import install_exception_handlers, require_permission
from server.helpers import _err, _ok

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _public(row: dict) -> dict:
    """Forma pública — NUNCA o segredo (só se ele existe)."""
    return {
        "id": row.get("id"),
        "url": row.get("url"),
        "description": row.get("description") or "",
        "events": row.get("events") or [],
        "enabled": bool(row.get("enabled")),
        "created_at": row.get("created_at"),
        "last_delivery_at": row.get("last_delivery_at"),
        "last_status": row.get("last_status"),
        "failure_streak": row.get("failure_streak") or 0,
        "disabled_reason": row.get("disabled_reason"),
    }


def _validate(url: str, events) -> str | None:
    if not url or not _URL_RE.match(url):
        return "Informe uma URL http(s) válida."
    if url.lower().startswith("http://") and not url.lower().startswith("http://localhost"):
        # Aviso, não bloqueio: o corpo é assinado, mas trafega em claro.
        logger.warning("[Webhook] endpoint em HTTP sem TLS: %s", url)
    if not isinstance(events, list) or not events:
        return "Escolha ao menos um evento (ou \"*\")."
    if any(not isinstance(e, str) or not e.strip() for e in events):
        return "Lista de eventos inválida."
    return None


def register_routes(app, deps):
    install_exception_handlers(app)

    @app.get("/api/webhooks",
             dependencies=[Depends(require_permission("webhook.manage"))])
    async def list_webhooks(request: Request):
        rows = await asyncio.to_thread(webhook_repo.list_endpoints)
        return _ok({
            "items": [_public(r) for r in rows],
            # O catálogo do que ``"*"`` cobre — a tela monta o seletor a partir
            # daqui em vez de carregar uma lista escrita à mão que envelhece.
            "exportable_events": sorted(webhook_dispatcher.EXPORTABLE_EVENTS),
        })

    @app.post("/api/webhooks",
              dependencies=[Depends(require_permission("webhook.manage"))])
    async def create_webhook(body: dict, request: Request):
        url = (body.get("url") or "").strip()
        events = body.get("events")
        err = _validate(url, events)
        if err:
            return _err(err)
        secret = webhook_dispatcher.generate_secret()
        actor = current_user(request)
        row = await asyncio.to_thread(
            webhook_repo.create_endpoint, url=url, secret=secret,
            events=[str(e).strip() for e in events],
            description=(body.get("description") or "").strip(),
            created_by=(actor.get("id") if actor else None))
        webhook_dispatcher.invalidate_cache()
        await asyncio.to_thread(
            audit_listener.record,
            action=audit_actions.AuditAction.WEBHOOK_CREATE,
            resource_type=audit_actions.ResourceType.WEBHOOK,
            resource_id=row["id"],
            after={"url": url, "events": row.get("events")})   # NUNCA o segredo
        data = _public(row)
        data["secret"] = secret   # a única vez que ele aparece
        return _ok(data)

    @app.put("/api/webhooks/{endpoint_id}",
             dependencies=[Depends(require_permission("webhook.manage"))])
    async def update_webhook(endpoint_id: int, body: dict, request: Request):
        row = await asyncio.to_thread(webhook_repo.get_endpoint, endpoint_id)
        if row is None:
            return _err("Webhook não encontrado.", 404)
        fields: dict = {}
        if "url" in body:
            url = (body.get("url") or "").strip()
            if not _URL_RE.match(url):
                return _err("Informe uma URL http(s) válida.")
            fields["url"] = url
        if "events" in body:
            events = body.get("events")
            if not isinstance(events, list) or not events:
                return _err("Escolha ao menos um evento (ou \"*\").")
            fields["events"] = [str(e).strip() for e in events]
        if "description" in body:
            fields["description"] = (body.get("description") or "").strip()
        if "enabled" in body:
            fields["enabled"] = 1 if body.get("enabled") else 0
            if fields["enabled"]:
                # Reabilitar limpa o auto-desligamento — senão o endpoint voltaria
                # já contando as falhas antigas e cairia de novo na hora.
                fields["failure_streak"] = 0
                fields["disabled_reason"] = None
        updated = await asyncio.to_thread(
            webhook_repo.update_endpoint, endpoint_id, **fields)
        webhook_dispatcher.invalidate_cache()
        await asyncio.to_thread(
            audit_listener.record,
            action=audit_actions.AuditAction.WEBHOOK_UPDATE,
            resource_type=audit_actions.ResourceType.WEBHOOK,
            resource_id=endpoint_id,
            before={"url": row.get("url"), "events": row.get("events"),
                    "enabled": bool(row.get("enabled"))},
            after={k: v for k, v in fields.items() if k != "secret"})
        return _ok(_public(updated))

    @app.post("/api/webhooks/{endpoint_id}/rotate-secret",
              dependencies=[Depends(require_permission("webhook.manage"))])
    async def rotate_secret(endpoint_id: int, request: Request):
        """Gera um segredo novo (o antigo deixa de assinar imediatamente).

        Existe porque o segredo não é recuperável: perdido o valor, a única saída
        seria recadastrar o endpoint e perder o histórico de entregas."""
        if await asyncio.to_thread(webhook_repo.get_endpoint, endpoint_id) is None:
            return _err("Webhook não encontrado.", 404)
        secret = webhook_dispatcher.generate_secret()
        row = await asyncio.to_thread(
            webhook_repo.update_endpoint, endpoint_id, secret=secret)
        webhook_dispatcher.invalidate_cache()
        await asyncio.to_thread(
            audit_listener.record,
            action=audit_actions.AuditAction.WEBHOOK_UPDATE,
            resource_type=audit_actions.ResourceType.WEBHOOK,
            resource_id=endpoint_id, after={"secret_rotated": True})
        data = _public(row)
        data["secret"] = secret
        return _ok(data)

    @app.post("/api/webhooks/{endpoint_id}/test",
              dependencies=[Depends(require_permission("webhook.manage"))])
    async def test_webhook(endpoint_id: int, request: Request):
        """POSTa um ``webhook.test`` SINCRONAMENTE e devolve o que o destino
        respondeu — o operador precisa do veredito na hora, não na fila."""
        row = await asyncio.to_thread(webhook_repo.get_endpoint, endpoint_id)
        if row is None:
            return _err("Webhook não encontrado.", 404)
        result = await webhook_dispatcher.send_test(row)
        if not result["ok"]:
            return _err(f"O destino não aceitou: {result['error'] or 'sem resposta'}",
                        status=502, data=result)
        return _ok(result)

    @app.get("/api/webhooks/{endpoint_id}/deliveries",
             dependencies=[Depends(require_permission("webhook.manage"))])
    async def list_deliveries(endpoint_id: int, request: Request,
                              limit: int = 50, status: str | None = None):
        rows = await asyncio.to_thread(
            webhook_repo.list_deliveries, endpoint_id,
            limit=max(1, min(int(limit), 200)), status=status)
        return _ok({"items": rows})

    @app.delete("/api/webhooks/{endpoint_id}",
                dependencies=[Depends(require_permission("webhook.manage"))])
    async def delete_webhook(endpoint_id: int, request: Request):
        row = await asyncio.to_thread(webhook_repo.get_endpoint, endpoint_id)
        if row is None:
            return _err("Webhook não encontrado.", 404)
        await asyncio.to_thread(webhook_repo.delete_endpoint, endpoint_id)
        webhook_dispatcher.invalidate_cache()
        await asyncio.to_thread(
            audit_listener.record,
            action=audit_actions.AuditAction.WEBHOOK_DELETE,
            resource_type=audit_actions.ResourceType.WEBHOOK,
            resource_id=endpoint_id,
            before={"url": row.get("url"), "events": row.get("events")})
        return _ok({"deleted": endpoint_id})
