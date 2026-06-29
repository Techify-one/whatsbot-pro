"""Runtime observability endpoints (plano 09 Fase 5).

Read-only views of the background task supervisor and the managed subprocess
service. Health lives only in memory (P30) — no tables, no migration; the
endpoints read straight from the live objects.

Admin-only conceptually; in the MVP they sit behind the current session auth
(``server/app.py`` auth middleware, with ``/api/webhook`` + ``/health`` exempt).
They gain real RBAC when plano 03 lands ``request.state.user``.
"""

import asyncio

from fastapi import Request

from plugins import context as _ctx
from server.authz import permission_denied
from server.helpers import _ok


def register_routes(app, deps):

    @app.get("/api/runtime/tasks")
    async def runtime_tasks(request: Request):
        denied = permission_denied(request, "settings.manage")
        if denied:
            return denied
        sup = getattr(_ctx, "_supervisor", None)
        status = await asyncio.to_thread(sup.status) if sup is not None else []
        return _ok(status)

    @app.get("/api/runtime/subprocesses")
    async def runtime_subprocesses(request: Request):
        denied = permission_denied(request, "settings.manage")
        if denied:
            return denied
        svc = getattr(_ctx, "_subprocess_service", None)
        status = await asyncio.to_thread(svc.status) if svc is not None else []
        return _ok(status)
