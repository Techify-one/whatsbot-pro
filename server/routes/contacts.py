"""Contact CRUD and messaging endpoints."""

import asyncio
import csv
import io
import logging
import time
from pathlib import Path

from fastapi import File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse
from gowa.client import GOWASendError

from db.repositories import contact_repo, message_repo, config_repo, conversation_repo, tag_repo
from db.repositories import custom_attribute_repo as ca_repo
from db.repositories import inbox_repo
from db.repositories.custom_attribute_validate import validate_value
from db.tables import contacts as contacts_table
from agent import group_mentions
from server import system_notices
from server.authz import (current_user, permission_denied, can_access_inbox,
                          visible_inbox_ids)
from server.avatars import avatar_version, refresh_and_broadcast
from server.helpers import _ok, _err, parse_split_reply
from server.transcription import maybe_transcribe
from plugins.events import emit as emit_event, apply_filter, emit_with_filter
from server.routes.sandbox import SANDBOX_CONTACT_PREFIX

logger = logging.getLogger(__name__)


async def _emit_send_error(ws_manager, phone: str, content: str) -> None:
    """Broadcast a ``role:'error'`` message card for a failed send (R3).

    Single place that builds the error-bubble WS payload the panel renders as a
    centered error card. Shape is intentionally fixed (``phone`` + a ``message``
    with ``role``/``content``/``ts``) — every send route delegates here instead
    of inlining the same ``ws_manager.broadcast`` ceremony.
    """
    await ws_manager.broadcast("new_message", {
        "phone": phone,
        "message": {"role": "error", "content": content, "ts": time.time()},
    })


def _is_sandbox_contact(phone: str) -> bool:
    """True when the contact is a sandbox/test number — operator sends to it
    must stay local (a real GOWA send would fail: the number isn't on WhatsApp)."""
    return bool(config_repo.get(f"{SANDBOX_CONTACT_PREFIX}{phone}"))


