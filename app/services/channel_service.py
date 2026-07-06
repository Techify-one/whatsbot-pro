"""Channel CRUD + lifecycle service (Plano 23 · Fase B6).

Owns the channel management logic that previously lived inline in
``server/routes/channels.py``: create / update / delete (soft + purge) / restore /
list / get, the per-channel members editor, status reads, the live-instance
registration on create/QR, the capabilities probe (required credentials), and —
critically — the per-channel cache invalidation on a config edit.

Branch by Abstraction: the routes resolve permission + body validation + 404,
then delegate the WRITE + side effects here. Behavior is preserved byte-for-byte
(the 876-check legacy suite is the contract).

Q6 — minimal channel domain event (anticipating C3): a config edit emits the
MINIMAL ``channel.updated`` domain event and a status read that prefers the live
instance emits ``channel.status_changed`` when the live status is read. Cache
invalidation is DRIVEN OFF the update path via :func:`_invalidate_channel_caches`
(the JID-type cache used by the GOWA webhook + the per-channel AI-settings cache),
instead of a bare direct call scattered in the route. Full channel-event
normalization (DTOs, every verb, audit) is C3's job — this is the minimal seam.

The service never imports ``server.app``: it receives ``deps`` (registry,
gowa_client/manager, ws_manager, outbound_router, agent_handler) and reaches the
plugin bus / caches via lazy imports to avoid an import cycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid

from sqlalchemy.exc import IntegrityError

from channels import dedup
from db.repositories import (channel_repo, channel_credential_repo, inbox_repo,
                             inbox_member_repo, user_repo)
from plugins.events import emit_with_filter

logger = logging.getLogger(__name__)


# ── Account-identity dedup enforcement (plano 32 F4) ─────────────────────────────

class DuplicateChannelError(Exception):
    """Raised by create/update when the submitted credentials resolve to an account
    already bound to another enabled channel of the same provider. The route maps it
    to HTTP 409 (D1: block, don't just warn)."""

    def __init__(self, existing_channel_id: str):
        self.existing_channel_id = existing_channel_id
        super().__init__(
            f"Esta conta já está conectada no canal {existing_channel_id}.")


def credential_identity(deps, provider: str, creds: dict):
    """The account identity a provider derives from credentials, or ``None``.

    Generic: reads the provider CLASS's ``identity_from_credentials`` hook (a
    classmethod) via the registry — never branches on provider name. ``None`` when
    the provider has no create-time identity (GOWA) or the plugin isn't loaded."""
    registry = getattr(deps, "channel_registry", None)
    if registry is None:
        return None
    cls = registry.get_provider(provider)
    if cls is None:
        return None
    try:
        return cls.identity_from_credentials(dict(creds or {}))
    except Exception:  # noqa: BLE001
        return None


def _guard_duplicate(deps, provider: str, creds: dict, *, exclude_channel_id):
    """Resolve the credential identity and raise if it duplicates another channel.

    Returns the identity to persist (or ``None`` when the provider has none at this
    point, e.g. GOWA — which dedups later via the sweep). Raises
    :class:`DuplicateChannelError` on a conflict."""
    identity = credential_identity(deps, provider, creds)
    if identity is None:
        return None
    conflict = dedup.find_conflict(provider, identity,
                                   exclude_channel_id=exclude_channel_id)
    if conflict:
        raise DuplicateChannelError(conflict)
    return identity


def _persist_identity(provider: str, channel_id: str, identity) -> None:
    """Write the resolved identity, mapping the index backstop to a 409."""
    if identity is None:
        return
    try:
        channel_repo.set_status(channel_id, account_identity=identity.value,
                                account_identity_kind=identity.kind)
    except IntegrityError:
        conflict = dedup.find_conflict(provider, identity,
                                       exclude_channel_id=channel_id) or channel_id
        raise DuplicateChannelError(conflict)

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ALLOWED_PROVIDERS = {"gowa", "whatsapp_cloud", "telegram", "test"}

# Non-secret credential keys returned in CLEAR so the edit form can show + pre-fill
# them (they are public identifiers, not secrets). Everything else is masked (P15).
NON_SECRET_CRED_KEYS = {"waba_id", "phone_number_id"}

TEMPLATE_CATEGORIES = {"UTILITY", "MARKETING", "AUTHENTICATION"}


# ── Serialization / masking (P15) ───────────────────────────────────────────────

def _mask(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return f"••••{tail}"


def serialize(row: dict, creds: dict) -> dict:
    """Mask a channel row's credentials at the API boundary (P15)."""
    row = dict(row)
    row["credentials"] = {
        k: (v if k in NON_SECRET_CRED_KEYS else _mask(v))
        for k, v in creds.items()
    }
    return row


def assignable_users() -> list[dict]:
    """Active panel users for the channel agent picker (id/name/email/is_admin)."""
    return [
        {"id": u["id"], "name": u.get("name") or u.get("email"),
         "email": u.get("email"), "is_admin": bool(u.get("is_admin"))}
        for u in user_repo.list_all() if u.get("is_active")
    ]


# ── Provider instantiation / live registration (R15 via registry.instantiate) ───

def required_credentials(deps, provider: str) -> list[str]:
    """Credential keys a provider MUST have to ever become operational.

    Probes a throwaway instance (pure constructor) to read
    ``ChannelCapabilities.required_credentials``. Best-effort → ``[]`` on any
    failure, so a probe issue never spuriously blocks creation. Never branches on
    provider name (the knowledge lives in each provider).
    """
    registry = getattr(deps, "channel_registry", None)
    if registry is None:
        return []
    if registry.get_provider(provider) is None:
        return []
    try:
        inst = registry.instantiate(provider, "__probe__")
        if inst is None:
            return []
        caps = getattr(inst, "capabilities", None)
        return list(getattr(caps, "required_credentials", ()) or ())
    except Exception:
        return []


def register_live(deps, cid: str, provider: str, row: dict | None = None) -> None:
    """Make a freshly-created channel operational without a restart.

    GOWA gets its per-device client (needs gowa_client/manager); other providers
    are built generically from the registered provider class. Pure constructor —
    no network — so it is safe and best-effort (no-op if the plugin isn't loaded).
    """
    registry = getattr(deps, "channel_registry", None)
    if registry is None:
        return
    try:
        inst = registry.instantiate(
            provider, cid, row=row,
            gowa_client=getattr(deps, "gowa_client", None),
            gowa_manager=getattr(deps, "gowa_manager", None))
        if inst is not None:
            registry.add_channel(cid, inst)
    except Exception:
        pass


# ── Q6: minimal channel domain event + cache invalidation ───────────────────────

def _invalidate_channel_caches(channel_id: str) -> None:
    """Drop the per-channel caches a config edit may have invalidated.

    The GOWA JID-type filter cache (read by the webhook) and the per-channel AI
    settings cache (plano 21) are both TTL-based; dropping them here lets the new
    config take effect immediately instead of waiting out the TTL. Defensive — a
    cache miss/reset never fails the action. Driven off the ``channel.updated``
    path (Q6) so the invalidation policy lives in one place.
    """
    try:
        from app.services.message_ingest_service import reset_allowed_jid_cache
        reset_allowed_jid_cache()
    except Exception:
        pass
    try:
        from channels import ai_settings
        ai_settings.reset_cache(channel_id)
    except Exception:
        pass


async def _emit_channel_event(deps, bus_event: str, payload: dict) -> None:
    """Emit a minimal channel domain event on the plugin bus, defensively."""
    try:
        await emit_with_filter(bus_event, payload)
    except Exception as e:
        logger.debug("channel event %s failed: %s", bus_event, e)


# ── Read paths ──────────────────────────────────────────────────────────────────

async def list_channels(deps, archived: bool = False) -> list[dict]:
    rows = await asyncio.to_thread(
        channel_repo.list_archived if archived else channel_repo.list_all)
    out = []
    for row in rows:
        creds = await asyncio.to_thread(channel_credential_repo.get_all, row["id"])
        out.append(serialize(row, creds))
    return out


async def list_connected(deps) -> list[dict]:
    """Connected + logged-in channels an operator can start a conversation on.

    Inclusion is by ``logged_in`` (the session can send), NOT ``connected`` — for
    GOWA the two differ (subprocess ownership vs. live WhatsApp session). Status
    prefers the live registry instance, falling back to the stored flags.
    """
    registry = getattr(deps, "channel_registry", None)
    rows = await asyncio.to_thread(channel_repo.list_all)
    out = []
    for row in rows:
        if not row.get("enabled"):
            continue
        logged_in = bool(row.get("logged_in"))
        inst = registry.get(row["id"]) if registry is not None else None
        if inst is not None:
            try:
                st = await asyncio.to_thread(inst.status)
                logged_in = bool(st.get("logged_in"))
            except Exception:
                pass
        if not logged_in:
            continue
        out.append({
            "id": row["id"],
            "provider": row.get("provider"),
            "display_name": row.get("display_name") or "",
            "own_phone": row.get("own_phone"),
        })
    return out


async def get(deps, channel_id: str) -> dict | None:
    """Fetch a channel row (raw, unmasked) — None if it doesn't exist."""
    return await asyncio.to_thread(channel_repo.get, channel_id)


async def get_serialized(deps, channel_id: str) -> dict | None:
    """Fetch a channel with masked credentials, or None."""
    row = await asyncio.to_thread(channel_repo.get, channel_id)
    if row is None:
        return None
    creds = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
    return serialize(row, creds)


def provider_descriptor(deps, provider: str) -> dict:
    """The provider's self-description (plano 33), reconciled with capabilities.

    Reads the provider CLASS's ``provider_descriptor`` (a classmethod) via the
    registry — never branches on provider name — then guarantees every credential
    key the provider marks REQUIRED via ``ChannelCapabilities.required_credentials``
    is present + flagged ``required`` in the returned ``credential_fields`` (so a
    provider that only declared its required set, or nothing, still yields a usable
    generic form). Best-effort: a broken/absent provider degrades to a minimal
    descriptor rather than failing the endpoint."""
    registry = getattr(deps, "channel_registry", None)
    cls = registry.get_provider(provider) if registry is not None else None
    desc: dict = {}
    if cls is not None:
        try:
            desc = dict(cls.provider_descriptor())
        except Exception:  # noqa: BLE001
            desc = {}
    if not desc:
        desc = {"provider": provider, "label": provider, "color": "gray",
                "credential_fields": [], "config_fields": [],
                "capabilities": {"needs_qr": False, "templates": False},
                "ai_sequential_default": False, "post_create": None,
                "form_component": None}
    desc.setdefault("provider", provider)
    desc.setdefault("label", provider)
    desc.setdefault("color", "gray")
    desc.setdefault("config_fields", [])
    desc.setdefault("capabilities", {"needs_qr": False, "templates": False})
    desc.setdefault("ai_sequential_default", False)
    desc.setdefault("post_create", None)
    desc.setdefault("form_component", None)
    fields = list(desc.get("credential_fields") or [])
    # Reconcile: capability-required credentials must appear + be flagged required.
    have = {f.get("key") for f in fields}
    for key in required_credentials(deps, provider):
        matched = next((f for f in fields if f.get("key") == key), None)
        if matched is not None:
            matched["required"] = True
        else:
            fields.append({"key": key, "label": key, "type": "secret",
                           "required": True})
    desc["credential_fields"] = fields
    return desc


async def providers(deps) -> dict:
    """Available providers as full descriptors (plano 33) + a flat required-
    credentials map (kept for the create-form gate + back-compat).

    Offer = every provider currently REGISTERED in the live registry (its backing
    plugin is enabled). ``ALLOWED_PROVIDERS`` is no longer the source of the offer
    (a provider is offerable iff installed); it survives only as a legacy constant.
    A provider that isn't installed simply doesn't appear."""
    registry = getattr(deps, "channel_registry", None)
    names = sorted(registry.providers()) if registry is not None else []
    descriptors = [provider_descriptor(deps, p) for p in names]
    required = {
        d["provider"]: [f["key"] for f in d["credential_fields"] if f.get("required")]
        for d in descriptors
    }
    return {"providers": descriptors, "required_credentials": required}


async def status(deps, row: dict) -> dict:
    """Status for ``row``: prefer the live instance, fall back to stored flags.

    Q6: a live status read emits the minimal ``channel.status_changed`` event so
    C3 (channel-event normalization) has the seam — best-effort, never fails."""
    registry = getattr(deps, "channel_registry", None)
    channel_id = row["id"]
    inst = registry.get(channel_id) if registry is not None else None
    if inst is not None:
        try:
            st = await asyncio.to_thread(inst.status)
            await _emit_channel_event(deps, "channel.status_changed", {
                "channel_id": channel_id,
                "status": ("connected" if st.get("connected") else "disconnected"),
                "is_connected": bool(st.get("connected")),
                "is_logged_in": bool(st.get("logged_in")),
                "ts": time.time(),
            })
            return st
        except Exception as e:
            return {"connected": False, "logged_in": False,
                    "needs_qr": False, "error": str(e)}
    return {
        "connected": bool(row.get("connected")),
        "logged_in": bool(row.get("logged_in")),
        "needs_qr": False,
        "error": row.get("last_error"),
    }


async def qr(deps, row: dict):
    """Fetch the GOWA QR PNG for ``row``.

    Returns the PNG bytes, or one of the sentinels: ``"not_gowa"`` (provider isn't
    GOWA), ``"unavailable"`` (no live instance / no qr method), ``""`` (already
    logged in / GOWA not ready → 204), or raises is caught and returned as a
    ``("error", msg)`` tuple."""
    if row.get("provider") != "gowa":
        return "not_gowa"
    registry = getattr(deps, "channel_registry", None)
    channel_id = row["id"]
    inst = registry.get(channel_id) if registry is not None else None
    # The channel may exist in the DB but not be live yet — build/register on demand.
    if inst is None:
        register_live(deps, channel_id, "gowa", row)
        inst = registry.get(channel_id) if registry is not None else None
    # Canonical method is ``get_qr`` (Channel.get_qr); fall back to the deprecated
    # ``qr`` alias for a provider not yet migrated (plano 23 Fase C4 Expand-Contract).
    qr_method = getattr(inst, "get_qr", None) or getattr(inst, "qr", None)
    if inst is None or qr_method is None:
        return "unavailable"
    try:
        png = await asyncio.to_thread(qr_method)
    except Exception as e:  # noqa: BLE001
        return ("error", str(e))
    return png or ""


async def _session_action(deps, row: dict, action: str):
    """Shared body for per-channel ``reconnect``/``logout`` (plano 27 F3.1).

    Mirrors :func:`qr`: gate to GOWA, register the live instance on-demand, then
    call ``inst.<action>`` off-thread. Returns the ``{ok, error}`` dict, or a
    sentinel: ``"not_gowa"`` (provider isn't GOWA) / ``"unavailable"`` (no live
    instance / method)."""
    if row.get("provider") != "gowa":
        return "not_gowa"
    registry = getattr(deps, "channel_registry", None)
    channel_id = row["id"]
    inst = registry.get(channel_id) if registry is not None else None
    if inst is None:
        register_live(deps, channel_id, "gowa", row)
        inst = registry.get(channel_id) if registry is not None else None
    method = getattr(inst, action, None)
    if inst is None or method is None:
        return "unavailable"
    return await asyncio.to_thread(method)


async def reconnect(deps, row: dict):
    """Reconnect a GOWA channel's device socket (acts on the right device)."""
    return await _session_action(deps, row, "reconnect")


async def logout(deps, row: dict):
    """Log a GOWA channel's device out of WhatsApp (acts on the right device)."""
    return await _session_action(deps, row, "logout")


# ── Members ─────────────────────────────────────────────────────────────────────

async def get_members(deps, row: dict) -> dict:
    channel_id = row["id"]
    inbox = await asyncio.to_thread(
        inbox_repo.get_or_create_for_channel, channel_id,
        name=row.get("display_name") or channel_id)
    members = await asyncio.to_thread(inbox_member_repo.member_ids, inbox["id"])
    users = await asyncio.to_thread(assignable_users)
    return {"inbox_id": inbox["id"], "member_ids": members, "users": users}


async def set_members(deps, row: dict, user_ids: list[int]) -> dict:
    channel_id = row["id"]
    inbox = await asyncio.to_thread(
        inbox_repo.get_or_create_for_channel, channel_id,
        name=row.get("display_name") or channel_id)
    members = await asyncio.to_thread(
        inbox_member_repo.set_members, inbox["id"], user_ids)
    return {"inbox_id": inbox["id"], "member_ids": members}


# ── Create / update / delete / restore ──────────────────────────────────────────

async def create(deps, *, cid: str, provider: str, display_name: str,
                 submitted_creds: dict, config, gowa_device_id: str | None) -> dict:
    """Create a channel + credentials + its inbox, register it live, and return the
    serialized row. The route does the validation (provider allow-list, missing
    creds, id format/uniqueness) and resolves the final ``cid``/``gowa_device_id``
    before calling here so the persistence + side-effects live in one place.

    Account-identity dedup (plano 32 F4): if the provider derives an identity from
    the submitted credentials (Cloud ``phone_number_id``, Telegram ``bot_token``)
    and it duplicates another channel, raise ``DuplicateChannelError`` (→ 409)
    BEFORE persisting anything. GOWA has no create-time identity → dedups later via
    the sweep."""
    identity = _guard_duplicate(deps, provider, submitted_creds, exclude_channel_id=None)
    row = await asyncio.to_thread(
        channel_repo.create, id=cid, provider=provider,
        display_name=display_name or cid,
        gowa_device_id=gowa_device_id,
        config=json.dumps(config) if isinstance(config, (dict, list)) else config,
    )
    for key, value in submitted_creds.items():
        if value:  # never store an empty/placeholder secret
            await asyncio.to_thread(channel_credential_repo.set, cid, str(key), str(value))
    if identity is not None:
        await asyncio.to_thread(_persist_identity, provider, cid, identity)
        row = await asyncio.to_thread(channel_repo.get, cid)
    # One inbox per channel (plano 11) — best-effort; resolve_inbox_id self-heals.
    try:
        await asyncio.to_thread(
            inbox_repo.get_or_create_for_channel, cid,
            name=row.get("display_name") or cid)
    except Exception:
        logger.warning("Falha ao criar inbox do canal %s", cid, exc_info=True)
    # Register the live channel now so status/QR/send work without a restart.
    register_live(deps, cid, provider, row)
    stored = await asyncio.to_thread(channel_credential_repo.get_all, cid)
    return serialize(row, stored)


async def update(deps, row: dict, body: dict) -> dict:
    """Apply a channel update (display_name / enabled / config / credentials).

    Keeps the inbox name in sync with ``display_name``, and on a config edit drops
    the per-channel caches and emits the minimal ``channel.updated`` event (Q6).
    """
    channel_id = row["id"]
    provider = row.get("provider")
    # Account-identity dedup on EDIT (plano 32 F4): a credential edit could make this
    # channel collide with another (bypass of the create-guard). Resolve the EFFECTIVE
    # credentials (stored overlaid with the non-placeholder submitted values) and
    # block before persisting anything. GOWA has no create-time identity → None here.
    submitted_creds = body.get("credentials") or {}
    if submitted_creds:
        effective = dict(await asyncio.to_thread(
            channel_credential_repo.get_all, channel_id))
        for k, v in submitted_creds.items():
            if v and not str(v).startswith("••••"):
                effective[str(k)] = str(v)
        _guard_duplicate(deps, provider, effective, exclude_channel_id=channel_id)
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
        # Keep the channel's inbox name in sync (display_name is source of truth).
        if "display_name" in fields:
            inbox = await asyncio.to_thread(inbox_repo.get_by_channel, channel_id)
            if inbox:
                await asyncio.to_thread(
                    inbox_repo.update, inbox["id"],
                    name=fields["display_name"] or channel_id)
    # A config edit may change the GOWA JID-type filter / per-channel AI settings —
    # invalidate the caches and emit the minimal channel.updated domain event (Q6).
    if "config" in body:
        _invalidate_channel_caches(channel_id)
        await _emit_channel_event(deps, "channel.updated", {
            "channel_id": channel_id,
            "keys_changed": ["config"],
            "ts": time.time(),
        })
    # Credentials: a non-empty value replaces; the masked placeholder (••••) is ignored.
    for key, value in (body.get("credentials") or {}).items():
        if value and not str(value).startswith("••••"):
            await asyncio.to_thread(channel_credential_repo.set, channel_id, str(key), str(value))
    # Persist the (already dedup-guarded) credential identity so the row + index
    # reflect the edit (plano 32 F4).
    if submitted_creds:
        identity = credential_identity(deps, provider, effective)
        if identity is not None:
            await asyncio.to_thread(_persist_identity, provider, channel_id, identity)
            row = await asyncio.to_thread(channel_repo.get, channel_id)
    stored = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
    return serialize(row, stored)


async def delete(deps, channel_id: str, *, purge: bool = False) -> dict | None:
    """Remove a channel: soft-delete (archive, default) or hard-delete (purge).

    Returns the result dict, or ``None`` if the channel vanished mid-purge (the
    route maps it to a 404). The caller already guarded ``default`` and existence.
    """
    registry = getattr(deps, "channel_registry", None)
    # Best-effort: log the GOWA device out so the WhatsApp number is freed.
    inst = registry.get(channel_id) if registry is not None else None
    if inst is not None and getattr(inst, "_client", None) is not None:
        try:
            await asyncio.to_thread(inst._client.logout)
        except Exception:
            pass

    if purge:
        inbox = await asyncio.to_thread(inbox_repo.get_by_channel, channel_id)
        if inbox:
            await asyncio.to_thread(inbox_repo.delete, inbox["id"])
        await asyncio.to_thread(channel_credential_repo.delete_all, channel_id)
        ok = await asyncio.to_thread(channel_repo.delete, channel_id)
        if not ok:
            return None
        if registry is not None:
            try:
                registry.remove_channel(channel_id)
            except Exception:
                pass
        logger.info("Canal %s removido (purge).", channel_id)
        return {"deleted": channel_id, "purged": True}

    await asyncio.to_thread(channel_repo.archive, channel_id)
    if registry is not None:
        try:
            registry.remove_channel(channel_id)
        except Exception:
            pass
    logger.info("Canal %s arquivado (soft-delete).", channel_id)
    return {"deleted": channel_id, "archived": True}


async def restore(deps, channel_id: str) -> dict:
    """Undo a soft-delete: unarchive (stays disabled until re-enabled)."""
    row = await asyncio.to_thread(channel_repo.restore, channel_id)
    creds = await asyncio.to_thread(channel_credential_repo.get_all, channel_id)
    return serialize(row, creds)
