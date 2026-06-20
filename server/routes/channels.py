"""Channels read API (plano 02 Fase 0).

Read-only listing of configured channels for the panel. Credentials are masked
at this boundary (P15 — secrets never returned in clear), mirroring how
``/api/config`` masks the LLM key. Channel CRUD + per-provider config land with
the later phases (multi-number, Cloud API).
"""

import asyncio

from db.repositories import channel_repo, channel_credential_repo
from server.helpers import _ok, _err


def _mask(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return f"••••{tail}"


def register_routes(app, deps):

    @app.get("/api/channels")
    async def list_channels():
        rows = await asyncio.to_thread(channel_repo.list_all)
        out = []
        for row in rows:
            creds = await asyncio.to_thread(channel_credential_repo.get_all, row["id"])
            row = dict(row)
            row["credentials"] = {k: _mask(v) for k, v in creds.items()}
            out.append(row)
        return _ok(out)

    @app.get("/api/channels/{channel_id}")
    async def get_channel(channel_id: str):
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        creds = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
        row = dict(row)
        row["credentials"] = {k: _mask(v) for k, v in creds.items()}
        return _ok(row)
