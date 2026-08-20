"""Peças compartilhadas da fachada ``/api/v1`` (plano "Sistema de API com chave por usuário").

A fachada é FINA de propósito: ela traduz HTTP ↔ os serviços/repos que já
existem, e **não** reimplementa regra de negócio. O que ela tem de próprio:

* **DTO próprio, não o envelope da UI.** ``/api/*`` do painel devolve
  ``{ok, data|error}`` com HTTP 200 quase sempre — um contrato bom para o Preact
  e ruim para um integrador, que espera status HTTP com significado. Aqui o corpo
  é o recurso direto e o erro é ``{"error": {"code", "message"}}`` com o status
  certo (400/403/404/409/429).
* **Erros como exceção**, renderizados por um handler — uma dependency do FastAPI
  não consegue abortar devolvendo uma ``Response``.

O gate é o RBAC do USUÁRIO dono da chave: nada de permissão nova por rota (D5) e
nada de escopo por chave (D3). O escopo de DADOS continua vindo de
``visible_inbox_ids`` — a membresia de inbox do dono É o escopo da chave.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from server import authz

V1_PREFIX = "/api/v1"


class V1Error(Exception):
    """Erro de domínio da fachada, renderizado como ``{"error": {...}}``."""

    def __init__(self, message: str, *, status: int = 400, code: str = "bad_request",
                 details=None) -> None:
        self.message = message
        self.status = status
        self.code = code
        self.details = details
        super().__init__(message)


async def _v1_error_handler(request: Request, exc: V1Error):
    body = {"error": {"code": exc.code, "message": exc.message}}
    if exc.details is not None:
        body["error"]["details"] = exc.details
    return JSONResponse(body, status_code=exc.status)


def install_v1_handlers(app) -> None:
    """Idempotente — Starlette guarda handlers num dict por tipo de exceção."""
    app.add_exception_handler(V1Error, _v1_error_handler)


def not_found(what: str = "Recurso não encontrado.") -> V1Error:
    return V1Error(what, status=404, code="not_found")


def forbidden(message: str = "Permissão negada.") -> V1Error:
    return V1Error(message, status=403, code="forbidden")


def require(key: str) -> Callable[[Request], Awaitable[None]]:
    """Dependency de permissão da v1 — mesma decisão do painel, erro em DTO v1.

    Chama :func:`server.authz.acheck` (async ⇒ o seam ABAC ``filter.authz.decision``
    roda LIVE) e preserva o default-allow de instalação aberta.
    """
    async def _dep(request: Request) -> None:
        if not await authz.acheck(request, key):
            raise forbidden()

    return _dep


def visible_inboxes(request: Request):
    """Ids de inbox que o dono da chave enxerga, ou ``None`` para "todos"."""
    return authz.visible_inbox_ids(request)


def ensure_inbox_access(request: Request, inbox_id) -> None:
    """Levanta 403 quando o dono da chave não pode agir naquela caixa."""
    if not authz.can_access_inbox(request, inbox_id):
        raise forbidden("Sem acesso a esta caixa de entrada.")


def page_params(limit, offset, *, default: int = 50, cap: int = 200) -> tuple[int, int]:
    """Normaliza ``limit``/``offset``. Fora da faixa é CLAMPADO, nunca 500."""
    try:
        lim = default if limit is None else int(limit)
    except (TypeError, ValueError):
        lim = default
    try:
        off = 0 if offset is None else int(offset)
    except (TypeError, ValueError):
        off = 0
    return max(1, min(lim, cap)), max(0, off)


# ── Serializadores (a forma pública, estável, dos recursos) ──────────────────

def contact_dto(row: dict) -> dict:
    """Contato na forma da v1. Nunca vaza colunas internas por acidente."""
    if not row:
        return {}
    info = row.get("info") or {}
    return {
        "id": row.get("id"),
        "phone": row.get("phone"),
        "name": row.get("name") or info.get("name") or "",
        "is_group": bool(row.get("is_group")),
        "is_archived": bool(row.get("is_archived")),
        "is_pinned": bool(row.get("is_pinned")),
        "contact_type": row.get("contact_type") or "outros",
        "ai_enabled": bool(row.get("ai_enabled", True)),
        "tags": row.get("tags") or [],
        "custom_attributes": row.get("custom_attributes") or info.get("custom_attributes") or {},
        "unread_count": row.get("unread_count"),
        "last_message_ts": row.get("last_message_ts"),
        "created_at": row.get("created_at"),
    }


def conversation_dto(row: dict) -> dict:
    if not row:
        return {}
    return {
        "id": row.get("id"),
        "display_id": row.get("display_id"),
        "inbox_id": row.get("inbox_id"),
        "channel_id": row.get("channel_id"),
        "contact_id": row.get("contact_id"),
        "contact_phone": row.get("contact_phone"),
        "contact_name": row.get("contact_name"),
        "status": row.get("status"),
        "is_archived": bool(row.get("is_archived")),
        "is_pinned": bool(row.get("is_pinned")),
        "assignee_user_id": row.get("assignee_user_id"),
        "team_id": row.get("team_id"),
        "active_agent_key": row.get("active_agent_key"),
        "ai_active": bool(row.get("ai_active", 1)),
        "labels": row.get("labels") or [],
        "custom_attributes": row.get("custom_attributes") or {},
        "unread_count": row.get("unread_count"),
        "last_activity_at": row.get("last_activity_at"),
        "created_at": row.get("created_at"),
    }


def message_dto(row: dict) -> dict:
    if not row:
        return {}
    return {
        "id": row.get("id"),
        "conversation_id": row.get("conversation_id"),
        "role": row.get("role"),
        "content": row.get("content"),
        "ts": row.get("ts"),
        "status": row.get("status"),
        "msg_id": row.get("msg_id"),
        "media_type": row.get("media_type"),
        "media_path": row.get("media_path"),
        "media_caption": row.get("media_caption"),
        "reply_to_msg_id": row.get("reply_to_msg_id"),
        "reactions": row.get("reactions"),
        "revoked": bool(row.get("revoked")),
        "edited_ts": row.get("edited_ts"),
        "sent_by_name": row.get("sent_by_name"),
    }