def _format_attr_cell(value) -> str:
    """Render a custom-attribute value as a flat CSV cell.

    Handles the JSON types stored in ``contacts.custom_attributes``: booleans
    (checkbox) → ``1``/``0``, lists → comma-joined, ``None``/missing → empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _normalize_import_phone(raw: str) -> str | None:
    """Strip a CSV phone cell to WhatsApp digits (ensures BR country code).

    Returns ``None`` when too short to be a real number. Mirrors the
    normalization used by ``/api/contacts/check-phone`` so imported numbers match
    contacts created through the UI."""
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if len(digits) < 10:
        return None
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


# Aliases aceitos para cada coluna do CSV de importação (case-insensitive),
# tolerando arquivos exportados do Chatwoot e planilhas em PT-BR.
_IMPORT_COLUMN_ALIASES = {
    "phone": ("phone", "telefone", "phone_number", "numero", "número", "whatsapp"),
    "name": ("name", "nome", "full_name"),
    "email": ("email", "e-mail", "e_mail"),
    "profession": ("profession", "profissao", "profissão", "cargo"),
    "company": ("company", "empresa", "company_name"),
    "address": ("address", "endereco", "endereço"),
    "ai_enabled": ("ai_enabled", "ia", "ia_ativa"),
    "tags": ("tags", "etiquetas", "labels"),
}

_IMPORT_TAG_COLOR = "#64748b"  # cor default para tags criadas na importação


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings
    statics_outbox_dir = deps.statics_outbox_dir
    outbound = deps.outbound_router

    def _channel_for(phone: str, conversation_id=None, channel_id=None) -> str:
        """Channel a conversation belongs to (plano 11 D1). The conversa-cêntrica UI
        passes ``conversation_id``; an explicit ``channel_id`` is used when starting a
        BRAND-NEW conversation (no conversation row yet — the inbox picker chooses it).
        Legacy callers (neither) fall back to 'default' (GOWA), preserving the previous
        behavior exactly. Routing is by CHANNEL, never name."""
        if conversation_id:
            from db.repositories import conversation_repo as _cr
            try:
                conv = _cr.get_with_channel(int(conversation_id))
            except (TypeError, ValueError):
                conv = None
            if conv and conv.get("channel_id"):
                return conv["channel_id"]
        if channel_id:
            return str(channel_id)
        return "default"

    def _resolve_inbox_id(conversation_id=None, channel_id=None) -> int | None:
        """Inbox id targeted by an operator write (plano inboxes/canais §4.7).

        Prefers the conversation's inbox; falls back to the channel's inbox when
        starting a brand-new conversation. ``None`` quando indeterminável — o
        chamador (``can_access_inbox``) nega para usuários escopados."""
        if conversation_id:
            try:
                conv = conversation_repo.get(int(conversation_id))
            except (TypeError, ValueError):
                conv = None
            if conv:
                return conv.get("inbox_id")
        cid = str(channel_id) if channel_id else "default"
        inbox = inbox_repo.get_by_channel(cid)
        return inbox["id"] if inbox else None

    async def _inbox_send_denied(request: Request, *, conversation_id=None,
                                 channel_id=None):
        """403 response if the user may not write to the target conversation's inbox.

        Returns ``None`` when allowed (legacy/open, read_all, or member). Closes the
        send half of Bug 2: tirar o usuário da caixa agora também bloqueia o envio."""
        inbox_id = await asyncio.to_thread(
            _resolve_inbox_id, conversation_id, channel_id)
        if not can_access_inbox(request, inbox_id):
            return _err("Sem acesso a esta caixa de entrada.", status=403)
        return None

    def _route_send_text(channel_id, phone, text, mentions=None, reply_to=None) -> str:
        """Send text via the conversation's channel. Raises GOWASendError on failure
        (so the existing handlers keep working); returns the external msg_id."""
        res = outbound.send_text(channel_id, phone, text, reply_to=reply_to, mentions=mentions)
        if not res.ok:
            raise GOWASendError(res.error or "Falha no envio")
        return res.external_msg_id or ""

    def _route_send_media(channel_id, phone, kind, path, caption="", filename=None) -> str:
        res = outbound.send_media(channel_id, phone, kind, path, caption=caption, filename=filename)
        if not res.ok:
            raise GOWASendError(res.error or "Falha no envio de mídia")
        return res.external_msg_id or ""

    def _session_window_block(channel_id, conversation_id, phone=None):
        """Guard for the WhatsApp Cloud 24h free-text window (plano 02 P17).

        Returns a 409 ``_err`` when free text/media is NOT allowed right now —
        i.e. a windowed channel (Cloud, ``session_window_hours>0``) whose last
        inbound is older than the window (or has none). Returns ``None`` when the
        send is allowed, which is ALWAYS the case for always-open channels
        (GOWA/Telegram, ``session_window_hours==0``). Capability-driven — never by
        provider name. Outside the window only an approved template may be sent.
        The agentic auto-reply (webhook) does not pass through here and is
        inherently in-window, so it is never affected.

        When ``conversation_id`` is absent (a BRAND-NEW conversation from the "Nova
        conversa" modal — plano 21) but ``phone`` is given, resolves the contact's
        latest conversation in this channel's inbox so an already-open 24h window is
        honored (otherwise a fresh send would be wrongly blocked even mid-window).
        """
        caps = outbound.capabilities(channel_id)
        if not getattr(caps, "session_window_hours", 0):
            return None
        last_ts = None
        if conversation_id:
            try:
                last_ts = message_repo.last_inbound_ts(conversation_id=int(conversation_id))
            except (TypeError, ValueError):
                last_ts = None
        elif phone:
            contact = contact_repo.get_by_phone(phone)
            if contact:
                from db.repositories import inbox_repo
                inbox = inbox_repo.get_by_channel(channel_id)
                conv = (conversation_repo.get_latest_for_contact_inbox(
                    contact["id"], inbox["id"]) if inbox else None)
                if conv:
                    last_ts = message_repo.last_inbound_ts(conversation_id=conv["id"])
        if outbound.session_open(channel_id, last_ts):
            return None
        return _err(
            "Fora da janela de 24h: só é possível enviar um template aprovado.",
            status=409, data={"reason": "session_window_closed"})

    async def _send_read_receipts(phone: str, msg_ids: list[str]):
        """Send read receipts to GOWA in background (best-effort)."""
        for mid in msg_ids:
            try:
                await asyncio.to_thread(gowa_client.mark_as_read, mid, phone)
                logger.info("[ReadReceipt] Sent for %s msg %s", phone, mid)
            except Exception as e:
                logger.warning("[ReadReceipt] Failed for %s msg %s: %s", phone, mid, e)

    @app.get("/api/contacts")
    async def list_contacts(request: Request, q: str = "", archived: bool = False):
        """List all contacts with summary info."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        # Inbox-membership scoping (plano inboxes/canais §4.7): a sidebar
        # conversa-cêntrica carrega esta lista; sem o filtro ela vazava contatos de
        # caixas que o usuário não acessa. Espelha GET /api/conversations.
        results = await asyncio.to_thread(
            contact_repo.list_contacts, q, archived, visible_inbox_ids(request))
        # Cache-busting version for each avatar (file mtime) so updated photos
        # are picked up by the browser instead of the stale cached image.
        for c in results:
            c["avatar_v"] = avatar_version(settings, c.get("phone", ""))
        return _ok(results)

    @app.post("/api/contacts/check-phone")
    async def check_phone(request: Request):
        """Check if a phone number is registered on WhatsApp."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        body = await request.json()
        phone = (body.get("phone") or "").strip()
        if not phone:
            return _err("Campo 'phone' é obrigatório.")

        # Normalize: strip non-digits, ensure country code
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) < 10:
            return _err("Número inválido. Informe DDD + número.")
        if not digits.startswith("55"):
            digits = "55" + digits

        # Route the check through the SELECTED channel (plano 21): GOWA actually
        # queries WhatsApp; Cloud API / Telegram can't verify before sending, so
        # they inherit the base "assume valid". Without a channel_id (legacy
        # "Iniciar conversa" da busca) keep the original GOWA behavior.
        channel_id = (body.get("channel_id") or "").strip() or None
        assumed = False
        try:
            if channel_id:
                result = await asyncio.to_thread(outbound.check_phone, channel_id, digits)
            else:
                result = await asyncio.to_thread(gowa_client.check_phone, digits)
        except GOWASendError as e:
            # Verification couldn't run (e.g. GOWA logged in but momentarily
            # disconnected from WhatsApp's services server → HTTP 401
            # "not connect to services server"). Rather than blocking contact
            # creation on a transient connectivity hiccup, fall back to the same
            # "assume valid" behavior used by Cloud API / Telegram channels. A
            # number that is genuinely NOT on WhatsApp does not raise — it
            # returns is_on_whatsapp=false and is handled below.
            logger.warning("check_phone: verification failed, assuming valid (%s)", e)
            result = {"registered": True, "canonical_phone": digits, "name": ""}
            assumed = True

        registered = result.get("registered", False)
        name = result.get("name", "")
        # Use canonical phone from WhatsApp (avoids BR 12/13 digit duplicates)
        canonical = result.get("canonical_phone", digits) if registered else digits

        # `create=false` (ex: verificação ao vivo no modal "Nova conversa") apenas
        # valida o número, sem materializar o contato — assim ele só aparece na
        # sidebar quando uma mensagem é de fato enviada. Default True preserva o
        # fluxo legado (botão "Iniciar conversa" da busca).
        should_create = body.get("create", True) is not False

        # If registered, pre-create contact with WhatsApp name and AI setting
        if registered and should_create:
            ai_default = settings.get("default_ai_enabled", True)
            def _save():
                contact_repo.get_or_create(canonical, default_ai_enabled=ai_default)
                if name:
                    c = contact_repo.get_by_phone(canonical)
                    if c and not c["name"]:
                        contact_repo.update(c["id"], name=f"~{name}")
            await asyncio.to_thread(_save)

        return _ok({
            "phone": canonical,
            "registered": registered,
            "jid": result.get("jid", ""),
            "name": name,
            "assumed": assumed,
        })

    @app.get("/api/contacts/unread-count")
    async def unread_count(request: Request):
        """Number of conversations with unread messages (for the browser-tab badge).

        Declared before /api/contacts/{phone} so the static path wins over the
        path parameter."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        count = await asyncio.to_thread(contact_repo.unread_conversation_count)
        return _ok({"count": count})

    @app.get("/api/contacts/export")
    async def export_contacts(request: Request):
        """Download all contacts as a CSV file (Chatwoot-style export).

        Declared before /api/contacts/{phone} so the static path wins over the
        path parameter. Scoped by inbox membership, same as the list endpoint."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        rows = await asyncio.to_thread(
            contact_repo.list_for_export, visible_inbox_ids(request))
        # Custom attribute definitions (plano 05) become extra CSV columns,
        # dynamically — a newly created attribute shows up here automatically.
        attr_defs = await asyncio.to_thread(ca_repo.list_definitions, "contact")

        output = io.StringIO()
        writer = csv.writer(output)
        header = ["phone", "name", "email", "profession", "company",
                  "address", "ai_enabled", "tags"]
        header.extend(d["attribute_key"] for d in attr_defs)
        writer.writerow(header)
        for r in rows:
            custom = r.get("custom_attributes") or {}
            row_out = [
                r["phone"], r["name"], r["email"], r["profession"],
                r["company"], r["address"],
                "1" if r["ai_enabled"] else "0",
                ", ".join(r["tags"]),
            ]
            for d in attr_defs:
                row_out.append(_format_attr_cell(custom.get(d["attribute_key"])))
            writer.writerow(row_out)
        # BOM (﻿) so Excel opens the UTF-8 file with the right encoding.
        content = "﻿" + output.getvalue()
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="contatos.csv"'},
        )

    @app.post("/api/contacts/import")
    async def import_contacts(request: Request, file: UploadFile = File(...)):
        """Import contacts from a CSV file (Chatwoot-style import).

        Expected header: phone, name, email, profession, company, address,
        ai_enabled, tags (PT-BR/Chatwoot aliases accepted; only ``phone`` is
        required). Existing contacts (matched by phone) are updated — only
        non-empty cells overwrite, so a sparse CSV never wipes saved info. New
        tags referenced in the ``tags`` column are created on the fly.

        Custom attributes (plano 05): any extra column whose header matches an
        existing attribute definition by name (attribute_key OR display_name) is
        imported into the contact's custom_attributes; values are type-validated
        and an invalid/unrecognized value is silently discarded. Columns that
        don't match any defined attribute are ignored (for now) — the contact is
        still saved regardless."""
        denied = permission_denied(request, "contact.write")
        if denied:
            return denied

        raw = await file.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("latin-1")
            except UnicodeDecodeError:
                return _err("Não foi possível ler o arquivo. Use CSV em UTF-8.")

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return _err("Arquivo CSV vazio ou sem cabeçalho.")

        # Map each canonical field to the actual header present in the file.
        header_lookup = {(h or "").strip().lower(): h for h in reader.fieldnames}
        col = {}
        for field, aliases in _IMPORT_COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in header_lookup:
                    col[field] = header_lookup[alias]
                    break
        if "phone" not in col:
            return _err("CSV precisa de uma coluna de telefone (phone/telefone).")

        ai_default = settings.get("default_ai_enabled", True)

        def _cell(row, field):
            src = col.get(field)
            return (row.get(src) or "").strip() if src else ""

        # Map extra CSV columns to custom attribute definitions by name
        # (attribute_key OR display_name, case-insensitive). Columns consumed by
        # core fields are excluded; columns matching no definition are dropped.
        attr_defs_map = ca_repo.get_definitions_map("contact")
        attr_by_name = {}
        for key, d in attr_defs_map.items():
            attr_by_name[key.strip().lower()] = d
            display = (d.get("display_name") or "").strip().lower()
            if display:
                attr_by_name.setdefault(display, d)
        consumed_headers = {(h or "").strip().lower() for h in col.values()}
        custom_cols = {}  # actual CSV header -> definition
        for h in reader.fieldnames:
            hl = (h or "").strip().lower()
            if not hl or hl in consumed_headers:
                continue
            d = attr_by_name.get(hl)
            if d is not None:
                custom_cols[h] = d

        def _do_import():
            existing_tags = set(tag_repo.get_all().keys())
            imported = updated = skipped = 0
            errors = []
            for idx, row in enumerate(reader, start=2):  # row 1 = header
                phone = _normalize_import_phone(_cell(row, "phone"))
                if not phone:
                    skipped += 1
                    errors.append({"row": idx, "error": "Telefone inválido."})
                    continue

                existed = contact_repo.get_by_phone(phone) is not None
                contact = contact_repo.get_or_create(
                    phone, default_ai_enabled=ai_default)
                cid = contact["id"]

                fields = {}
                for f in ("name", "email", "profession", "company", "address"):
                    val = _cell(row, f)
                    if val:
                        fields[f] = val
                ai_raw = _cell(row, "ai_enabled").lower()
                if ai_raw:
                    fields["ai_enabled"] = 0 if ai_raw in ("0", "false", "nao",
                                                           "não", "no", "off") else 1
                if fields:
                    contact_repo.update(cid, **fields)

                tags_raw = _cell(row, "tags")
                if tags_raw:
                    names = [t.strip() for t in tags_raw.replace(";", ",").split(",")
                             if t.strip()]
                    for name in names:
                        if name not in existing_tags:
                            tag_repo.create(name, _IMPORT_TAG_COLOR)
                            existing_tags.add(name)
                    if names:
                        tag_repo.set_contact_tags(cid, names)

                # Custom attributes: only columns matching a defined attribute by
                # name are kept; invalid values are discarded (contact still saved).
                if custom_cols:
                    partial = {}
                    for header, d in custom_cols.items():
                        raw_val = (row.get(header) or "").strip()
                        if not raw_val:
                            continue
                        norm, err = validate_value(d, raw_val)
                        if err or norm is None:
                            continue
                        partial[d["attribute_key"]] = norm
                    if partial:
                        ca_repo.set_values(contacts_table, cid, partial)

                if existed:
                    updated += 1
                else:
                    imported += 1
            return imported, updated, skipped, errors

        imported, updated, skipped, errors = await asyncio.to_thread(_do_import)
        return _ok({
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "errors": errors[:50],  # cap to keep the response small
        })

    @app.get("/api/contacts/{phone}")
    async def get_contact(phone: str, request: Request, mark_read: bool = True,
                          channel_id: str = ""):
        """Return full contact data including conversation history.

        Quando ``channel_id`` é informado (multicanal — abrir uma conversa NOVA pela
        caixa de entrada escolhida, antes de existir uma conversa nessa caixa), o
        thread é escopado ao canal: carrega só as mensagens da conversa daquele canal
        (vazio se ainda não houver). Sem ``channel_id`` o comportamento legado é
        mantido (funde as mensagens de todos os canais do mesmo número). NUNCA cair na
        conversa de OUTRO canal — caixas de entrada não se confundem."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        vis = visible_inbox_ids(request)
        channel = (channel_id or "").strip()
        def _load():
            data = contact_repo.get_full_contact(phone)
            if data is None:
                # Auto-create contact for verified phone numbers
                ai_default = settings.get("default_ai_enabled", True)
                contact_repo.get_or_create(phone, default_ai_enabled=ai_default)
                data = contact_repo.get_full_contact(phone)
            if data is None:
                return None, []
            contact_id = data["id"]
            # Inbox-membership scoping (plano inboxes/canais §4.7): hide (as 404,
            # BEFORE any mark-read side effect) a contact whose conversations are
            # all in inboxes the user can't access. Espelha o _inbox_hidden da view
            # conversa-cêntrica e fecha o vazamento da view legada por contato.
            if contact_repo.contact_hidden_by_inbox_scope(contact_id, vis):
                return "__hidden__", []
            # Channel-scoped resolution (multicanal): the inbox picker chose a
            # specific channel for a brand-new conversation. Resolve that channel's
            # conversation (may be None) and scope the thread to it, so a new GOWA
            # thread never shows the Cloud API conversation (and vice-versa).
            scoped_conv = None
            if channel:
                from db.repositories import inbox_repo
                inbox = inbox_repo.get_by_channel(channel)
                if inbox:
                    scoped_conv = conversation_repo.get_latest_for_contact_inbox(
                        contact_id, inbox["id"])
            # Mark as read when viewing contact (skip if mark_read=false)
            msg_ids = []
            if mark_read and (data.get("unread_count", 0) > 0 or data.get("unread_ai_count", 0) > 0):
                msg_ids = contact_repo.mark_as_read(contact_id)
                data["unread_count"] = 0
                data["unread_ai_count"] = 0
                # Update in-memory cache (all channel-variants of this phone)
                for cm in agent_handler.iter_cached_contacts(phone):
                    cm.unread_count = 0
                    cm.unread_ai_count = 0
            # Load messages — scoped to the chosen channel's conversation when given
            # (empty list when that channel has no conversation yet), else legacy
            # all-channels view.
            if channel:
                data["channel_id"] = channel
                data["conversation_id"] = scoped_conv["id"] if scoped_conv else None
                data["messages"] = (message_repo.get_by_conversation(scoped_conv["id"])
                                    if scoped_conv else [])
                # Compositor hints (Frente C / plano 21): mesmo SEM conversa ainda, o
                # canal escolhido define se aceita template e se a janela de texto livre
                # está aberta. Sem isso, abrir um canal Cloud (windowed) sem conversa não
                # mostra o botão de template — e Cloud exige template fora da janela 24h.
                last_ts = (message_repo.last_inbound_ts(
                    conversation_id=scoped_conv["id"]) if scoped_conv else None)
                data["templates_supported"] = outbound.supports(channel, "templates")
                data["session_open"] = outbound.session_open(channel, last_ts)
            else:
                data["messages"] = message_repo.get_all(contact_id)
            # Load usage for the full response
            data["usage"] = []
            return data, msg_ids
        data, msg_ids = await asyncio.to_thread(_load)
        if data is None or data == "__hidden__":
            return _err("Contato não encontrado.", status=404)
        if msg_ids:
            asyncio.create_task(_send_read_receipts(phone, msg_ids))
        # Check group send permissions (fresh check on every contact load)
        if data.get("is_group") and state.bot_phone:
            try:
                can_send = await asyncio.to_thread(
                    gowa_client.can_bot_send_in_group, phone, state.bot_phone)
                if data.get("can_send", True) != can_send:
                    await asyncio.to_thread(
                        contact_repo.update, data["id"], can_send=1 if can_send else 0)
                    data["can_send"] = can_send
                    for cm in agent_handler.iter_cached_contacts(phone):
                        cm.can_send = can_send
            except Exception as e:
                logger.warning("[Contact] Failed to check group send permission: %s", e)
        # Opening a conversation triggers a best-effort avatar refresh in the
        # background; if the photo changed, an `avatar_updated` WS event updates
        # it live. Include the current version for immediate cache-busting.
        data["avatar_v"] = avatar_version(settings, phone)
        asyncio.create_task(refresh_and_broadcast(deps, phone))
        return _ok(data)

    @app.delete("/api/contacts/{phone}")
    async def delete_contact(phone: str, request: Request):
        """Permanently delete a contact and all associated data."""
        denied = permission_denied(request, "contact.write")
        if denied:
            return denied
        def _delete():
            data = contact_repo.get_by_phone(phone)
            if data is None:
                return False
            contact_repo.delete(data["id"])
            # Clear in-memory cache (all channel-variants)
            agent_handler.drop_cached_contact(phone)
            return True
        found = await asyncio.to_thread(_delete)
        if not found:
            return _err("Contato não encontrado.", status=404)
        logger.info("[Contact] Deleted contact %s", phone)
        await ws_manager.broadcast("contact_deleted", {"phone": phone})
        return _ok({"message": "Contato apagado."})

    @app.post("/api/contacts/{phone}/archive")
    async def archive_contact(phone: str, body: dict, request: Request):
        """Archive or unarchive a contact (by app)."""
        denied = permission_denied(request, "contact.write")
        if denied:
            return denied
        archived = body.get("archived")
        if archived is None:
            return _err("Campo 'archived' é obrigatório.")
        def _archive():
            data = contact_repo.get_by_phone(phone)
            if data is None:
                return None
            contact_repo.set_archived(data["id"], bool(archived), by_app=True)
            # Update in-memory cache (all channel-variants of this phone)
            for cm in agent_handler.iter_cached_contacts(phone):
                cm.is_archived = bool(archived)
                cm.archived_by_app = bool(archived)
            return bool(archived)
        result = await asyncio.to_thread(_archive)
        if result is None:
            return _err("Contato não encontrado.", status=404)
        logger.info("[Contact] %s contact %s", "Archived" if result else "Unarchived", phone)
        await ws_manager.broadcast("contact_archived", {"phone": phone, "archived": result})
        return _ok({"archived": result})

    @app.post("/api/contacts/{phone}/pin")
    async def pin_contact(phone: str, body: dict, request: Request):
        """Pin or unpin a conversation (pinned ones sort to the top of the list)."""
        denied = permission_denied(request, "contact.write")
        if denied:
            return denied
        pinned = body.get("pinned")
        if pinned is None:
            return _err("Campo 'pinned' é obrigatório.")
        def _pin():
            data = contact_repo.get_by_phone(phone)
            if data is None:
                return None
            contact_repo.set_pinned(data["id"], bool(pinned))
            return bool(pinned)
        result = await asyncio.to_thread(_pin)
        if result is None:
            return _err("Contato não encontrado.", status=404)
        logger.info("[Contact] %s contact %s", "Pinned" if result else "Unpinned", phone)
        await ws_manager.broadcast("contact_pinned", {"phone": phone, "pinned": result})
        return _ok({"pinned": result})

    @app.post("/api/contacts/{phone}/send")
    async def send_to_contact(phone: str, body: dict, request: Request):
        """Send a manual message to a contact (operator-initiated, no LLM)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        message = (body.get("message") or "").strip()
        if not message:
            return _err("Campo 'message' é obrigatório.")
        # Optional: quote/reply to an existing message (GOWA msg_id).
        reply_to = (body.get("reply_to") or "").strip() or None

        # Plugin filter: allow plugins to add signature/formatting/redact to operator sends
        filtered = await apply_filter(
            "filter.reply.part", message,
            {"phone": phone, "index": 0, "total": 1, "source": "operator"},
        )
        if filtered is None:
            return _err("Mensagem bloqueada por plugin.", status=400)
        message = filtered

        # Sandbox/test contact — never goes over GOWA (the number isn't real).
        # Persist the operator message locally and broadcast it, no error.
        if await asyncio.to_thread(_is_sandbox_contact, phone):
            msg_data = await asyncio.to_thread(
                agent_handler.save_operator_message, phone, message, status="operator",
                reply_to_msg_id=reply_to,
            )
            await ws_manager.broadcast("new_message", {"phone": phone, "message": msg_data})
            await emit_with_filter("message.sent", {
                "phone": phone, "text": message, "msg_id": None,
                "media_type": None, "media_path": None,
                "source": "operator", "status": "operator",
                "reply_to_msg_id": reply_to,
                "ts": time.time(),
            })
            logger.info("[Send] Sandbox contact %s — message saved locally (no GOWA)", phone)
            return _ok({"message": "Mensagem enviada.", "msg_id": None})

        denied_inbox = await _inbox_send_denied(
            request, conversation_id=body.get("conversation_id"),
            channel_id=body.get("channel_id"))
        if denied_inbox:
            return denied_inbox

        channel_id = _channel_for(phone, body.get("conversation_id"), body.get("channel_id"))
        block = await asyncio.to_thread(
            _session_window_block, channel_id, body.get("conversation_id"), phone)
        if block:
            return block
        # Resolve @Name / @todos -> real mentions for group targets, only on channels
        # that support groups (Cloud is 1:1). `message` (friendly @Name) is saved/shown;
        # `send_text` (inline @<number>) + mentions go on the wire.
        send_text, mentions = message, None
        if "@g.us" in phone and outbound.supports(channel_id, "groups"):
            send_text, mentions = await asyncio.to_thread(
                group_mentions.resolve_outgoing, phone, message)

        # Track sent message to filter echo-backs (key matches the webhook: channel:phone:text)
        state.recently_sent[f"{channel_id}:{phone}:{send_text[:120]}"] = time.time()

        # Send via the conversation's channel — always save message (status on failure)
        send_failed = False
        error_msg = ""
        msg_id = None
        try:
            msg_id = await asyncio.to_thread(
                _route_send_text, channel_id, phone, send_text, mentions, reply_to)
        except GOWASendError as e:
            logger.error("[Send] Failed to send message to %s: %s", phone, e)
            send_failed = True
            error_msg = str(e)
        except Exception as e:
            logger.error("[Send] Failed to send message to %s: %s", phone, e)
            send_failed = True
            error_msg = str(e)

        if send_failed:
            msg_id = None

        # Always save to contact memory (with status="failed" if send failed)
        try:
            msg_data = await asyncio.to_thread(
                agent_handler.save_operator_message, phone, message,
                status="failed" if send_failed else "operator",
                msg_id=msg_id, reply_to_msg_id=reply_to, channel_id=channel_id,
            )
        except Exception as e:
            logger.error("[Send] Failed to save message for %s: %s", phone, e)
            return _err(f"Erro ao salvar mensagem: {e}", status=500)

        if send_failed:
            # Broadcast error event for frontend toast/error bubble
            await _emit_send_error(ws_manager, phone, f"Falha ao enviar mensagem: {error_msg}")
            return _err(f"Falha ao enviar mensagem: {error_msg}", status=500)

        logger.info("[Send] Manual message to %s: %s", phone, message[:80])

        # Broadcast to all WS clients
        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "channel_id": channel_id,
            "message": msg_data,
        })

        # Plugin event: manual operator send
        await emit_with_filter("message.sent", {
            "phone": phone, "text": message, "msg_id": msg_id,
            "media_type": None, "media_path": None,
            "source": "operator", "status": "operator",
            "reply_to_msg_id": reply_to,
            "ts": time.time(),
        })

        return _ok({"message": "Mensagem enviada.", "msg_id": msg_id})

    @app.post("/api/contacts/{phone}/messages/delete")
    async def delete_message(phone: str, body: dict, request: Request):
        """Delete a message. scope='me' (local) or scope='all' (revoke for everyone).

        Identifies the message by GOWA ``msg_id`` (preferred) or, for local-only
        messages without one, by DB ``db_id``. ``scope='all'`` is only allowed for
        the operator's own (outgoing) messages and requires a msg_id.

        In every case the message is deleted on WhatsApp (via GOWA) but KEPT in our
        DB — it is only flagged as revoked, never hard-deleted — so the panel keeps
        showing it with a 'deleted' indicator.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        scope = (body.get("scope") or "me").strip()
        msg_id = (body.get("msg_id") or "").strip()
        db_id = body.get("db_id")
        if scope not in ("me", "all"):
            return _err("scope inválido (use 'me' ou 'all').")
        if not msg_id and not db_id:
            return _err("É necessário msg_id ou db_id.")

        denied_inbox = await _inbox_send_denied(
            request, conversation_id=body.get("conversation_id"))
        if denied_inbox:
            return denied_inbox

        is_sandbox = await asyncio.to_thread(_is_sandbox_contact, phone)

        if scope == "all":
            if not msg_id:
                return _err("Apagar para todos exige uma mensagem já enviada ao WhatsApp.", status=400)
            # Only outgoing (own) messages can be revoked for everyone.
            msg = await asyncio.to_thread(message_repo.get_by_msg_id, msg_id)
            if msg and msg.get("role") == "user":
                return _err("Só é possível apagar para todos as suas próprias mensagens.", status=400)
            if not is_sandbox:
                channel_id = _channel_for(phone, body.get("conversation_id"))
                await asyncio.to_thread(outbound.revoke, channel_id, phone, msg_id)
            await asyncio.to_thread(message_repo.mark_revoked, msg_id, "all")
            await ws_manager.broadcast("message_revoked", {"phone": phone, "msg_id": msg_id})
            await emit_with_filter("message.revoked", {
                "id": msg_id, "phone": phone,
                "revoked_message_id": msg_id, "revoked_from_me": True,
                "ts": time.time(),
            })
            logger.info("[Delete] Revoked (all) msg %s for %s", msg_id, phone)
            return _ok({"message": "Mensagem apagada para todos.", "msg_id": msg_id})

        # scope == "me": delete on the linked device via GOWA, but keep the row in
        # our DB (flagged revoked) so the panel still shows it.
        was_from_me = True
        if msg_id:
            msg = await asyncio.to_thread(message_repo.get_by_msg_id, msg_id)
            was_from_me = (msg or {}).get("role") != "user"
            if not is_sandbox:
                # "Delete for me" is a GOWA/linked-device local op; no Cloud equivalent.
                channel_id = _channel_for(phone, body.get("conversation_id"))
                if channel_id == "default":
                    await asyncio.to_thread(gowa_client.delete_message, msg_id, phone)
            await asyncio.to_thread(message_repo.mark_revoked, msg_id, "me")
        if db_id:
            await asyncio.to_thread(message_repo.mark_revoked_by_id, int(db_id), "me")
        await ws_manager.broadcast("message_deleted", {
            "phone": phone, "msg_id": msg_id or None, "db_id": db_id,
        })
        await emit_with_filter("message.deleted", {
            "phone": phone, "deleted_message_id": msg_id or "",
            "was_from_me": was_from_me, "ts": time.time(),
        })
        logger.info("[Delete] Revoked (me, kept in DB) msg %s/db %s for %s", msg_id, db_id, phone)
        return _ok({"message": "Mensagem apagada para você.", "msg_id": msg_id or None})

    @app.post("/api/contacts/{phone}/messages/react")
    async def react_to_message(phone: str, body: dict, request: Request):
        """React to a message with an emoji. Empty emoji removes the operator's reaction.

        The operator's own reaction is stored under the sentinel reactor "me".
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        msg_id = (body.get("msg_id") or "").strip()
        emoji = (body.get("emoji") or "").strip()
        if not msg_id:
            return _err("msg_id é obrigatório.")

        denied_inbox = await _inbox_send_denied(
            request, conversation_id=body.get("conversation_id"))
        if denied_inbox:
            return denied_inbox

        if not await asyncio.to_thread(_is_sandbox_contact, phone):
            channel_id = _channel_for(phone, body.get("conversation_id"))
            await asyncio.to_thread(outbound.react, channel_id, phone, msg_id, emoji)
        reactions = await asyncio.to_thread(message_repo.set_reaction, msg_id, emoji, "me")
        if reactions is None:
            return _err("Mensagem não encontrada.", status=404)
        await ws_manager.broadcast("message_reaction", {
            "phone": phone, "msg_id": msg_id, "reactions": reactions,
        })
        await emit_with_filter("message.reaction", {
            "id": msg_id, "phone": phone,
            "reaction": emoji, "reacted_message_id": msg_id,
            "is_from_me": True, "ts": time.time(),
        })
        logger.info("[React] %s reacted %r to msg %s", phone, emoji, msg_id)
        return _ok({"message": "Reação registrada.", "reactions": reactions})

    @app.post("/api/contacts/{phone}/improve")
    async def improve_message(phone: str, body: dict, request: Request):
        """Gerar uma análise de melhoria para uma resposta da IA marcada como incorreta.

        O operador clica com o botão direito numa resposta da IA → "Gerar melhoria"
        → (opcionalmente) escreve o que saiu errado → o LLM analisa o prompt, as
        ferramentas e o histórico e devolve um diagnóstico + recomendações. O
        resultado é salvo como mensagem ``role="system"`` (painel-only, excluída do
        contexto do LLM) e transmitido via WS ``new_message``."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        target = body.get("message") or {}
        feedback = (body.get("feedback") or "").strip()
        if not (target.get("content") or "").strip():
            return _err("Mensagem inválida para análise.")
        try:
            analysis = await asyncio.to_thread(
                agent_handler.generate_improvement, phone, target, feedback)
        except Exception as e:
            logger.exception("Falha ao gerar análise de melhoria para %s", phone)
            return _err(f"Erro ao gerar análise: {e}", status=500)

        note_text = f"🔧 Análise de melhoria\n\n{analysis}"

        def _save():
            contact = agent_handler._get_contact(phone)
            contact.add_message("system", note_text)
            return message_repo.get_last(contact.id)

        note_msg = await asyncio.to_thread(_save)
        if not note_msg:
            return _err("Falha ao salvar a análise.", status=500)
        await ws_manager.broadcast("new_message", {"phone": phone, "message": note_msg})
        return _ok(note_msg)

    async def _run_private_ai(phone: str, text: str, reply_in_chat: bool = True,
                              conversation_id=None):
        """Process a private message via the LLM.

        Triggered by the operator's "IA lê" toggle on the private-message panel.
        Tool calls run normally and their cards are broadcast to the panel.

        If `reply_in_chat` is True, the LLM reply is sent to the contact as a
        regular assistant message. If False, each reply part is saved as a
        private note (stays only in the panel).
        """
        try:
            result = await agent_handler.aprocess_message(
                phone, text,
                save_user_message=False,
                save_response=False,
            )
            reply_text = (result.reply or "").strip()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[PrivateAI] aprocess_message failed for %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Erro ao processar IA: {e}")
            return

        if result.tool_calls:
            try:
                await deps.broadcast_tool_calls(
                    phone, result.tool_calls, result.contact_info)
            except Exception as e:
                logger.warning("[PrivateAI] broadcast_tool_calls failed for %s: %s",
                               phone, e)

        if not reply_text:
            return

        # The LLM may return a JSON array of strings when split_messages is on.
        parts = (parse_split_reply(reply_text)
                 if settings.get("split_messages", True) else [reply_text])
        filter_source = "private_ai" if reply_in_chat else "private_ai_note"
        # Plugin filter on the part list (can reorder/add/remove).
        parts = await apply_filter("filter.reply.parts", parts, {"phone": phone, "source": filter_source})
        if parts is None or not parts:
            return

        for i, part in enumerate(parts):
            part = await apply_filter(
                "filter.reply.part", part,
                {"phone": phone, "index": i, "total": len(parts), "source": filter_source},
            )
            if part is None:
                continue

            if not reply_in_chat:
                # AI reply stays in the panel as a private note.
                saved_note = None
                try:
                    def _save_note(p=part):
                        contact = agent_handler._get_contact(phone)
                        contact.add_message("private_note", p)
                        return message_repo.get_last(contact.id)
                    saved_note = await asyncio.to_thread(_save_note)
                except Exception as e:
                    logger.error("[PrivateAI] failed to save private note: %s", e)
                note_msg = {
                    "role": "private_note",
                    "content": part,
                    "ts": (saved_note or {}).get("ts", time.time()),
                    "status": None,
                }
                if saved_note and saved_note.get("_id"):
                    note_msg["_id"] = saved_note["_id"]
                await ws_manager.broadcast("new_message", {"phone": phone, "message": note_msg})
                continue

            channel_id = _channel_for(phone, conversation_id)
            state.recently_sent[f"{channel_id}:{phone}:{part[:120]}"] = time.time()
            send_failed = False
            send_error = ""
            msg_id = None
            try:
                msg_id = await asyncio.to_thread(
                    _route_send_text, channel_id, phone, part)
            except GOWASendError as e:
                logger.error("[PrivateAI] send failed for %s: %s", phone, e)
                send_failed = True
                send_error = str(e)
            except Exception as e:
                logger.error("[PrivateAI] send failed for %s: %s", phone, e)
                send_failed = True
                send_error = str(e)

            if send_failed:
                msg_id = None
            if msg_id:
                state.processed_messages.add(msg_id)

            try:
                msg_data = await asyncio.to_thread(
                    agent_handler.save_assistant_message, phone, part,
                    msg_id=msg_id,
                    status="failed" if send_failed else "sent",
                )
            except Exception as e:
                logger.error("[PrivateAI] failed to save assistant message: %s", e)
                msg_data = {
                    "role": "assistant", "content": part, "ts": time.time(),
                    "status": "failed" if send_failed else "sent", "msg_id": msg_id,
                }

            if send_failed:
                await _emit_send_error(
                    ws_manager, phone, f"Falha ao enviar resposta da IA: {send_error}")
                return
            await ws_manager.broadcast("new_message", {
                "phone": phone, "channel_id": channel_id, "message": msg_data,
            })
            await emit_with_filter("message.sent", {
                "phone": phone, "text": part, "msg_id": msg_id,
                "media_type": None, "media_path": None,
                "source": "private_ai", "status": "sent",
                "ts": time.time(),
            })

    @app.post("/api/contacts/{phone}/private-message")
    async def send_private_message(phone: str, body: dict, request: Request):
        """Save a message that stays in the panel — never delivered to the contact.

        AI processing is triggered when the operator sets `ai_read=true` (UI
        toggle "IA lê"). `ai_reply` (default true) controls whether the AI
        reply goes to the WhatsApp chat or stays as a private note.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        text = (body.get("text") or "").strip()
        if not text:
            return _err("Campo 'text' é obrigatório.")

        denied_inbox = await _inbox_send_denied(
            request, conversation_id=body.get("conversation_id"),
            channel_id=body.get("channel_id"))
        if denied_inbox:
            return denied_inbox

        ai_read = bool(body.get("ai_read", False))
        ai_reply = bool(body.get("ai_reply", True))

        try:
            def _save():
                contact = agent_handler._get_contact(phone)
                contact.add_message("private_note", text)
                return message_repo.get_last(contact.id)
            saved = await asyncio.to_thread(_save)
        except Exception as e:
            logger.error("[Private] Failed to save private message for %s: %s", phone, e)
            return _err(f"Erro ao salvar mensagem privada: {e}", status=500)

        # Carry the DB row id so the panel can delete the note without a reload.
        note_msg = {
            "role": "private_note",
            "content": text,
            "ts": (saved or {}).get("ts", time.time()),
            "status": None,
        }
        if saved and saved.get("_id"):
            note_msg["_id"] = saved["_id"]
        await ws_manager.broadcast("new_message", {"phone": phone, "message": note_msg})

        if ai_read:
            asyncio.create_task(_run_private_ai(phone, text, reply_in_chat=ai_reply,
                                                conversation_id=body.get("conversation_id")))

        logger.info("[Private] Saved private note for %s (ai_read=%s, ai_reply=%s): %s",
                    phone, ai_read, ai_reply, text[:80])
        return _ok(note_msg)

    @app.post("/api/contacts/{phone}/retry-send")
    async def retry_send_to_contact(phone: str, body: dict, request: Request):
        """Retry sending a message that previously failed."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        message = (body.get("message") or "").strip()
        if not message:
            return _err("Campo 'message' é obrigatório.")

        denied_inbox = await _inbox_send_denied(
            request, conversation_id=body.get("conversation_id"),
            channel_id=body.get("channel_id"))
        if denied_inbox:
            return denied_inbox

        channel_id = _channel_for(phone, body.get("conversation_id"), body.get("channel_id"))
        block = await asyncio.to_thread(
            _session_window_block, channel_id, body.get("conversation_id"), phone)
        if block:
            return block
        # Track for echo-back filtering (key matches the webhook: channel:phone:text)
        state.recently_sent[f"{channel_id}:{phone}:{message[:120]}"] = time.time()

        msg_id = None
        try:
            msg_id = await asyncio.to_thread(_route_send_text, channel_id, phone, message)
        except GOWASendError as e:
            logger.error("[Retry] Failed to resend to %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Falha ao reenviar mensagem: {e}")
            return _err(f"Falha ao reenviar: {e}", status=500)
        except Exception as e:
            logger.error("[Retry] Failed to resend to %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Erro inesperado ao reenviar: {e}")
            return _err(f"Erro ao reenviar: {e}", status=500)

        # msg_id is the channel's external id (string)

        # Mark the existing failed message as sent
        try:
            await asyncio.to_thread(agent_handler.mark_message_sent, phone, message, msg_id)
        except Exception as e:
            logger.error("[Retry] Failed to update message status for %s: %s", phone, e)

        state.msg_count += 1
        await emit_with_filter("message.sent", {
            "phone": phone, "text": message, "msg_id": msg_id,
            "media_type": None, "media_path": None,
            "source": "retry", "status": "sent",
            "ts": time.time(),
        })
        logger.info("[Retry] Resent to %s: %s", phone, message[:80])
        return _ok({"message": "Mensagem reenviada."})

    @app.post("/api/contacts/{phone}/send-image")
    async def send_image_to_contact(
        phone: str,
        request: Request,
        image: UploadFile = File(...),
        caption: str = Form(""),
        conversation_id: str = Form(""),
        channel_id: str = Form(""),
    ):
        """Send an image to a contact (operator-initiated)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        denied_inbox = await _inbox_send_denied(
            request, conversation_id=conversation_id, channel_id=channel_id)
        if denied_inbox:
            return denied_inbox
        # Sandbox/test contact — keep the image local, never hit GOWA.
        is_sandbox = await asyncio.to_thread(_is_sandbox_contact, phone)
        channel_id = _channel_for(phone, conversation_id, channel_id)
        # 24h window gate BEFORE writing the file (no orphan on a blocked send);
        # sandbox stays local so it is never gated (mirrors /send text).
        if not is_sandbox:
            block = await asyncio.to_thread(_session_window_block, channel_id, conversation_id, phone)
            if block:
                return block
        suffix = Path(image.filename or "img.png").suffix or ".png"
        dest = statics_outbox_dir / f"{int(time.time() * 1000)}{suffix}"
        content = await image.read()
        dest.write_bytes(content)
        msg_id = None
        try:
            if not is_sandbox:
                msg_id = await asyncio.to_thread(
                    _route_send_media, channel_id, phone, "image", str(dest), caption)
        except GOWASendError as e:
            logger.error("[Send] Failed to send image to %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Falha ao enviar imagem: {e}")
            return _err(f"Falha ao enviar imagem: {e}", status=500)
        except Exception as e:
            logger.error("[Send] Failed to send image to %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Erro inesperado ao enviar imagem: {e}")
            return _err(f"Erro ao enviar imagem: {e}", status=500)

        # msg_id is the channel external id (None for sandbox). Mark it processed so
        # the webhook ignores the WhatsApp echo of our own message.
        if msg_id:
            state.processed_messages.add(msg_id)

        # Relative path for storage and frontend
        rel_path = f"statics/outbox/{dest.name}"
        msg_data = {
            "role": "assistant",
            "content": caption,
            "ts": time.time(),
            "media_type": "image",
            "media_path": rel_path,
            "status": "operator",
            "msg_id": msg_id,
        }
        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        contact.add_message("assistant", caption, media_type="image", media_path=rel_path,
                            status="operator", msg_id=msg_id)

        await ws_manager.broadcast("new_message", {"phone": phone, "channel_id": channel_id, "message": msg_data})
        await emit_with_filter("message.sent", {
            "phone": phone, "text": caption, "msg_id": msg_id,
            "media_type": "image", "media_path": rel_path,
            "source": "operator", "status": "operator",
            "ts": time.time(),
        })
        logger.info("[Send] Image sent to %s", phone)
        return _ok({"message": "Imagem enviada."})

    @app.post("/api/contacts/{phone}/send-audio")
    async def send_audio_to_contact(
        phone: str,
        request: Request,
        audio: UploadFile = File(...),
        conversation_id: str = Form(""),
        channel_id: str = Form(""),
    ):
        """Send an audio file to a contact (operator-initiated)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        denied_inbox = await _inbox_send_denied(
            request, conversation_id=conversation_id, channel_id=channel_id)
        if denied_inbox:
            return denied_inbox
        # Sandbox/test contact — keep the audio local, never hit GOWA.
        is_sandbox = await asyncio.to_thread(_is_sandbox_contact, phone)
        channel_id = _channel_for(phone, conversation_id, channel_id)
        # 24h window gate BEFORE writing the file (no orphan on a blocked send);
        # sandbox stays local so it is never gated (mirrors /send text).
        if not is_sandbox:
            block = await asyncio.to_thread(_session_window_block, channel_id, conversation_id, phone)
            if block:
                return block
        suffix = Path(audio.filename or "voice.ogg").suffix or ".ogg"
        dest = statics_outbox_dir / f"{int(time.time() * 1000)}{suffix}"
        content = await audio.read()
        dest.write_bytes(content)
        msg_id = None
        try:
            if not is_sandbox:
                msg_id = await asyncio.to_thread(
                    _route_send_media, channel_id, phone, "audio", str(dest))
        except GOWASendError as e:
            logger.error("[Send] Failed to send audio to %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Falha ao enviar áudio: {e}")
            return _err(f"Falha ao enviar áudio: {e}", status=500)
        except Exception as e:
            logger.error("[Send] Failed to send audio to %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Erro inesperado ao enviar áudio: {e}")
            return _err(f"Erro ao enviar áudio: {e}", status=500)

        # msg_id is the channel external id (None for sandbox). Mark it processed so
        # the webhook ignores the WhatsApp echo of our own message.
        if msg_id:
            state.processed_messages.add(msg_id)

        rel_path = f"statics/outbox/{dest.name}"
        msg_data = {
            "role": "assistant",
            "content": "[Áudio]",
            "ts": time.time(),
            "media_type": "audio",
            "media_path": rel_path,
            "status": "operator",
            "msg_id": msg_id,
        }
        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        contact.add_message("assistant", "[Áudio]", media_type="audio", media_path=rel_path,
                            status="operator", msg_id=msg_id)

        await ws_manager.broadcast("new_message", {"phone": phone, "channel_id": channel_id, "message": msg_data})
        await emit_with_filter("message.sent", {
            "phone": phone, "text": "", "msg_id": msg_id,
            "media_type": "audio", "media_path": rel_path,
            "source": "operator", "status": "operator",
            "ts": time.time(),
        })

        # Transcribe the operator-sent audio when enabled (audio_transcription_mode
        # in sent/both) so the panel/AI can read what was said — same private card
        # used for inbound media. Defensive: a transcription failure never breaks
        # the send (the audio was already delivered above).
        transcription = await maybe_transcribe(
            "audio", str(dest),
            settings=settings, agent_handler=agent_handler,
            phone=phone, source="operator",
            is_group=contact.is_group,
            group_jid=phone if contact.is_group else None,
        )
        if transcription:
            contact.add_message("transcription", transcription)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "channel_id": channel_id,
                "message": {
                    "role": "transcription",
                    "content": transcription,
                    "ts": time.time(),
                },
            })

        logger.info("[Send] Audio sent to %s", phone)
        return _ok({"message": "Áudio enviado."})

    @app.post("/api/contacts/{phone}/send-document")
    async def send_document_to_contact(
        phone: str,
        request: Request,
        document: UploadFile = File(...),
        caption: str = Form(""),
        conversation_id: str = Form(""),
        channel_id: str = Form(""),
    ):
        """Send an arbitrary file (document) to a contact (operator-initiated)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        denied_inbox = await _inbox_send_denied(
            request, conversation_id=conversation_id, channel_id=channel_id)
        if denied_inbox:
            return denied_inbox
        is_sandbox = await asyncio.to_thread(_is_sandbox_contact, phone)
        channel_id = _channel_for(phone, conversation_id, channel_id)
        # 24h window gate BEFORE writing the file (no orphan on a blocked send);
        # sandbox stays local so it is never gated (mirrors /send text).
        if not is_sandbox:
            block = await asyncio.to_thread(_session_window_block, channel_id, conversation_id, phone)
            if block:
                return block
        filename = document.filename or "arquivo"
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix
        stem = Path(safe_name).stem or "arquivo"
        dest = statics_outbox_dir / f"{int(time.time() * 1000)}_{stem}{suffix}"
        content = await document.read()
        dest.write_bytes(content)
        msg_id = None
        try:
            if not is_sandbox:
                msg_id = await asyncio.to_thread(
                    _route_send_media, channel_id, phone, "document", str(dest),
                    caption, safe_name)
        except GOWASendError as e:
            logger.error("[Send] Failed to send document to %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Falha ao enviar documento: {e}")
            return _err(f"Falha ao enviar documento: {e}", status=500)
        except Exception as e:
            logger.error("[Send] Failed to send document to %s: %s", phone, e)
            await _emit_send_error(ws_manager, phone, f"Erro inesperado ao enviar documento: {e}")
            return _err(f"Erro ao enviar documento: {e}", status=500)

        # msg_id is the channel external id (None for sandbox).
        if msg_id:
            state.processed_messages.add(msg_id)

        rel_path = f"statics/outbox/{dest.name}"
        text_content = f"[Documento enviado: {safe_name}]"
        if caption.strip():
            text_content = f"{text_content}\n{caption.strip()}"

        msg_data = {
            "role": "assistant",
            "content": text_content,
            "ts": time.time(),
            "media_type": "document",
            "media_path": rel_path,
            "status": "operator",
            "msg_id": msg_id,
        }
        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        contact.add_message("assistant", text_content, media_type="document",
                            media_path=rel_path, status="operator", msg_id=msg_id)

        await ws_manager.broadcast("new_message", {"phone": phone, "channel_id": channel_id, "message": msg_data})
        await emit_with_filter("message.sent", {
            "phone": phone, "text": caption, "msg_id": msg_id,
            "media_type": "document", "media_path": rel_path,
            "source": "operator", "status": "operator",
            "ts": time.time(),
        })
        logger.info("[Send] Document sent to %s: %s", phone, safe_name)
        return _ok({"message": "Documento enviado."})

    @app.post("/api/contacts/{phone}/presence")
    async def send_presence_to_contact(phone: str, body: dict, request: Request):
        """Send typing/stop presence indicator to a contact (operator-initiated).

        Capability-gated: channels without presence (e.g. WhatsApp Cloud) no-op."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        action = body.get("action", "start")
        channel_id = _channel_for(phone, body.get("conversation_id"))
        await asyncio.to_thread(outbound.send_presence, channel_id, phone, action)
        return _ok({"status": "ok"})

    @app.post("/api/contacts/{phone}/read")
    async def mark_contact_read(phone: str, request: Request):
        """Mark all messages from this contact as read (reset unread_count)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        def _mark():
            contact = agent_handler._get_contact(phone)
            return contact.mark_as_read()
        msg_ids = await asyncio.to_thread(_mark)
        if msg_ids:
            asyncio.create_task(_send_read_receipts(phone, msg_ids))
        return _ok({"message": "Marcado como lido."})

    @app.post("/api/contacts/mark-all-unread")
    async def mark_all_contacts_unread(request: Request):
        """Mark every conversation as unread (re-light the in-app green badge)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        def _mark():
            count = contact_repo.mark_all_as_unread()
            # Keep already-loaded ContactMemory caches consistent.
            for contact in agent_handler._contacts.values():
                if contact.unread_count < 1:
                    contact.unread_count = 1
            return count
        count = await asyncio.to_thread(_mark)
        return _ok({"count": count, "message": "Todas as conversas marcadas como não lidas."})

    @app.post("/api/contacts/mark-all-read")
    async def mark_all_contacts_read(request: Request):
        """Mark every conversation as read (clear all in-app unread badges)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        def _mark():
            count = contact_repo.mark_all_as_read()
            # Keep already-loaded ContactMemory caches consistent.
            for contact in agent_handler._contacts.values():
                contact.unread_count = 0
                contact.unread_ai_count = 0
            return count
        count = await asyncio.to_thread(_mark)
        return _ok({"count": count, "message": "Todas as conversas marcadas como lidas."})

    @app.post("/api/contacts/{phone}/unread")
    async def mark_contact_unread(phone: str, request: Request):
        """Mark a single conversation as unread (re-light the in-app green badge)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        def _mark():
            contact = agent_handler._get_contact(phone)
            contact.mark_as_unread()
            return contact.unread_count
        unread_count = await asyncio.to_thread(_mark)
        return _ok({"unread_count": unread_count, "message": "Marcado como não lida."})

    @app.get("/api/contacts/{phone}/members")
    async def get_group_members(phone: str, request: Request, force: bool = False):
        """List group participants with resolved names (for @mention autocomplete).

        ``force=true`` bypasses the TTL cache (used after a participant change to
        pick up a just-joined member immediately).
        """
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        if "@g.us" not in phone:
            return _ok({"members": []})
        members = await asyncio.to_thread(
            group_mentions.get_members, phone, force, True)
        return _ok({"members": members})

    @app.post("/api/contacts/{phone}/toggle-ai")
    async def toggle_contact_ai(phone: str, body: dict, request: Request):
        """Enable or disable AI auto-reply for a specific contact."""
        denied = permission_denied(request, "contact.write")
        if denied:
            return denied
        enabled = body.get("enabled")
        if enabled is None:
            return _err("Campo 'enabled' é obrigatório.")
        def _toggle():
            contact = agent_handler._get_contact(phone)
            contact.set_ai_enabled(bool(enabled))
            return contact.id, contact.ai_enabled
        contact_id, result = await asyncio.to_thread(_toggle)
        await ws_manager.broadcast("contact_ai_toggled", {
            "phone": phone,
            "ai_enabled": result,
        })
        await emit_with_filter("contact.ai_toggled", {
            "phone": phone, "ai_enabled": result, "ts": time.time(),
        })
        # Chat notice (plano 12, grupo `ai`): gate nível-contato. Resolve a conversa
        # aberta (fallback: a mais recente) do contato e emite o aviso nela (R20).
        actor = (current_user(request) or {}).get("name") or None
        conv = await asyncio.to_thread(
            system_notices.emit_for_contact,
            event_type="ai_on" if result else "ai_off",
            contact_id=contact_id, phone=phone, actor=actor)
        if conv is not None:
            # P17: the AI gate is per-conversation now — the contact flag above no
            # longer silences the bot. Mirror the toggle onto the contact's conversation
            # (low-level ai_active flip, no (un)assign) so the AI actually pauses/resumes
            # and the chat notice stays truthful. Push conversation_ai_toggled so the
            # sidebar badge flips live.
            await asyncio.to_thread(
                conversation_repo.set_ai_active, conv["id"], 1 if result else 0)
            await ws_manager.broadcast("conversation_ai_toggled", {
                "conversation_id": conv["id"], "contact_id": contact_id,
                "ai_active": 1 if result else 0, "ts": time.time()})
        return _ok({"ai_enabled": result})

    @app.get("/api/contacts/{phone}/avatar")
    async def get_contact_avatar(phone: str, request: Request):
        """Return contact's WhatsApp profile photo (cached on disk)."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        avatars_dir = statics_outbox_dir.parent / "avatars"
        avatars_dir.mkdir(parents=True, exist_ok=True)
        avatar_path = avatars_dir / f"{phone}.jpg"

        if avatar_path.exists():
            return FileResponse(str(avatar_path), media_type="image/jpeg")

        # Fetch from GOWA on-demand
        try:
            data = await asyncio.to_thread(gowa_client.get_avatar, phone)
        except Exception:
            data = None

        if data and isinstance(data, bytes):
            avatar_path.write_bytes(data)
            return FileResponse(str(avatar_path), media_type="image/jpeg")

        return Response(status_code=204)

    @app.put("/api/contacts/{phone}/info")
    async def update_contact_info(phone: str, body: dict, request: Request):
        """Update contact info fields (name, email, profession, company, address,
        observations, plus custom_attributes — plano 05).

        Scalar fields use replace semantics (an empty string clears the field);
        a custom_attribute sent as null is removed. This is an explicit human
        edit, distinct from the LLM auto-fill path (ContactMemory.update_info)."""
        denied = permission_denied(request, "contact.write")
        if denied:
            return denied
        # Validate custom attributes up front so we can return a clean 400 (P50:
        # unknown key → error; invalid value → error) before touching the row.
        custom_attrs = body.get("custom_attributes")
        valid_partial: dict = {}
        if custom_attrs is not None:
            if not isinstance(custom_attrs, dict):
                return _err("custom_attributes deve ser um objeto.")
            defs = await asyncio.to_thread(ca_repo.get_definitions_map, "contact")
            for key, value in custom_attrs.items():
                definition = defs.get(key)
                if definition is None:
                    return _err(f"Atributo '{key}' não existe.", 400)  # P50
                if value is None:
                    valid_partial[key] = None  # explicit clear → set_values pops it
                    continue
                norm, err = validate_value(definition, value)
                if err:
                    return _err(err)
                valid_partial[key] = norm

        result_attrs: dict = {}

        def _update():
            nonlocal result_attrs
            contact = agent_handler._get_contact(phone)
            # Scalar fields: explicit human edit → replace semantics (an empty
            # string clears the field). Only keys actually present in the body
            # are written, so an absent field is left untouched while "" is an
            # intentional clear. Distinct from update_info (LLM merge).
            scalar_keys = ("name", "email", "profession", "company", "address")
            scalar_fields = {k: body[k] for k in scalar_keys if k in body}
            if scalar_fields:
                contact.set_info_fields(scalar_fields)
            # Observations: replace entire list (update_info only appends)
            if "observations" in body:
                new_obs = [
                    o for o in body["observations"] if isinstance(o, str) and o.strip()
                ]
                contact.info["observations"] = new_obs
                contact_repo.set_observations(contact.id, new_obs)
            if custom_attrs is not None:
                result_attrs = ca_repo.set_values(contacts_table, contact.id, valid_partial)
            else:
                result_attrs = ca_repo.get_values(contacts_table, contact.id)
            return contact.info
        info = await asyncio.to_thread(_update)
        # Surface the persisted custom_attributes under `info` so the panel and the
        # resolve guard read a single, reliable source (matches get_full_contact).
        info = {**info, "custom_attributes": result_attrs}
        await emit_with_filter("contact.updated", {
            "phone": phone, "info": info, "custom_attributes": result_attrs, "ts": time.time(),
        })
        return _ok(info)
