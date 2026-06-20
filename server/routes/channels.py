"""Channels API (plano 02 Fase 0 + Fase 2).

CRUD of configured channels for the panel. Credentials are masked at this
boundary (P15 — secrets never returned in clear), mirroring how ``/api/config``
masks the LLM key. Secrets are stored in clear (MVP, P15) via
``channel_credential_repo``; only the read path masks.

Live registration: creating/deleting a channel persists to the DB; the live
``ChannelRegistry`` is (re)built from the DB at boot, so a new channel becomes
operational on the next restart (same model as plugins). Status reads prefer the
live instance when present, else the stored flags.
"""

import asyncio
import json
import re

from fastapi import Request

from db.repositories import channel_repo, channel_credential_repo, inbox_repo
from server.authz import permission_denied
from server.helpers import _ok, _err

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ALLOWED_PROVIDERS = {"gowa", "whatsapp_cloud", "telegram", "test"}


def _mask(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return f"••••{tail}"


def _serialize(row: dict, creds: dict) -> dict:
    row = dict(row)
    row["credentials"] = {k: _mask(v) for k, v in creds.items()}
    return row


def register_routes(app, deps):

    registry = getattr(deps, "channel_registry", None)

    @app.get("/api/channels")
    async def list_channels(request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        rows = await asyncio.to_thread(channel_repo.list_all)
        out = []
        for row in rows:
            creds = await asyncio.to_thread(channel_credential_repo.get_all, row["id"])
            out.append(_serialize(row, creds))
        return _ok(out)

    @app.get("/api/channels/{channel_id}")
    async def get_channel(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        creds = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
        return _ok(_serialize(row, creds))

    @app.post("/api/channels")
    async def create_channel(body: dict, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        cid = (body.get("id") or "").strip()
        provider = (body.get("provider") or "").strip()
        if not _ID_RE.match(cid):
            return _err("id inválido (use snake_case: a-z, 0-9, _; começa com letra).", 400)
        if provider not in _ALLOWED_PROVIDERS:
            return _err(f"provider deve ser um de: {', '.join(sorted(_ALLOWED_PROVIDERS))}.", 400)
        if await asyncio.to_thread(channel_repo.get, cid):
            return _err("Já existe um canal com esse id.", 409)
        config = body.get("config")
        # The UI may nest gowa_device_id inside config; accept either spot.
        gowa_device_id = body.get("gowa_device_id")
        if not gowa_device_id and isinstance(config, dict):
            gowa_device_id = config.get("gowa_device_id")
        row = await asyncio.to_thread(
            channel_repo.create, id=cid, provider=provider,
            display_name=body.get("display_name", "") or cid,
            gowa_device_id=gowa_device_id,
            config=json.dumps(config) if isinstance(config, (dict, list)) else config,
        )
        creds = body.get("credentials") or {}
        for key, value in creds.items():
            if value:  # never store an empty/placeholder secret
                await asyncio.to_thread(channel_credential_repo.set, cid, str(key), str(value))
        # One inbox per channel (plano 11): conversations on this channel get their
        # own thread. Best-effort — a failure here never blocks channel creation
        # (resolve_inbox_id self-heals on first inbound).
        try:
            await asyncio.to_thread(
                inbox_repo.get_or_create_for_channel, cid,
                name=row.get("display_name") or cid)
        except Exception:
            pass
        stored = await asyncio.to_thread(channel_credential_repo.get_all, cid)
        return _ok(_serialize(row, stored))

    @app.put("/api/channels/{channel_id}")
    async def update_channel(channel_id: str, body: dict, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        fields = {}
        if "display_name" in body:
            fields["display_name"] = body["display_name"]
        if "enabled" in body:
            fields["enabled"] = 1 if body["enabled"] else 0
        if "config" in body:
            cfg = body["config"]
            fields["config"] = json.dumps(cfg) if isinstance(cfg, (dict, list)) else cfg
        if fields:
            row = await asyncio.to_thread(channel_repo.update, channel_id, **fields)
        # Credentials: a non-empty value replaces; the masked placeholder (••••) is ignored.
        for key, value in (body.get("credentials") or {}).items():
            if value and not str(value).startswith("••••"):
                await asyncio.to_thread(channel_credential_repo.set, channel_id, str(key), str(value))
        stored = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
        return _ok(_serialize(row, stored))

    @app.delete("/api/channels/{channel_id}")
    async def delete_channel(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        if channel_id == "default":
            return _err("O canal default não pode ser removido.", 400)
        await asyncio.to_thread(channel_credential_repo.delete_all, channel_id)
        ok = await asyncio.to_thread(channel_repo.delete, channel_id)
        if not ok:
            return _err("Canal não encontrado.", 404)
        if registry is not None:
            try:
                registry.remove_channel(channel_id)
            except Exception:
                pass
        return _ok({"deleted": channel_id})

    @app.get("/api/channels/{channel_id}/status")
    async def channel_status(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        # Prefer the live instance's status; fall back to the stored flags.
        inst = registry.get(channel_id) if registry is not None else None
        if inst is not None:
            try:
                st = await asyncio.to_thread(inst.status)
                return _ok(st)
            except Exception as e:
                return _ok({"connected": False, "logged_in": False,
                            "needs_qr": False, "error": str(e)})
        return _ok({
            "connected": bool(row.get("connected")),
            "logged_in": bool(row.get("logged_in")),
            "needs_qr": False,
            "error": row.get("last_error"),
        })
