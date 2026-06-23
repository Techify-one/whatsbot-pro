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
import uuid

from fastapi import Request
from fastapi.responses import Response

from channels.providers.gowa_channel import build_gowa_channel
from db.repositories import (channel_repo, channel_credential_repo, inbox_repo,
                             inbox_member_repo, user_repo)
from server.authz import permission_denied
from server.helpers import _ok, _err

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ALLOWED_PROVIDERS = {"gowa", "whatsapp_cloud", "telegram", "test"}


def _mask(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return f"••••{tail}"


# Non-secret credential keys returned in CLEAR so the edit form can show + pre-fill
# them (they are public identifiers, not secrets). Everything else is masked (P15).
_NON_SECRET_CRED_KEYS = {"waba_id", "phone_number_id"}


def _serialize(row: dict, creds: dict) -> dict:
    row = dict(row)
    row["credentials"] = {
        k: (v if k in _NON_SECRET_CRED_KEYS else _mask(v))
        for k, v in creds.items()
    }
    return row


def _assignable_users() -> list[dict]:
    """Active panel users for the channel agent picker (id/name/email/is_admin)."""
    return [
        {"id": u["id"], "name": u.get("name") or u.get("email"),
         "email": u.get("email"), "is_admin": bool(u.get("is_admin"))}
        for u in user_repo.list_all() if u.get("is_active")
    ]


def register_routes(app, deps):

    registry = getattr(deps, "channel_registry", None)
    gowa_client = getattr(deps, "gowa_client", None)
    gowa_manager = getattr(deps, "gowa_manager", None)

    def _register_live_gowa(cid: str, row: dict) -> None:
        """Make a freshly-created GOWA channel operational without a restart, so
        its QR/status endpoints work immediately."""
        if registry is None or gowa_client is None:
            return
        try:
            inst = build_gowa_channel(cid, row, gowa_client=gowa_client,
                                      gowa_manager=gowa_manager)
            registry.add_channel(cid, inst)
        except Exception:
            pass

    def _register_live_provider(cid: str, provider: str) -> None:
        """Make a freshly-created non-GOWA channel (Cloud/Telegram/…) live without
        a restart, when its provider plugin is loaded.

        Mirrors the boot-time materialization in ``create_app``: instantiate the
        registered provider class with ``(channel_id, registry=...)`` so status/QR/
        send and (for Telegram) the long-poll loop pick it up immediately. Pure
        constructor — no network — so it is safe and best-effort."""
        if registry is None:
            return
        provider_cls = registry.get_provider(provider)
        if provider_cls is None:
            return
        try:
            try:
                inst = provider_cls(cid, registry=registry)
            except TypeError:
                inst = provider_cls(cid)
            registry.add_channel(cid, inst)
        except Exception:
            pass

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

    @app.get("/api/channels/connected")
    async def list_connected_channels(request: Request):
        """Connected + logged-in channels an operator can start a conversation on.

        Lighter and lower-privileged than ``GET /api/channels`` (gated by
        ``conversation.reply`` instead of ``channel.manage``, no credentials): the
        "start conversation" inbox picker needs only id/provider/name/status.
        Status prefers the live registry instance, falling back to the stored flags.
        Disconnected or disabled channels are filtered out.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        rows = await asyncio.to_thread(channel_repo.list_all)
        out = []
        for row in rows:
            if not row.get("enabled"):
                continue
            connected = bool(row.get("connected"))
            logged_in = bool(row.get("logged_in"))
            inst = registry.get(row["id"]) if registry is not None else None
            if inst is not None:
                try:
                    st = await asyncio.to_thread(inst.status)
                    connected = bool(st.get("connected"))
                    logged_in = bool(st.get("logged_in"))
                except Exception:
                    pass
            if not (connected and logged_in):
                continue
            out.append({
                "id": row["id"],
                "provider": row.get("provider"),
                "display_name": row.get("display_name") or "",
                "own_phone": row.get("own_phone"),
            })
        return _ok(out)

    @app.get("/api/channels/assignable-users")
    async def assignable_users(request: Request):
        """Active panel users for the channel agent picker (create + edit).

        Gated by ``channel.manage`` (same as the rest of this screen). Registered
        before ``/{channel_id}`` so the literal path wins the match."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        users = await asyncio.to_thread(_assignable_users)
        return _ok({"users": users})

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
        if provider not in _ALLOWED_PROVIDERS:
            return _err(f"provider deve ser um de: {', '.join(sorted(_ALLOWED_PROVIDERS))}.", 400)
        config = body.get("config")
        # The UI may nest gowa_device_id inside config; accept either spot.
        gowa_device_id = body.get("gowa_device_id")
        if not gowa_device_id and isinstance(config, dict):
            gowa_device_id = config.get("gowa_device_id")
        # GOWA device id is auto-generated, never user-chosen. Each channel maps to
        # its own GOWA device (X-Device-Id) on the shared GOWA process.
        if provider == "gowa" and not gowa_device_id:
            gowa_device_id = f"gowa_{uuid.uuid4().hex[:8]}"
        # Channel id is auto-generated: the user only picks a display name. GOWA
        # reuses its device id as the channel id; other providers get
        # "<provider>_<hex>". A client may still send an explicit id (back-compat):
        # it is validated and checked for uniqueness as before.
        if cid:
            if not _ID_RE.match(cid):
                return _err("id inválido (use snake_case: a-z, 0-9, _; começa com letra).", 400)
            if await asyncio.to_thread(channel_repo.get, cid):
                return _err("Já existe um canal com esse id.", 409)
        else:
            cid = (gowa_device_id if (provider == "gowa" and gowa_device_id)
                   else f"{provider}_{uuid.uuid4().hex[:8]}")
            while await asyncio.to_thread(channel_repo.get, cid):
                cid = f"{provider}_{uuid.uuid4().hex[:8]}"
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
        # Register the live channel now so status/QR/send work without a restart.
        # GOWA needs its per-device client; other providers are built generically
        # from the registered provider class (no-op if the plugin isn't loaded).
        if provider == "gowa":
            _register_live_gowa(cid, row)
        else:
            _register_live_provider(cid, provider)
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
        # A config edit may change the GOWA JID-type filter — drop its cache so
        # the webhook picks up the new allowed types without waiting for the TTL.
        if "config" in body:
            try:
                from server.routes.webhook import reset_allowed_jid_cache
                reset_allowed_jid_cache()
            except Exception:
                pass
        # Credentials: a non-empty value replaces; the masked placeholder (••••) is ignored.
        for key, value in (body.get("credentials") or {}).items():
            if value and not str(value).startswith("••••"):
                await asyncio.to_thread(channel_credential_repo.set, channel_id, str(key), str(value))
        stored = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
        return _ok(_serialize(row, stored))

    @app.get("/api/channels/{channel_id}/members")
    async def get_channel_members(channel_id: str, request: Request):
        """Agents (panel users) who see/receive this channel's inbox.

        Returns the channel's inbox id, the current member user ids, and the full
        list of assignable (active) users for the picker — one round-trip for the
        editor. Gated by ``channel.manage`` (same as the rest of this screen)."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        inbox = await asyncio.to_thread(
            inbox_repo.get_or_create_for_channel, channel_id,
            name=row.get("display_name") or channel_id)
        members = await asyncio.to_thread(inbox_member_repo.member_ids, inbox["id"])
        users = await asyncio.to_thread(_assignable_users)
        return _ok({"inbox_id": inbox["id"], "member_ids": members, "users": users})

    @app.put("/api/channels/{channel_id}/members")
    async def set_channel_members(channel_id: str, body: dict, request: Request):
        """Replace the member set of this channel's inbox. Body: ``{user_ids: [...]}``."""
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        raw = body.get("user_ids")
        if not isinstance(raw, list):
            return _err("user_ids deve ser uma lista.", 400)
        try:
            user_ids = [int(u) for u in raw]
        except (TypeError, ValueError):
            return _err("user_ids deve conter apenas inteiros.", 400)
        inbox = await asyncio.to_thread(
            inbox_repo.get_or_create_for_channel, channel_id,
            name=row.get("display_name") or channel_id)
        members = await asyncio.to_thread(
            inbox_member_repo.set_members, inbox["id"], user_ids)
        return _ok({"inbox_id": inbox["id"], "member_ids": members})

    @app.delete("/api/channels/{channel_id}")
    async def delete_channel(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        if channel_id == "default":
            return _err("O canal default não pode ser removido.", 400)
        # Best-effort: log the GOWA device out so the WhatsApp number is freed.
        inst = registry.get(channel_id) if registry is not None else None
        if inst is not None and getattr(inst, "_client", None) is not None:
            try:
                await asyncio.to_thread(inst._client.logout)
            except Exception:
                pass
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

    @app.get("/api/channels/{channel_id}/qr")
    async def channel_qr(channel_id: str, request: Request):
        denied = permission_denied(request, "channel.manage")
        if denied:
            return denied
        row = await asyncio.to_thread(channel_repo.get, channel_id)
        if row is None:
            return _err("Canal não encontrado.", 404)
        if row.get("provider") != "gowa":
            return _err("QR disponível apenas para canais GOWA.", 400)
        inst = registry.get(channel_id) if registry is not None else None
        # The channel may exist in the DB but not be live yet (created before this
        # boot, or registry unavailable) — build/register it on demand.
        if inst is None:
            _register_live_gowa(channel_id, row)
            inst = registry.get(channel_id) if registry is not None else None
        if inst is None or not hasattr(inst, "qr"):
            return _err("Canal GOWA indisponível.", 503)
        try:
            png = await asyncio.to_thread(inst.qr)
        except Exception as e:  # noqa: BLE001
            return _err(f"Falha ao obter QR: {e}", 502)
        if not png:
            # Already logged in, or GOWA not ready yet — 204 tells the UI to poll status.
            return Response(status_code=204)
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})
