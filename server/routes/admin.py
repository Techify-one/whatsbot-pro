"""Admin endpoints — info do banco + manutenção Postgres.

Plano 29 · Eixo C (Postgres-only): o fluxo de migração SQLite → Postgres
(endpoints ``/api/admin/migrate-to-postgres`` + tela Settings → Banco) foi
removido junto com o caminho SQLite. Restam a inspeção do backend atual e o
reparo de sequências (útil após import manual de dados).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import Depends

from db import engine as engine_module
from db.pg_maintenance import repair_postgres_sequences
from server.deps import require_permission, install_exception_handlers
from server.helpers import _err, _ok

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    # B1: render require_permission's PermissionDeniedError to the legacy
    # _err(403) envelope (idempotent across route modules).
    install_exception_handlers(app)

    @app.get("/api/admin/database",
             dependencies=[Depends(require_permission("database.manage"))])
    async def database_info():
        """Return the current DB URL (redacted) plus dialect."""
        url = engine_module.get_database_url()
        return _ok({
            "dialect": engine_module.get_engine().dialect.name,
            "url_redacted": _redact(url),
        })

    @app.post("/api/admin/repair-sequences",
              dependencies=[Depends(require_permission("database.manage"))])
    async def repair_sequences():
        """Re-anchor every Postgres sequence to MAX(<pk>).

        Useful when data was imported via tools that don't bump sequences —
        exposed as a manual recovery button.
        """
        if not engine_module.is_postgres():
            return _err("Banco atual não é Postgres — sequências são exclusivas dele.", status=400)
        fixed = await asyncio.to_thread(repair_postgres_sequences, engine_module.get_engine())
        return _ok({"repaired": fixed, "count": len(fixed)})


def _redact(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            _, host_part = rest.split("@", 1)
            return f"{scheme}://***@{host_part}"
    return url
