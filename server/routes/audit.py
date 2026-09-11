"""Audit trail read endpoints (plano 07 Fase 2). Gated by ``audit.read``.

List/filter (paginated), distinct actions for filter selects, and CSV/JSON
export. The export itself is auditable (``data.export``). All read-only — the
trail is append-only and never mutated here.
"""

import asyncio
import csv
import io
import json

from fastapi import Query, Request
from fastapi.responses import Response

from db import audit_actions
from db.repositories import audit_repo
from server.audit_context import get_current_actor
from server.authz import permission_denied
from server.helpers import _ok, _err

_EXPORT_CAP = 10000  # hard cap so an export never OOMs; surfaced in the response


def _filters_from_query(actor, actor_type, resource_type, resource_id, action, ts_from, ts_to,
                        api_key=None):
    return dict(
        actor_user_id=actor, actor_type=actor_type or None,
        resource_type=resource_type or None, resource_id=resource_id or None,
        action=action or None, ts_from=ts_from, ts_to=ts_to,
        api_key_id=api_key,
    )


def _api_key_labels() -> dict:
    """``{id: rótulo}`` das chaves — para a tela nomear a procedência em vez do id.

    Inclui as revogadas de propósito: a linha de auditoria é histórica e precisa
    continuar resolvendo o rótulo depois que a chave morreu.
    """
    try:
        from db.repositories import api_key_repo
        return {r["id"]: (r.get("label") or f"#{r['id']}")
                for r in api_key_repo.list_all()}
    except Exception:  # noqa: BLE001 — a trilha nunca deixa de ser lida por isto
        return {}


def register_routes(app, deps):

    @app.get("/api/audit")
    async def list_audit(
        request: Request,
        actor: int | None = None,
        actor_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        api_key: int | None = None,
        ts_from: float | None = Query(None, alias="from"),
        ts_to: float | None = Query(None, alias="to"),
        limit: int = 50,
        offset: int = 0,
    ):
        denied = permission_denied(request, "audit.read")
        if denied:
            return denied
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        f = _filters_from_query(actor, actor_type, resource_type, resource_id, action,
                                ts_from, ts_to, api_key)
        items = await asyncio.to_thread(lambda: audit_repo.query(limit=limit, offset=offset, **f))
        total = await asyncio.to_thread(lambda: audit_repo.count(**f))
        labels = await asyncio.to_thread(_api_key_labels)
        for it in items:
            kid = it.get("api_key_id")
            it["api_key_label"] = labels.get(kid) if kid else None
        return _ok({"items": items, "total": total, "limit": limit, "offset": offset,
                    "api_keys": [{"id": k, "label": v} for k, v in sorted(labels.items())]})

    @app.get("/api/audit/actions")
    async def audit_actions_list(request: Request):
        denied = permission_denied(request, "audit.read")
        if denied:
            return denied
        data = await asyncio.to_thread(audit_repo.distinct_actions)
        labels = await asyncio.to_thread(_api_key_labels)
        data["api_keys"] = [{"id": k, "label": v} for k, v in sorted(labels.items())]
        return _ok(data)

    @app.get("/api/audit/export")
    async def export_audit(
        request: Request,
        actor: int | None = None,
        actor_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        api_key: int | None = None,
        ts_from: float | None = Query(None, alias="from"),
        ts_to: float | None = Query(None, alias="to"),
        format: str = "csv",
    ):
        denied = permission_denied(request, "audit.read")
        if denied:
            return denied
        if format not in ("csv", "json"):
            return _err("Formato deve ser csv ou json.")
        f = _filters_from_query(actor, actor_type, resource_type, resource_id, action,
                                ts_from, ts_to, api_key)
        rows = await asyncio.to_thread(lambda: audit_repo.query(limit=_EXPORT_CAP, offset=0, **f))
        total = await asyncio.to_thread(lambda: audit_repo.count(**f))
        truncated = total > len(rows)

        # The export is itself an auditable action (data.export).
        actor_ctx = get_current_actor()
        await asyncio.to_thread(lambda: audit_repo.add(
            actor_user_id=actor_ctx.id, actor_type=actor_ctx.type, actor_label=actor_ctx.label,
            action=audit_actions.AuditAction.DATA_EXPORT, resource_type=audit_actions.ResourceType.DATA,
            resource_id="audit_log", after={"format": format, "count": len(rows), "truncated": truncated},
            ip_address=actor_ctx.ip, request_id=actor_ctx.request_id,
            api_key_id=actor_ctx.api_key_id))

        cols = ["id", "created_at", "actor_type", "actor_user_id", "actor_label",
                "api_key_id", "action", "resource_type", "resource_id", "ip_address",
                "request_id", "before_json", "after_json"]
        if format == "json":
            body = json.dumps(rows, ensure_ascii=False, default=str)
            media = "application/json"
            ext = "json"
        else:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
            body = buf.getvalue()
            media = "text/csv"
            ext = "csv"
        headers = {"Content-Disposition": f'attachment; filename="audit_log.{ext}"'}
        if truncated:
            headers["X-Audit-Truncated"] = str(total)  # surface the cap, never silent
        return Response(content=body, media_type=media, headers=headers)
