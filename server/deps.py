"""Reusable FastAPI dependencies for the WhatsBot backend (Plano 23 · Fase B1).

This module centralizes the small set of cross-cutting dependencies that route
modules wire via ``dependencies=[Depends(...)]`` / injected params, replacing the
hand-rolled ``denied = permission_denied(...); if denied: return denied`` pattern
that was repeated across ~50 call sites.

Behavior preservation (R16): ``require_permission`` produces the **byte-identical**
403 envelope that :func:`server.authz.permission_denied` produced — ``_err(...)``'s
``{"ok": false, "error": "Permissão negada."}`` shape, NOT FastAPI's default
``{"detail": ...}``. It also preserves the legacy/open default-allow (no user
identity ⇒ allow). The one *intentional, plan-sanctioned* difference from the old
inline path: the dependency is **async** and calls :func:`server.authz.acheck`, so
the ``filter.authz.decision`` ABAC seam runs LIVE (the old inline ``check`` ran on
the event-loop thread where ``apply_filter_sync`` is inert). The RBAC 403/200
outcomes with no filter registered are unchanged.

A FastAPI dependency cannot short-circuit a request by *returning* a ``Response``
(returning only provides a value). To abort with a custom body we raise
:class:`PermissionDeniedError` / :class:`NotFoundError` and render them via an
exception handler installed by :func:`install_exception_handlers` — which each
target route module calls once from its ``register_routes`` (idempotent).

``get_*_service()`` dependencies are intentionally NOT defined yet — the service
layer (``messaging_service`` / ``conversation_service`` / …) does not exist until
Wave 2. They land here then, alongside ``dependency_overrides`` test wiring.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastapi import Depends, Request

from server import authz
from server.helpers import _err


# ── Custom exceptions rendered to the legacy envelope ───────────────────────

class PermissionDeniedError(Exception):
    """Raised by :func:`require_permission` when the current user lacks the
    permission. Rendered to the same ``_err(403)`` envelope ``permission_denied``
    returned (``{"ok": false, "error": "Permissão negada."}``)."""

    message = "Permissão negada."


class NotFoundError(Exception):
    """Raised by get-or-404 dependencies. Rendered to ``_err(404)`` with the
    caller-supplied message (defaults to the channel phrasing)."""

    def __init__(self, message: str = "Não encontrado.") -> None:
        self.message = message
        super().__init__(message)


async def _permission_denied_handler(request: Request, exc: PermissionDeniedError):
    # Byte-identical to authz.permission_denied → _err("Permissão negada.", status=403).
    return _err(PermissionDeniedError.message, status=403)


async def _not_found_handler(request: Request, exc: NotFoundError):
    return _err(exc.message, status=404)


def install_exception_handlers(app) -> None:
    """Register the handlers that render :class:`PermissionDeniedError` /
    :class:`NotFoundError` to the legacy ``_err`` envelopes.

    Idempotent: Starlette keeps handlers in a dict keyed by exception type, so
    registering the same pair from each route module's ``register_routes`` just
    overwrites with the identical callable — no duplication, no ordering effect.
    """
    app.add_exception_handler(PermissionDeniedError, _permission_denied_handler)
    app.add_exception_handler(NotFoundError, _not_found_handler)


# ── require_permission ──────────────────────────────────────────────────────

def require_permission(key: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency factory gating a route on permission ``key``.

    Usage::

        @app.put("/api/...", dependencies=[Depends(require_permission("agent.manage"))])
        async def handler(...): ...

    Resolves ``current_user`` from the request and calls :func:`authz.acheck`
    (async ⇒ the ``filter.authz.decision`` seam is live). On deny raises
    :class:`PermissionDeniedError`, which :func:`install_exception_handlers`
    renders to the identical 403 envelope. Preserves default-allow: with no user
    identity (legacy/open install) ``acheck`` returns True and the gate passes.
    """

    async def _dep(request: Request) -> None:
        allowed = await authz.acheck(request, key)
        if not allowed:
            raise PermissionDeniedError()

    return _dep


# ── get_channel_or_404 ──────────────────────────────────────────────────────

async def get_channel_or_404(channel_id: str) -> dict:
    """Load a channel by id or raise :class:`NotFoundError` (rendered 404).

    Mirrors the inline ``row = channel_repo.get(id); if row is None: return
    _err("Canal não encontrado.", 404)`` pattern used across ``channels.py``.
    Returns the channel row dict on success. Used as an injected dependency::

        async def handler(channel: dict = Depends(get_channel_or_404)): ...
    """
    from db.repositories import channel_repo

    row = await asyncio.to_thread(channel_repo.get, channel_id)
    if row is None:
        raise NotFoundError("Canal não encontrado.")
    return row
