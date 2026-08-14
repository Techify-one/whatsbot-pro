"""Contact CRUD and messaging endpoints."""

import asyncio
import csv
import io
import json
import logging
import os
import time
from pathlib import Path

from fastapi import Body, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from gowa.client import GOWASendError

from db.repositories import contact_repo, message_repo, config_repo, conversation_repo, tag_repo
from db.repositories import custom_attribute_repo as ca_repo
from db.repositories import inbox_repo
from db.repositories import mention_repo, inbox_member_repo
from db.repositories import contact_inbox_repo
from db.repositories.custom_attribute_validate import validate_value
from db.tables import contacts as contacts_table
from channels.contact_type import resolve_contact_type
from channels import (audio_transcode, audio_validate, media_limits,
                      video_validate, video_transcode)
from agent import group_mentions
from server import system_notices
from server.authz import (current_user, permission_denied, can_access_inbox,
                          visible_inbox_ids)
from server.avatars import avatar_version, refresh_and_broadcast
from server.helpers import _ok, _err, parse_split_reply
from server.upload_names import unique_media_name
from server.pagination import (CAP_LIST, CAP_MSGS, PAGE_LIST, PAGE_MSGS,
                               clamp_limit, clamp_offset)
from plugins.events import emit as emit_event, apply_filter, emit_with_filter
from server.routes.sandbox import SANDBOX_CONTACT_PREFIX
# ``app.services.messaging_service`` is imported INSIDE ``register_routes`` (not
# at module top) to avoid a latent import cycle: ``server.app`` loads this module,
# and ``messaging_service`` imports back into ``server``. Deferring to call time
# (after ``server.app`` has finished loading) keeps the module importable in any
# order.

logger = logging.getLogger(__name__)

# Contact-scope filter dims (plano 69 F5b). Everything else in the querystring
# (q/archived/sort/limit/offset/include_messages) stays a native route param.
_CONTACT_FILTER_KEYS = ("tag", "labels", "contact_type")


def _contact_filter_where(request: Request):
    """Build the advanced-filter WHERE (plano 69 F5b) from flat query params.

    Pulls ONLY the contact-scope dims (tag/contact_type/cattr:contact:*) out of the
    querystring and compiles them with ``db.filters.build_contact_where`` (contacts.c.*
    scope, allowlisted + bind-param safe). ``None`` when no filter param is present
    (byte-identical to the pre-plano-69 path). Raises ``FilterError`` on a bad
    key/op/value (caller maps to 400)."""
    from db.filters import build_contact_where
    from db.filters.spec import from_params
    from db.filters.translate import FilterContext
    filt = {
        k: v for k, v in dict(request.query_params).items()
        if (k[:-4] if k.endswith("__op") else k) in _CONTACT_FILTER_KEYS
        or (k[:-4] if k.endswith("__op") else k).startswith("cattr:contact:")
    }
    if not filt:
        return None
    spec = from_params(filt)
    ctx = FilterContext(contact_cattr_keys=frozenset(
        d["attribute_key"] for d in ca_repo.list_filterable("contact")))
    return build_contact_where(spec, ctx)


async def _emit_send_error(ws_manager, phone: str, content: str) -> None:
    """Broadcast a ``role:'error'`` message card for a failed send (R3 / B2).

    Single place that builds the error-bubble WS payload the panel renders as a
    centered error card. Plano 23 Fase B3 lifted the implementation into
    ``app.services.messaging_service.error_bubble`` (one source for both the
    operator send routes and the outbound pipeline); this wrapper keeps the
    existing call sites (send text / retry / private-AI) unchanged.
    """
    from app.services.messaging_service import error_bubble
    await error_bubble(ws_manager, phone, content)


def _is_sandbox_contact(phone: str) -> bool:
    """True when the contact is a sandbox/test number — operator sends to it
    must stay local (a real GOWA send would fail: the number isn't on WhatsApp)."""
    return bool(config_repo.get(f"{SANDBOX_CONTACT_PREFIX}{phone}"))


def _apply_window(data: dict, window: dict) -> None:
    """Copia o veredito da janela (plano 99) para o payload da thread.

    ``has_more`` continua sendo o ALIAS de ``has_more_older`` — cliente antigo e
    ``threadData.prependOlder`` leem esse nome, e mantê-lo é o que deixa a
    transição para a janela bidirecional ser aditiva.
    """
    data["has_more"] = window["has_more_older"]
    data["has_more_older"] = window["has_more_older"]
    data["has_more_newer"] = window["has_more_newer"]
    data["anchor_id"] = window["anchor_id"]


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

    # Outbound messaging service (plano 23 Fase B3): the operator media-send tail
    # (R14 — image/audio/document unified) lives in ``MessagingService.send_media``.
    # The AI gate isn't exercised by ``send_media`` but the context requires it;
    # mirror the webhook's master gate so the service is wired identically.
    from app.services.messaging_service import (MessagingContext, MessagingService,
                                                _turn_handed_off)
    # Conversation lifecycle/ownership service (plano 23 Fase B4): the per-contact
    # AI toggle emits its events + system notice + P17 conversation mirror through
    # ``conversation_service.toggle_contact_ai``. Deferred import (cycle avoidance).
    from app.services import conversation_service as conv_svc

    def _channel_ai_enabled(channel_id: str) -> bool:
        if not settings.get("auto_reply", True):
            return False
        from channels import ai_settings as _ais
        return bool(_ais.value(channel_id, "ai_enabled", True))

    messaging = MessagingService(MessagingContext(
        deps=deps, agent_handler=agent_handler, ws_manager=ws_manager,
        state=state, settings=settings, outbound=outbound,
        channel_ai_enabled=_channel_ai_enabled,
    ))

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

    def _wire_target(phone: str, conversation_id=None) -> str:
        """Real send address (the JID the conversation RECEIVES from), not the saved
        ``contacts.phone``.

        ``contacts.phone`` can drift from the WhatsApp account's JID — the classic
        Brazilian 9th-digit case: a number saved with 13 digits whose WhatsApp is
        registered on 12 (or vice-versa). Inbound and the AI auto-reply always use the
        exact JID that came in the received message, so they deliver; a manual/operator
        send that used the raw ``phone`` went to a GHOST JID (GOWA still returns a
        msg_id, but WhatsApp silently drops it → the client never sees it, no delivery
        ack). This resolves the conversation's ``contact_inbox.source_jid`` (the address
        the conversation actually receives from) and returns its bare digits for a
        person (``@s.whatsapp.net``) or the full JID for a group (``@g.us``); anything
        else (lid / Telegram / Cloud / unknown) keeps ``phone`` unchanged, so only the
        exact bug is corrected and other providers are untouched. Falls back to
        ``phone`` on any lookup failure — never raises."""
        if not conversation_id:
            return phone
        try:
            conv = conversation_repo.get(int(conversation_id))
            ci_id = (conv or {}).get("contact_inbox_id")
            if not ci_id:
                return phone
            ci = contact_inbox_repo.get(int(ci_id))
            cand = ((ci or {}).get("source_jid")
                    or (ci or {}).get("source_id") or "").strip()
        except Exception:  # noqa: BLE001 — a resolution glitch must never block a send
            return phone
        if cand.endswith("@g.us"):
            return cand
        if cand.endswith("@s.whatsapp.net"):
            digits = cand.split("@", 1)[0]
            return digits if digits.isdigit() else phone
        return phone

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
        # ``by_human=True``: every caller of this guard is an OPERATOR action from
        # the panel, so a provider that grants humans an extended window
        # (Messenger/Instagram, ``human_window_hours``) is honoured here — while the
        # agentic reply, which never passes through this guard, can't reach it.
        if outbound.session_open(channel_id, last_ts, by_human=True):
            return None
        return _err(
            "Fora da janela de 24h: só é possível enviar um template aprovado.",
            status=409, data={"reason": "session_window_closed"})

    def _media_limits_block(channel_id: str, kind: str, filename: str, size: int):
        """Guard for the media limits the CHANNEL declares (tamanho/formato).

        Returns a 413/415 ``_err`` when the upload cannot be delivered by this
        channel (WhatsApp Cloud: 5 MB JPEG/PNG, 16 MB áudio, 100 MB PDF/DOC/…),
        or ``None`` when it is fine — which is always the case for a channel that
        declares no limits for the kind (GOWA/Telegram). Capability-driven, never
        by provider name; the numbers live in the provider plugin.

        Runs BEFORE the upload is written to disk, so a blocked send leaves no
        orphan file. Video has its own path (validate → transcode → block) because
        it also inspects codecs and may re-encode instead of blocking.
        """
        verdict = media_limits.validate_upload(
            filename, size, outbound.capabilities(channel_id), kind)
        if verdict.ok:
            return None
        status = 413 if verdict.reason == media_limits.TOO_BIG else 415
        return _err(verdict.message, status=status, data={"reason": verdict.reason})

    def _private_ai_conversation_open(channel_id: str, phone: str) -> bool:
        """A conversa aceita que a IA fale com o cliente? (plano 96 I9)

        Só a camada POR-CONVERSA do gate (`ai_active` + dono humano) — ver a nota
        no call site sobre por que o master global não entra aqui. Fail-open igual
        ao gate: erro de leitura nunca cala uma conversa saudável."""
        try:
            from app.services.messaging_service import _conversation_ai_active
            contact = agent_handler._get_contact(phone, channel_id=channel_id)
            return True if contact is None else _conversation_ai_active(contact)
        except Exception:
            logger.exception("[PrivateAI] falha ao reconsultar o gate de %s", phone)
            return True

    def _operator_took_over(channel_id: str, phone: str) -> None:
        """O atendente ENVIOU — interrompe o ciclo da IA em voo (plano 96 I8).

        Enviar é decisão inequívoca (ao contrário de digitar, que só segura): o
        humano assumiu a fala e uma resposta da IA chegando 20s depois duplicaria
        o atendimento. Best-effort e não-destrutivo — ``abort_ai_cycle`` se recusa
        a cancelar durante a fase de envio, para não rasgar um split no meio."""
        try:
            from app.services.messaging_service import abort_ai_cycle
            abort_ai_cycle(deps, channel_id, phone)
        except Exception:
            logger.debug("[Send] falha ao abortar o ciclo da IA de %s/%s",
                         channel_id, phone)

    async def _send_read_receipts(phone: str, msg_ids: list[str], channel_id: str = "default"):
        """Send read receipts via the conversation's channel (best-effort, plano 38 F3).

        Routed through ``outbound.mark_read`` (capability/registry) instead of a
        hardcoded ``gowa_client.mark_as_read`` so a Telegram/Cloud conversa não
        recebe um receipt GOWA para um id que o GOWA não resolve. ``mark_read`` já
        no-op sem canal vivo."""
        for mid in msg_ids:
            # Notas privadas notificadas carregam um msg_id sintético ("pn:…") que não
            # existe no provedor — nunca mandar read-receipt dele.
            if str(mid).startswith("pn:"):
                continue
            try:
                await asyncio.to_thread(outbound.mark_read, channel_id, phone, mid)
                logger.info("[ReadReceipt] Sent for %s msg %s (channel=%s)", phone, mid, channel_id)
            except Exception as e:
                logger.warning("[ReadReceipt] Failed for %s msg %s: %s", phone, mid, e)

    @app.get("/api/contacts")
    async def list_contacts(request: Request, q: str = "", archived: bool = False,
                            limit: int | None = None, offset: int = 0,
                            sort: str = "recency", include_messages: bool = True):
        """List all contacts with summary info.

        Paginação (plano 50 F5): quando ``limit`` é informado, devolve o envelope
        ``{items, total, has_more}`` (cap ``CAP_LIST``). SEM ``limit`` mantém o shape
        legado (``data`` = lista). ``sort`` (F7): ``name`` = alfabético (tela /contacts),
        default ``recency`` (sidebar). ``offset`` ≥ 0.

        ``include_messages`` (plano 62 F5, default ``true`` por compat): com
        ``false`` a busca ``q`` não olha o conteúdo das mensagens (só nome/
        telefone/grupo/tag) e as linhas não ganham ``match_snippet`` — mais barato
        para telas que não renderizam o trecho casado."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        sort = "name" if sort == "name" else "recency"
        # Filtro avançado server-side (plano 69 F5b): tag/contact_type/cattr:contact:*
        # entram no MESMO WHERE da lista e do COUNT, então a lista bate com o total.
        from db.filters import FilterError
        try:
            filter_where = _contact_filter_where(request)
        except FilterError as e:
            return _err(str(e), status=400)
        except (TypeError, ValueError, KeyError, IndexError) as e:
            logger.warning("Filtro de contatos inválido: %s", e)
            return _err("Filtro inválido.", status=400)
        # Inbox-membership scoping (plano inboxes/canais §4.7): a sidebar
        # conversa-cêntrica carrega esta lista; sem o filtro ela vazava contatos de
        # caixas que o usuário não acessa. Espelha GET /api/conversations.
        inbox_ids = visible_inbox_ids(request)
        if limit is None:
            # Caminho legado: lista completa (retrocompatível).
            results = await asyncio.to_thread(
                contact_repo.list_contacts, q, archived, inbox_ids,
                include_messages=include_messages, filter_where=filter_where)
            for c in results:
                c["avatar_v"] = avatar_version(settings, c.get("phone", ""))
            return _ok(results)
        # Caminho paginado: envelope {items, total, has_more}.
        lim = clamp_limit(limit, PAGE_LIST, CAP_LIST)
        off = clamp_offset(offset)
        page = await asyncio.to_thread(
            contact_repo.list_contacts_page, q, archived, inbox_ids,
            limit=lim, offset=off, sort=sort, include_messages=include_messages,
            filter_where=filter_where)
        # Cache-busting version for each avatar (file mtime) so updated photos
        # are picked up by the browser instead of the stale cached image.
        for c in page["items"]:
            c["avatar_v"] = avatar_version(settings, c.get("phone", ""))
        return _ok(page)

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
            ctype = resolve_contact_type(channel_id)
            def _save():
                contact_repo.get_or_create(canonical, default_ai_enabled=ai_default,
                                           contact_type=ctype)
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
        denied = permission_denied(request, "conversation.read")
        if denied:
            return denied
        count = await asyncio.to_thread(
            contact_repo.unread_conversation_count, visible_inbox_ids(request))
        return _ok({"count": count})

    @app.get("/api/contacts/export")
    async def export_contacts(request: Request):
        """Download all contacts as a CSV file (Chatwoot-style export).

        Declared before /api/contacts/{phone} so the static path wins over the
        path parameter. Scoped by inbox membership, same as the list endpoint."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        inbox_ids = visible_inbox_ids(request)
        # Custom attribute definitions (plano 05) become extra CSV columns,
        # dynamically — a newly created attribute shows up here automatically.
        attr_defs = await asyncio.to_thread(ca_repo.list_definitions, "contact")
        # Email/Profissão/Empresa/Endereço are now custom attributes too, but stay
        # as fixed CSV columns (sourced from the JSON) for a stable, familiar
        # format — so they're excluded from the dynamic-attribute columns to avoid
        # duplicate headers.
        # "type" is a fixed column too (o tipo do contato) — exclude any custom
        # attribute with that key so the export never emits a duplicate header.
        _CORE_ATTR_KEYS = {"email", "profession", "company", "address", "type"}
        extra_defs = [d for d in attr_defs if d["attribute_key"] not in _CORE_ATTR_KEYS]

        def _format_line(cells: list) -> str:
            buf = io.StringIO()
            csv.writer(buf).writerow(cells)
            return buf.getvalue()

        def _rows():
            # Streaming (plano 50 F11): BOM + header, depois cada contato formatado em
            # chunks pelo gerador (memória constante, sem N+1 de tags). Gerador SÍNCRONO
            # → Starlette o itera num threadpool, então as queries de DB não bloqueiam.
            header = ["phone", "name", "email", "profession", "company",
                      "address", "ai_enabled", "tags", "type"]
            header.extend(d["attribute_key"] for d in extra_defs)
            yield "﻿" + _format_line(header)   # BOM p/ o Excel abrir UTF-8
            for r in contact_repo.iter_for_export(inbox_ids):
                custom = r.get("custom_attributes") or {}
                row_out = [
                    r["phone"], r["name"],
                    _format_attr_cell(custom.get("email")),
                    _format_attr_cell(custom.get("profession")),
                    _format_attr_cell(custom.get("company")),
                    _format_attr_cell(custom.get("address")),
                    "1" if r["ai_enabled"] else "0",
                    ", ".join(r["tags"]),
                    # Tipo do contato herdado do canal de origem (whatsapp/telegram/outros).
                    r.get("contact_type") or "outros",
                ]
                for d in extra_defs:
                    row_out.append(_format_attr_cell(custom.get(d["attribute_key"])))
                yield _format_line(row_out)

        return StreamingResponse(
            _rows(),
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
        denied = permission_denied(request, "contact.import")
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
        # Import CSV é por telefone (WhatsApp) — sem canal explícito, herda o tipo
        # do canal `default` (GOWA). Resolvido uma vez (não por linha).
        import_ctype = resolve_contact_type(None)

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
                    phone, default_ai_enabled=ai_default, contact_type=import_ctype)
                cid = contact["id"]

                # Name + ai_enabled stay as real contact columns.
                fields = {}
                name_val = _cell(row, "name")
                if name_val:
                    fields["name"] = name_val
                ai_raw = _cell(row, "ai_enabled").lower()
                if ai_raw:
                    fields["ai_enabled"] = 0 if ai_raw in ("0", "false", "nao",
                                                           "não", "no", "off") else 1
                if fields:
                    contact_repo.update(cid, **fields)

                # Email/Profissão/Empresa/Endereço are now custom attributes — import
                # their cells (PT-BR aliases honoured by _IMPORT_COLUMN_ALIASES) into
                # custom_attributes, validated against the seeded definitions.
                core_partial = {}
                for f in ("email", "profession", "company", "address"):
                    val = _cell(row, f)
                    if not val:
                        continue
                    d = attr_defs_map.get(f)
                    if d is None:
                        continue
                    norm, err = validate_value(d, val)
                    if err or norm is None:
                        continue
                    core_partial[f] = norm
                if core_partial:
                    ca_repo.set_values(contacts_table, cid, core_partial)

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
                          channel_id: str = "", limit: int = PAGE_MSGS,
                          before_id: int | None = None,
                          after_id: int | None = None,
                          around_id: int | None = None,
                          at_ts: float | None = None):
        """Return full contact data including conversation history.

        Quando ``channel_id`` é informado (multicanal — abrir uma conversa NOVA pela
        caixa de entrada escolhida, antes de existir uma conversa nessa caixa), o
        thread é escopado ao canal: carrega só as mensagens da conversa daquele canal
        (vazio se ainda não houver). Sem ``channel_id`` o comportamento legado é
        mantido (funde as mensagens de todos os canais do mesmo número). NUNCA cair na
        conversa de OUTRO canal — caixas de entrada não se confundem.

        Paginação keyset (plano 50 F3): ``limit`` (cap ``CAP_MSGS``) + ``before_id``
        — mesma semântica de ``/api/atendimentos/{id}/messages``; devolve a página mais
        recente e ``has_more``. Vale para os dois ramos (multicanal e legado all-channels).

        Janela ANCORADA (plano 99 F0c): ``after_id`` / ``around_id`` / ``at_ts``, com a
        MESMA semântica da view conversa-cêntrica (a regra é uma só, em
        ``message_repo.read_window``) — mutuamente exclusivos com ``before_id``, e
        nenhum deles marca a conversa como lida.
        """
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        anchors = [p for p in (before_id, after_id, around_id, at_ts) if p is not None]
        if len(anchors) > 1:
            return _err("Use apenas uma âncora: before_id, after_id, around_id ou at_ts.",
                        status=400)
        if after_id is not None or around_id is not None or at_ts is not None:
            mark_read = False
        page_limit = clamp_limit(limit, PAGE_MSGS, CAP_MSGS)
        vis = visible_inbox_ids(request)
        channel = (channel_id or "").strip()
        def _load():
            data = contact_repo.get_full_contact(phone)
            if data is None:
                # Auto-create contact for verified phone numbers
                ai_default = settings.get("default_ai_enabled", True)
                contact_repo.get_or_create(phone, default_ai_enabled=ai_default,
                                           contact_type=resolve_contact_type(channel))
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
                if scoped_conv:
                    _page, _win = message_repo.read_window(
                        page_limit, before_id=before_id, after_id=after_id,
                        around_id=around_id, at_ts=at_ts,
                        conversation_id=scoped_conv["id"])
                    data["messages"] = _page
                    _apply_window(data, _win)
                else:
                    data["messages"] = []
                    _apply_window(data, {"has_more_older": False,
                                         "has_more_newer": False, "anchor_id": None})
                # Compositor hints (Frente C / plano 21): mesmo SEM conversa ainda, o
                # canal escolhido define se aceita template e se a janela de texto livre
                # está aberta. Sem isso, abrir um canal Cloud (windowed) sem conversa não
                # mostra o botão de template — e Cloud exige template fora da janela 24h.
                last_ts = (message_repo.last_inbound_ts(
                    conversation_id=scoped_conv["id"]) if scoped_conv else None)
                data["templates_supported"] = outbound.supports(channel, "templates")
                data["session_open"] = outbound.session_open(channel, last_ts)
                # Capability hints p/ o menu de contexto da mensagem: esconder
                # "Apagar" onde o canal não revoga (Cloud), mostrar "Editar" só onde
                # o canal edita. Dirigido por CAPABILITY, nunca por nome de provider.
                data["revoke_supported"] = outbound.supports(channel, "revoke")
                data["edit_supported"] = outbound.supports(channel, "edit_message")
                # Limites de mídia DECLARADOS pelo canal (o provider é dono dos
                # números): o compositor bloqueia o anexo fora do padrão com um
                # popup em vez de deixar o envio falhar no provider.
                data["media_limits"] = media_limits.describe(
                    outbound.capabilities(channel),
                    video_transcode_available=video_transcode.available(),
                    audio_transcode_available=audio_transcode.available())
            else:
                _page, _win = message_repo.read_window(
                    page_limit, before_id=before_id, after_id=after_id,
                    around_id=around_id, at_ts=at_ts, contact_id=contact_id)
                data["messages"] = _page
                _apply_window(data, _win)
            data["marked_read"] = bool(mark_read)
            # Load usage for the full response
            data["usage"] = []
            return data, msg_ids
        data, msg_ids = await asyncio.to_thread(_load)
        if data is None or data == "__hidden__":
            return _err("Contato não encontrado.", status=404)
        if msg_ids:
            # plano 38 F3: route the receipt through the viewed conversation's channel
            # (falls back to 'default'/GOWA on the legacy all-channels view).
            asyncio.create_task(_send_read_receipts(
                phone, msg_ids, data.get("channel_id") or "default"))
        # Check group send permissions (fresh check on every contact load). plano 38 F4:
        # only for channels whose provider supports groups (GOWA) — a Telegram/Cloud
        # group must not fire the GOWA-specific can_bot_send_in_group.
        group_channel = data.get("channel_id") or "default"
        if data.get("is_group") and state.bot_phone and outbound.supports(group_channel, "groups"):
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
        # plano 38 F5: refresh via the viewed conversation's channel (default/GOWA on
        # the legacy all-channels view). A Telegram/Cloud-only contact won't hit GOWA.
        asyncio.create_task(refresh_and_broadcast(deps, phone, data.get("channel_id") or "default"))
        return _ok(data)

    @app.delete("/api/contacts/{phone}")
    async def delete_contact(phone: str, request: Request):
        """Permanently delete a contact and all associated data."""
        denied = permission_denied(request, "contact.delete")
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
        # Operador logado (para exibir o nome no balão em vez de "Manual"). None em
        # instalação legada/aberta → cai em "Manual".
        _u = current_user(request)
        _uid = _u.get("id") if _u else None
        _uname = _u.get("name") if _u else None

        # Plugin filter: allow plugins to add signature/formatting/redact to operator sends
        filtered = await apply_filter(
            "filter.reply.part", message,
            {"phone": phone, "index": 0, "total": 1, "source": "operator", "sent_by_name": _uname},
        )
        if filtered is None:
            return _err("Mensagem bloqueada por plugin.", status=400)
        message = filtered

        # Regra "ignorar abertura" (plugin): um filtro pode impedir que este envio do
        # operador REABRA uma conversa fechada (mantém fechada quando o texto casa a
        # regex). Sem plugin registrado, apply_filter devolve True → reopen=None (default).
        _allow_reopen = await apply_filter(
            "filter.conversation.before_reopen", True,
            {"phone": phone, "role": "assistant", "text": message})
        _reopen = False if not _allow_reopen else None

        # Sandbox/test contact — never goes over GOWA (the number isn't real).
        # Persist the operator message locally and broadcast it, no error.
        if await asyncio.to_thread(_is_sandbox_contact, phone):
            msg_data = await asyncio.to_thread(
                agent_handler.save_operator_message, phone, message, status="operator",
                reply_to_msg_id=reply_to,
                sent_by_user_id=_uid, sent_by_name=_uname, reopen=_reopen,
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
        _operator_took_over(channel_id, phone)
        # Wire target = the JID the conversation actually receives from (fixes the BR
        # 9th-digit ghost-send). `phone` stays the contact key for save/broadcast.
        wire_phone = await asyncio.to_thread(
            _wire_target, phone, body.get("conversation_id"))
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

        # Plugin filter: WIRE-ONLY transform (e.g. signature) — reaches the contact
        # but NOT the saved/broadcast copy (which keeps using `message`).
        _wired = await apply_filter(
            "filter.outbound.text", send_text,
            {"phone": phone, "channel_id": channel_id, "source": "operator",
             "sent_by_name": _uname, "index": 0, "total": 1},
        )
        if _wired is not None:
            send_text = _wired

        # Track sent message to filter echo-backs — key on the WIRE target (the echo
        # comes back stamped with the real JID, not the saved phone).
        state.recently_sent[f"{channel_id}:{wire_phone}:{send_text[:120]}"] = time.time()

        # Send via the conversation's channel — always save message (status on failure)
        send_failed = False
        error_msg = ""
        msg_id = None
        try:
            msg_id = await asyncio.to_thread(
                _route_send_text, channel_id, wire_phone, send_text, mentions, reply_to)
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
                sent_by_user_id=_uid, sent_by_name=_uname, reopen=_reopen,
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

    @app.post("/api/contacts/{phone}/messages/edit")
    async def edit_message(phone: str, body: dict, request: Request):
        """Edit the text of an already-sent OUTGOING message (operator or AI).

        Identifies the message by its provider ``msg_id`` (required — editing happens
        on the provider). Only text messages sent by us can be edited: inbound
        (``role='user'``) and media messages are refused. The edit is pushed to the
        conversation's channel via the capability-gated ``outbound.edit_text``; on
        success the DB content is updated + ``edited_ts`` stamped, and the panel is
        notified via the ``message_edited`` WS event. Providers that can't edit
        (capability off) yield a clean error instead of a silent no-op.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        msg_id = (body.get("msg_id") or "").strip()
        text = (body.get("text") or "").strip()
        if not msg_id:
            return _err("Editar exige uma mensagem já enviada.", status=400)
        if not text:
            return _err("O texto da mensagem não pode ficar vazio.", status=400)

        denied_inbox = await _inbox_send_denied(
            request, conversation_id=body.get("conversation_id"))
        if denied_inbox:
            return denied_inbox

        msg = await asyncio.to_thread(message_repo.get_by_msg_id, msg_id)
        if not msg:
            return _err("Mensagem não encontrada.", status=404)
        if msg.get("role") == "user":
            return _err("Só é possível editar as suas próprias mensagens.", status=400)
        if msg.get("media_type"):
            return _err("Só é possível editar mensagens de texto.", status=400)

        is_sandbox = await asyncio.to_thread(_is_sandbox_contact, phone)
        if not is_sandbox:
            channel_id = _channel_for(phone, body.get("conversation_id"))
            res = await asyncio.to_thread(outbound.edit_text, channel_id, phone, msg_id, text)
            if not res.ok:
                return _err(f"Não foi possível editar a mensagem: {res.error}", status=400)

        db_id = msg.get("_id") or body.get("db_id")
        edited_ts = await asyncio.to_thread(message_repo.mark_edited, int(db_id), text) if db_id else None
        await ws_manager.broadcast("message_edited", {
            "phone": phone, "msg_id": msg_id, "db_id": db_id,
            "content": text, "edited_ts": edited_ts,
            "conversation_id": body.get("conversation_id"),
        })
        await emit_with_filter("message.edited", {
            "id": msg_id, "phone": phone, "original_message_id": msg_id,
            "body": text, "ts": time.time(),
        })
        logger.info("[Edit] Edited msg %s for %s", msg_id, phone)
        return _ok({"message": "Mensagem editada.", "msg_id": msg_id, "edited_ts": edited_ts})

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

    async def _run_private_ai(phone: str, text: str, reply_in_chat: bool = True,
                              conversation_id=None, abort_epoch: int | None = None):
        """Process a private message via the LLM.

        Triggered by the operator's "IA lê" toggle on the private-message panel.
        Tool calls run normally and their cards are broadcast to the panel.

        If `reply_in_chat` is True, the LLM reply is sent to the contact as a
        regular assistant message. If False, each reply part is saved as a
        private note (stays only in the panel).
        """
        # plano 37 (B1 — a conversa #41): resolve o canal de ORIGEM uma vez e use-o
        # em TODO o fluxo (contexto lido, cards de tool, resposta salva), senão a IA
        # privada iniciada num canal não-default (ex.: Telegram) misfila pro
        # WhatsApp 'default'.
        run_channel = _channel_for(phone, conversation_id)
        # Private-AI tasks are fire-and-forget and do not live in processing_tasks.
        # The same abort generation still scopes them: a manual send/assignment that
        # happens while their LLM is running invalidates this specific reply without
        # permanently disabling the AI for the next customer turn.
        private_abort_epoch = (messaging._abort_epoch(run_channel, phone)
                               if abort_epoch is None else abort_epoch)
        run_wire = await asyncio.to_thread(_wire_target, phone, conversation_id)

        async def _may_reply_in_chat_now(allow_self_handoff: bool = False) -> bool:
            """Espelha ``MessagingService._cycle_may_continue`` (plano 122).

            Época PRIMEIRO — o perdão jamais a alcança. Ele só existe para o turno
            que chamou ``transfer_to_human`` e portanto fechou o próprio gate."""
            if messaging._abort_epoch(run_channel, phone) != private_abort_epoch:
                return False
            if allow_self_handoff:
                return True
            return await asyncio.to_thread(
                _private_ai_conversation_open, run_channel, phone)

        async def _blocked_notice() -> None:
            aviso = ("⚠️ A resposta da IA ao cliente foi interrompida porque um "
                     "atendente assumiu a conversa. Desmarque \"responder no chat\" "
                     "para receber a resposta como nota privada, ou devolva a "
                     "conversa à IA.")
            try:
                def _save_aviso():
                    c = agent_handler._get_contact(phone, channel_id=run_channel)
                    return c.add_message("system_notice", aviso)
                saved = await asyncio.to_thread(_save_aviso)
                await ws_manager.broadcast("new_message", {
                    "phone": phone, "channel_id": run_channel,
                    "message": saved or {"role": "system_notice", "content": aviso,
                                         "ts": time.time()},
                })
            except Exception:
                logger.exception("[PrivateAI] falha ao gravar o card de bloqueio de %s",
                                 phone)

        try:
            result = await agent_handler.aprocess_message(
                phone, text,
                save_user_message=False,
                save_response=False,
                channel_id=run_channel,
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
                    phone, result.tool_calls, result.contact_info,
                    channel_id=run_channel, agent_key=result.agent_key)
            except Exception as e:
                logger.warning("[PrivateAI] broadcast_tool_calls failed for %s: %s",
                               phone, e)

        if not reply_text:
            return

        # Plano 96 I9/P1 — a IA da nota privada era o ÚNICO caminho de saída sem
        # gate nenhum (3 dos 22 incidentes medidos): com "IA lê" + "responder no
        # chat" ligados, ela mandava ao cliente mesmo numa conversa já assumida por
        # um humano. Pior: o toggle só se reseta ao TROCAR de conversa, então quem
        # o liga para instruir a IA segue com ele ligado nas notas seguintes.
        # Gate só no caminho que FALA com o cliente — ``reply_in_chat=False`` vira
        # nota privada e nunca sai do painel, então não é gateado.
        #
        # ⚠️ Aqui é o gate POR-CONVERSA (`_conversation_ai_active`), não o veredito
        # composto `ai_may_speak`: o master global `auto_reply` (default OFF numa
        # instalação nova) e o `ai_enabled` do canal governam a IA responder SOZINHA
        # a um inbound. Esta resposta foi PEDIDA por um humano — barrá-la pelo
        # interruptor de automação apagaria o recurso inteiro em instalação com a
        # automação desligada, que não é o problema que o plano 96 ataca (D1: o que
        # cala é o HUMANO no comando daquela conversa).
        #
        # Plano 122 — o perdão do turno que transferiu. Sem ele, uma IA privada com
        # "responder no chat" que chame ``transfer_to_human`` cai aqui e grava um
        # card FALSO ("um atendente assumiu a conversa" — ninguém assumiu).
        handed_off = _turn_handed_off(result.tool_calls)
        if reply_in_chat and not await _may_reply_in_chat_now(handed_off):
            logger.info("[PrivateAI] resposta não enviada a %s — a conversa está "
                        "com um atendente humano", phone)
            await _blocked_notice()
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
                # AI reply stays in the panel as a private note. Bind the note to
                # the SAME channel as the conversation (senão cairia no inbox
                # 'default' e não roteria no painel — plano 11) and use the row
                # add_message RETURNS (id/ts/conversation_id) instead of a racy
                # get_last.
                note_channel = run_channel
                saved_note = None
                try:
                    def _save_note(p=part):
                        contact = agent_handler._get_contact(phone, channel_id=note_channel)
                        return contact.add_message("private_note", p)
                    saved_note = await asyncio.to_thread(_save_note)
                except Exception as e:
                    logger.error("[PrivateAI] failed to save private note: %s", e)
                note_msg = {
                    "role": "private_note",
                    "content": part,
                    "ts": (saved_note or {}).get("ts", time.time()),
                    "status": None,
                    "conversation_id": (saved_note or {}).get("conversation_id"),
                }
                if saved_note and saved_note.get("id"):
                    note_msg["_id"] = saved_note["id"]
                if saved_note and saved_note.get("msg_id"):
                    note_msg["msg_id"] = saved_note["msg_id"]
                await ws_manager.broadcast(
                    "new_message",
                    {"phone": phone, "channel_id": note_channel, "message": note_msg})
                continue

            channel_id = run_channel
            # WIRE-ONLY transform (e.g. signature): reaches the contact but not the
            # saved copy (save below keeps using `part`).
            wire_text = part
            _wired = await apply_filter(
                "filter.outbound.text", part,
                {"phone": phone, "channel_id": channel_id, "source": "private_ai",
                 "index": i, "total": len(parts)},
            )
            if _wired is not None:
                wire_text = _wired
            # Same last-moment rule as the normal AI pipeline. The initial check
            # above is not enough: plugins and a multi-part response create another
            # window in which the operator can take over.
            if not await _may_reply_in_chat_now(handed_off):
                logger.info("[PrivateAI] split de %s/%s interrompido na parte %d/%d",
                            run_channel, phone, i + 1, len(parts))
                await _blocked_notice()
                return
            state.recently_sent[f"{channel_id}:{run_wire}:{wire_text[:120]}"] = time.time()
            send_failed = False
            send_error = ""
            msg_id = None
            try:
                msg_id = await asyncio.to_thread(
                    _route_send_text, channel_id, run_wire, wire_text)
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
                    channel_id=run_channel, agent_key=result.agent_key,
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

    def _parse_mentions_field(raw: str) -> list:
        """Decodifica o campo multipart ``mentions`` (JSON de user_ids). Silencioso."""
        if not raw:
            return []
        try:
            val = json.loads(raw)
            return val if isinstance(val, list) else []
        except (ValueError, TypeError):
            return []

    def _parse_mention_targets(raw_mentions, mention_inbox, inbox_id) -> list[int]:
        """Junta os user_ids citados explicitamente com os membros da caixa (quando
        ``mention_inbox``). "Time" = membros da inbox da conversa (decisão de projeto).
        Robusto a tipos (str/int); silencioso em erro."""
        targets: list[int] = []
        for m in (raw_mentions or []):
            try:
                targets.append(int(m))
            except (TypeError, ValueError):
                continue
        if mention_inbox and inbox_id is not None:
            try:
                targets.extend(inbox_member_repo.member_ids(int(inbox_id)))
            except Exception:
                logger.debug("mention: falha ao expandir membros da caixa %s", inbox_id)
        return targets

    async def _record_private_mentions(*, saved: dict, phone: str, contact_id: int,
                                        channel_id: str, actor: dict | None,
                                        raw_mentions, mention_inbox: bool,
                                        preview: str) -> None:
        """Grava as menções de uma nota privada e emite ``mention_created`` (in-app).

        Defensivo: uma falha aqui NUNCA quebra o save da nota."""
        try:
            conv_id = (saved or {}).get("conversation_id")
            # add_message devolve `id`; message_repo.get_last devolve `_id`.
            msg_id = (saved or {}).get("id") or (saved or {}).get("_id")
            if not conv_id or not msg_id:
                return
            inbox_id = await asyncio.to_thread(
                _resolve_inbox_id, conv_id, channel_id)
            actor_uid = (actor or {}).get("id")
            actor_name = (actor or {}).get("name")
            targets = _parse_mention_targets(raw_mentions, mention_inbox, inbox_id)
            n = await asyncio.to_thread(
                mention_repo.add_many,
                message_id=int(msg_id), conversation_id=int(conv_id),
                contact_id=int(contact_id), mentioned_user_ids=targets,
                actor_user_id=actor_uid, actor_name=actor_name)
            if not n:
                return
            recipients = [uid for uid in dict.fromkeys(targets)
                          if uid and uid != actor_uid]
            await ws_manager.broadcast("mention_created", {
                "conversation_id": int(conv_id),
                "contact_id": int(contact_id),
                "phone": phone,
                "channel_id": channel_id,
                "inbox_id": inbox_id,
                "mentioned_user_ids": recipients,
                "actor_name": actor_name,
                "preview": (preview or "")[:120],
            })
        except Exception:
            logger.exception("Falha ao registrar menções da nota privada para %s", phone)

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
        _u = current_user(request)

        # Bind the note to the conversation's channel (senão cai no inbox 'default'
        # e não roteia no painel — plano 11).
        note_channel = _channel_for(phone, body.get("conversation_id"), body.get("channel_id"))
        try:
            def _save():
                contact = agent_handler._get_contact(phone, channel_id=note_channel)
                saved_row = contact.add_message(
                    "private_note", text,
                    sent_by_user_id=(_u.get("id") if _u else None),
                    sent_by_name=(_u.get("name") if _u else None))
                return contact.id, saved_row
            contact_id, saved = await asyncio.to_thread(_save)
        except Exception as e:
            logger.error("[Private] Failed to save private message for %s: %s", phone, e)
            return _err(f"Erro ao salvar mensagem privada: {e}", status=500)

        # Menções (@atendente / @time) — colaboração estilo Chatwoot. Grava as linhas
        # em `mentions` e emite `mention_created` (toast/badge/aba). Best-effort.
        await _record_private_mentions(
            saved=saved, phone=phone, contact_id=contact_id, channel_id=note_channel,
            actor=_u, raw_mentions=body.get("mentions"),
            mention_inbox=bool(body.get("mention_inbox", False)), preview=text)

        # Carry the DB row id (delete without reload) + conversation_id/channel_id
        # so the panel routes the note to the right thread (plano 11). Uses the row
        # add_message returned — not a racy get_last. `_id` (+ the synthetic
        # "pn:…" msg_id when notify_private_messages is on) is the stable identity
        # the frontend dedups by (plano 53) — clock-skew immune.
        note_msg = {
            "role": "private_note",
            "content": text,
            "ts": (saved or {}).get("ts", time.time()),
            "status": None,
            "conversation_id": (saved or {}).get("conversation_id"),
        }
        if _u and _u.get("name"):
            note_msg["sent_by_name"] = _u.get("name")
        if saved and saved.get("id"):
            note_msg["_id"] = saved["id"]
        if saved and saved.get("msg_id"):
            note_msg["msg_id"] = saved["msg_id"]
        await ws_manager.broadcast(
            "new_message",
            {"phone": phone, "channel_id": note_channel, "message": note_msg})

        if ai_read:
            private_epoch = messaging._abort_epoch(note_channel, phone)
            asyncio.create_task(_run_private_ai(
                phone, text, reply_in_chat=ai_reply,
                conversation_id=body.get("conversation_id"),
                abort_epoch=private_epoch))

        logger.info("[Private] Saved private note for %s (ai_read=%s, ai_reply=%s): %s",
                    phone, ai_read, ai_reply, text[:80])
        return _ok(note_msg)

    @app.post("/api/contacts/{phone}/private-audio")
    async def send_private_audio(
        phone: str,
        request: Request,
        audio: UploadFile = File(...),
        ai_read: str = Form("false"),
        ai_reply: str = Form("true"),
        conversation_id: str = Form(""),
        channel_id: str = Form(""),
        mentions: str = Form(""),
        mention_inbox: str = Form("false"),
    ):
        """Record an audio note that stays in the panel — never sent to the contact.

        Mirrors ``/private-message`` but for audio. The clip is stored as a
        panel-only ``private_note`` card (audio player).

        The visible "Transcrição privada" card follows the channel's audio setting
        (multi-select "Privadas") REGARDLESS of ``ai_read`` — unchecked → no card.

        - ``ai_read=true``  → the AI reads the audio and runs the private-AI flow
          (transcribing internally when the channel didn't, without showing a
          card). ``ai_reply`` decides chat reply vs. private note.
        - ``ai_read=false`` → no AI action; only the channel-gated card (if any).
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        denied_inbox = await _inbox_send_denied(
            request, conversation_id=conversation_id, channel_id=channel_id)
        if denied_inbox:
            return denied_inbox

        ai_read_b = str(ai_read).lower() in ("1", "true", "yes", "on")
        ai_reply_b = str(ai_reply).lower() in ("1", "true", "yes", "on")
        resolved_channel = _channel_for(phone, conversation_id, channel_id)

        # Persist the clip locally (never hits GOWA — private notes are panel-only).
        dest = statics_outbox_dir / unique_media_name(
            audio.content_type, audio.filename, default_ext=".ogg")
        content = await audio.read()
        dest.write_bytes(content)
        rel_path = f"statics/outbox/{dest.name}"

        # Transcribe up front. ``card_text`` honors the channel setting ("Privadas"
        # in the multi-select) — source="private", NO force — and drives the visible
        # "Transcrição privada" card. ``ai_text`` is what the AI reads: "IA lê" forces
        # a transcription even when the channel wouldn't surface a card.
        card_text = ""
        try:
            card_text = await messaging.maybe_transcribe(
                "audio", str(dest),
                phone=phone, source="private",
                channel_id=resolved_channel)
        except Exception as e:
            logger.error("[Private] Audio transcription failed for %s: %s", phone, e)
        ai_text = ""
        if ai_read_b:
            ai_text = card_text
            if not ai_text:
                try:
                    ai_text = await messaging.maybe_transcribe(
                        "audio", str(dest),
                        phone=phone, source="private",
                        channel_id=resolved_channel, force=True)
                except Exception as e:
                    logger.error("[Private] Forced transcription failed for %s: %s", phone, e)
                    ai_text = ""

        # Persist the note with the TRANSCRIPTION as content (fallback "[Áudio]") so
        # the AI reads the real instruction via the private-note context path —
        # message_repo injects it as "[Nota privada do operador]: <content>", the
        # exact route a typed private note takes (run_turn drops the raw text when
        # save_user_message=False). The card still renders the audio player
        # (media_type=audio, so the transcription text isn't shown in that bubble).
        note_text = ai_text or card_text
        db_content = note_text or "[Áudio]"
        _u = current_user(request)
        try:
            def _save():
                contact = agent_handler._get_contact(phone, channel_id=resolved_channel)
                # Use the row add_message RETURNS (id/ts/conversation_id/msg_id)
                # instead of a racy get_last — same fix /private-message got.
                saved_row = contact.add_message(
                    "private_note", db_content,
                    media_type="audio", media_path=rel_path,
                    sent_by_user_id=(_u.get("id") if _u else None),
                    sent_by_name=(_u.get("name") if _u else None))
                return contact.id, saved_row
            contact_id, saved = await asyncio.to_thread(_save)
        except Exception as e:
            logger.error("[Private] Failed to save private audio for %s: %s", phone, e)
            return _err(f"Erro ao salvar áudio privado: {e}", status=500)

        await _record_private_mentions(
            saved=saved, phone=phone, contact_id=contact_id, channel_id=resolved_channel,
            actor=_u, raw_mentions=_parse_mentions_field(mentions),
            mention_inbox=str(mention_inbox).lower() in ("1", "true", "yes", "on"),
            preview=note_text or "[Áudio]")

        # Broadcast/return "[Áudio]" (not the transcription) so the operator's
        # optimistic bubble dedups cleanly; the player renders from media_path.
        # Full identity contract (plano 53): _id + msg_id + sent_by_name.
        note_msg = {
            "role": "private_note",
            "content": "[Áudio]",
            "ts": (saved or {}).get("ts", time.time()),
            "media_type": "audio",
            "media_path": rel_path,
            "status": None,
            "conversation_id": (saved or {}).get("conversation_id"),
        }
        if _u and _u.get("name"):
            note_msg["sent_by_name"] = _u.get("name")
        if saved and saved.get("id"):
            note_msg["_id"] = saved["id"]
        if saved and saved.get("msg_id"):
            note_msg["msg_id"] = saved["msg_id"]
        await ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": resolved_channel, "message": note_msg})

        # Visible "Transcrição privada" card — only when the channel opted in.
        if card_text:
            try:
                await asyncio.to_thread(
                    lambda: agent_handler._get_contact(
                        phone, channel_id=resolved_channel).add_message(
                        "transcription", card_text))
            except Exception as e:
                logger.error("[Private] Failed to save transcription for %s: %s", phone, e)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "channel_id": resolved_channel,
                "message": {
                    "role": "transcription",
                    "content": card_text,
                    "ts": time.time(),
                },
            })

        # "IA lê": run the private-AI flow. The instruction is already in the note
        # context (db_content), so the turn acts on it exactly like a typed note.
        if ai_read_b:
            if ai_text:
                private_epoch = messaging._abort_epoch(resolved_channel, phone)
                asyncio.create_task(_run_private_ai(
                    phone, ai_text, reply_in_chat=ai_reply_b,
                    conversation_id=conversation_id or None,
                    abort_epoch=private_epoch))
            else:
                await _emit_send_error(
                    ws_manager, phone,
                    "Não foi possível transcrever o áudio para a IA processar.")

        logger.info("[Private] Saved private audio for %s (ai_read=%s, ai_reply=%s, "
                    "card=%s)", phone, ai_read_b, ai_reply_b, bool(card_text))
        return _ok(note_msg)

    async def _save_private_media(*, phone: str, request: Request, upload: UploadFile,
                                  kind: str, conversation_id: str, channel_id: str,
                                  mentions_raw: str, mention_inbox_raw: str,
                                  caption: str = "") -> dict:
        """Persist a panel-only media private note (image/document) + record mentions.

        Espelha ``/private-audio`` para mídia estática: grava em ``statics/outbox/``,
        salva ``role='private_note'`` com ``media_type``/``media_path`` (autoria), emite
        ``new_message`` e registra as menções. Nunca vai ao GOWA."""
        resolved_channel = _channel_for(phone, conversation_id, channel_id)
        filename = upload.filename or ("imagem.jpg" if kind == "image" else "arquivo")
        safe_name = Path(filename).name
        dest = statics_outbox_dir / unique_media_name(
            upload.content_type, safe_name,
            default_ext=(".jpg" if kind == "image" else ".bin"))
        dest.write_bytes(await upload.read())
        rel_path = f"statics/outbox/{dest.name}"

        if kind == "image":
            db_content = caption.strip() or "[Imagem]"
        else:
            db_content = f"[Documento enviado: {safe_name}]"
            if caption.strip():
                db_content = f"{db_content}\n{caption.strip()}"

        _u = current_user(request)

        def _save():
            contact = agent_handler._get_contact(phone, channel_id=resolved_channel)
            # Row from add_message (not a racy get_last) — plano 53.
            saved_row = contact.add_message(
                "private_note", db_content,
                media_type=kind, media_path=rel_path,
                sent_by_user_id=(_u.get("id") if _u else None),
                sent_by_name=(_u.get("name") if _u else None))
            return contact.id, saved_row
        contact_id, saved = await asyncio.to_thread(_save)

        note_msg = {
            "role": "private_note", "content": db_content,
            "ts": (saved or {}).get("ts", time.time()),
            "media_type": kind, "media_path": rel_path, "status": None,
            "conversation_id": (saved or {}).get("conversation_id"),
        }
        if _u and _u.get("name"):
            note_msg["sent_by_name"] = _u.get("name")
        if saved and saved.get("id"):
            note_msg["_id"] = saved["id"]
        if saved and saved.get("msg_id"):
            note_msg["msg_id"] = saved["msg_id"]
        await ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": resolved_channel, "message": note_msg})

        await _record_private_mentions(
            saved=saved, phone=phone, contact_id=contact_id, channel_id=resolved_channel,
            actor=_u, raw_mentions=_parse_mentions_field(mentions_raw),
            mention_inbox=str(mention_inbox_raw).lower() in ("1", "true", "yes", "on"),
            preview=db_content)
        return note_msg

    @app.post("/api/contacts/{phone}/private-image")
    async def send_private_image(
        phone: str,
        request: Request,
        image: UploadFile = File(...),
        caption: str = Form(""),
        conversation_id: str = Form(""),
        channel_id: str = Form(""),
        mentions: str = Form(""),
        mention_inbox: str = Form("false"),
    ):
        """Imagem como nota privada — só no painel, nunca enviada ao contato."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        denied_inbox = await _inbox_send_denied(
            request, conversation_id=conversation_id, channel_id=channel_id)
        if denied_inbox:
            return denied_inbox
        try:
            note_msg = await _save_private_media(
                phone=phone, request=request, upload=image, kind="image",
                conversation_id=conversation_id, channel_id=channel_id,
                mentions_raw=mentions, mention_inbox_raw=mention_inbox, caption=caption)
        except Exception as e:
            logger.error("[Private] Failed to save private image for %s: %s", phone, e)
            return _err(f"Erro ao salvar imagem privada: {e}", status=500)
        logger.info("[Private] Saved private image for %s", phone)
        return _ok(note_msg)

    @app.post("/api/contacts/{phone}/private-document")
    async def send_private_document(
        phone: str,
        request: Request,
        document: UploadFile = File(...),
        caption: str = Form(""),
        conversation_id: str = Form(""),
        channel_id: str = Form(""),
        mentions: str = Form(""),
        mention_inbox: str = Form("false"),
    ):
        """Documento como nota privada — só no painel, nunca enviado ao contato."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        denied_inbox = await _inbox_send_denied(
            request, conversation_id=conversation_id, channel_id=channel_id)
        if denied_inbox:
            return denied_inbox
        try:
            note_msg = await _save_private_media(
                phone=phone, request=request, upload=document, kind="document",
                conversation_id=conversation_id, channel_id=channel_id,
                mentions_raw=mentions, mention_inbox_raw=mention_inbox, caption=caption)
        except Exception as e:
            logger.error("[Private] Failed to save private document for %s: %s", phone, e)
            return _err(f"Erro ao salvar documento privado: {e}", status=500)
        logger.info("[Private] Saved private document for %s", phone)
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
        _operator_took_over(channel_id, phone)
        wire_phone = await asyncio.to_thread(
            _wire_target, phone, body.get("conversation_id"))
        block = await asyncio.to_thread(
            _session_window_block, channel_id, body.get("conversation_id"), phone)
        if block:
            return block
        # Track for echo-back filtering — key on the WIRE target (real JID).
        state.recently_sent[f"{channel_id}:{wire_phone}:{message[:120]}"] = time.time()

        msg_id = None
        try:
            msg_id = await asyncio.to_thread(_route_send_text, channel_id, wire_phone, message)
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
        _operator_took_over(channel_id, phone)
        wire_phone = await asyncio.to_thread(_wire_target, phone, conversation_id)
        # 24h window gate BEFORE writing the file (no orphan on a blocked send);
        # sandbox stays local so it is never gated (mirrors /send text).
        if not is_sandbox:
            block = await asyncio.to_thread(_session_window_block, channel_id, conversation_id, phone)
            if block:
                return block
        suffix = Path(image.filename or "img.png").suffix or ".png"
        content = await image.read()
        # Bloqueio de tamanho/formato ANTES de gravar (sem órfão no disco).
        if not is_sandbox:
            block = _media_limits_block(
                channel_id, "image", image.filename or f"img{suffix}", len(content))
            if block:
                return block
        dest = statics_outbox_dir / unique_media_name(
            image.content_type, image.filename, default_ext=".png")
        dest.write_bytes(content)
        # R14: shared operator media-send tail (send → persist → broadcast → emit).
        _u = current_user(request)
        result = await messaging.send_media(
            channel_id=channel_id, phone=phone, kind="image", dest=dest,
            is_sandbox=is_sandbox, content=caption, emit_text=caption,
            caption=caption, error_label="imagem",
            sent_by_user_id=(_u.get("id") if _u else None),
            sent_by_name=(_u.get("name") if _u else None),
            wire_phone=wire_phone)
        if not result["ok"]:
            verb = "Falha" if result["kind"] == "send" else "Erro"
            return _err(f"{verb} ao enviar imagem: {result['error']}", status=500)
        logger.info("[Send] Image sent to %s", phone)
        return _ok({"message": "Imagem enviada.",
                    "msg_id": result.get("msg_id"),
                    "media_path": result.get("media_path")})

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
        _operator_took_over(channel_id, phone)
        wire_phone = await asyncio.to_thread(_wire_target, phone, conversation_id)
        # 24h window gate BEFORE writing the file (no orphan on a blocked send);
        # sandbox stays local so it is never gated (mirrors /send text).
        if not is_sandbox:
            block = await asyncio.to_thread(_session_window_block, channel_id, conversation_id, phone)
            if block:
                return block
        suffix = Path(audio.filename or "voice.ogg").suffix or ".ogg"
        content = await audio.read()
        # Canal que declara AudioLimits (codec-aware) segue o caminho do vídeo:
        # grava → valida (ffprobe) → recodifica com ffmpeg → só bloqueia se não
        # der. Canal com MediaLimits simples (ou nenhum) mantém o bloqueio
        # barato ANTES de gravar (sem órfão no disco). Dirigido pelo que o
        # PROVIDER declara, nunca por nome de provider.
        caps = outbound.capabilities(channel_id) if not is_sandbox else None
        alimits = audio_validate.audio_limits(caps) if caps is not None else None
        if not is_sandbox and alimits is None:
            block = _media_limits_block(
                channel_id, "audio", audio.filename or f"voice{suffix}", len(content))
            if block:
                return block
        dest = statics_outbox_dir / unique_media_name(
            audio.content_type, audio.filename, default_ext=".ogg")
        dest.write_bytes(content)

        if alimits is not None:
            verdict = await asyncio.to_thread(audio_validate.validate_audio, str(dest), caps)
            if not verdict.ok:
                # Recodifica para o container/codec que ESTE canal declarou
                # (ex.: Ogg/Vorbis → Ogg/Opus, o único ogg que a Meta aceita).
                transcoded = await asyncio.to_thread(
                    audio_transcode.transcode_to_limits, str(dest), alimits)
                if transcoded:
                    new_dest = (statics_outbox_dir
                                / f"{int(time.time() * 1000)}{Path(transcoded).suffix}")
                    try:
                        os.replace(transcoded, new_dest)
                    except OSError:
                        import shutil as _shutil
                        _shutil.move(transcoded, str(new_dest))
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                    dest = new_dest
                else:
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                    status = 413 if verdict.reason == audio_validate.TOO_BIG else 415
                    return _err(verdict.message, status=status,
                                data={"reason": verdict.reason})
        # R14: shared media-send tail. Audio sends with no caption, persists
        # "[Áudio]" / emits empty text, and runs the operator-audio transcription
        # tail (audio_transcription_mode in sent/both) inside the service.
        _u = current_user(request)
        result = await messaging.send_media(
            channel_id=channel_id, phone=phone, kind="audio", dest=dest,
            is_sandbox=is_sandbox, content="[Áudio]", emit_text="",
            error_label="áudio", transcribe_audio=True,
            sent_by_user_id=(_u.get("id") if _u else None),
            sent_by_name=(_u.get("name") if _u else None),
            wire_phone=wire_phone)
        if not result["ok"]:
            verb = "Falha" if result["kind"] == "send" else "Erro"
            return _err(f"{verb} ao enviar áudio: {result['error']}", status=500)
        logger.info("[Send] Audio sent to %s", phone)
        return _ok({"message": "Áudio enviado.",
                    "msg_id": result.get("msg_id"),
                    "media_path": result.get("media_path")})

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
        _operator_took_over(channel_id, phone)
        wire_phone = await asyncio.to_thread(_wire_target, phone, conversation_id)
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
        content = await document.read()
        # Bloqueio de tamanho/formato ANTES de gravar (sem órfão no disco).
        if not is_sandbox:
            block = _media_limits_block(channel_id, "document", safe_name, len(content))
            if block:
                return block
        dest = statics_outbox_dir / unique_media_name(
            document.content_type, safe_name, default_ext=".bin")
        dest.write_bytes(content)
        text_content = f"[Documento enviado: {safe_name}]"
        if caption.strip():
            text_content = f"{text_content}\n{caption.strip()}"
        # R14: shared media-send tail. Document persists/broadcasts the label
        # (+caption) body, emits the caption as the message.sent text, and sends
        # with the caption + the safe original filename.
        _u = current_user(request)
        result = await messaging.send_media(
            channel_id=channel_id, phone=phone, kind="document", dest=dest,
            is_sandbox=is_sandbox, content=text_content, emit_text=caption,
            caption=caption, filename=safe_name, error_label="documento",
            sent_by_user_id=(_u.get("id") if _u else None),
            sent_by_name=(_u.get("name") if _u else None),
            wire_phone=wire_phone)
        if not result["ok"]:
            verb = "Falha" if result["kind"] == "send" else "Erro"
            return _err(f"{verb} ao enviar documento: {result['error']}", status=500)
        logger.info("[Send] Document sent to %s: %s", phone, safe_name)
        return _ok({"message": "Documento enviado.",
                    "msg_id": result.get("msg_id"),
                    "media_path": result.get("media_path")})

    @app.post("/api/contacts/{phone}/send-video")
    async def send_video_to_contact(
        phone: str,
        request: Request,
        video: UploadFile = File(...),
        caption: str = Form(""),
        conversation_id: str = Form(""),
        channel_id: str = Form(""),
    ):
        """Send a video to a contact (operator-initiated) — plano 65.

        Routes ``kind="video"`` to the channel (WhatsApp Cloud already builds
        ``type:"video"``; GOWA/Telegram degrade to file/sendVideo). The upload is
        validated against the ``VideoLimits`` the CHANNEL declares (plano 65 —
        WhatsApp Cloud declares mp4/3gp, H.264/AAC, ≤16 MB; GOWA/Telegram declare
        none and are never blocked); a non-conforming file is transcoded when ffmpeg
        is present (F5B) and otherwise blocked with a clear message (F5A). Never
        keys off provider name — the policy comes from the provider.
        """
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        denied_inbox = await _inbox_send_denied(
            request, conversation_id=conversation_id, channel_id=channel_id)
        if denied_inbox:
            return denied_inbox
        is_sandbox = await asyncio.to_thread(_is_sandbox_contact, phone)
        channel_id = _channel_for(phone, conversation_id, channel_id)
        _operator_took_over(channel_id, phone)
        wire_phone = await asyncio.to_thread(_wire_target, phone, conversation_id)
        # 24h window gate BEFORE writing the file (no orphan on a blocked send);
        # sandbox stays local so it is never gated (mirrors /send text).
        if not is_sandbox:
            block = await asyncio.to_thread(_session_window_block, channel_id, conversation_id, phone)
            if block:
                return block
        suffix = Path(video.filename or "video.mp4").suffix or ".mp4"
        dest = statics_outbox_dir / unique_media_name(
            video.content_type, video.filename, default_ext=".mp4")
        content = await video.read()
        dest.write_bytes(content)

        # Validate against the Cloud limits (no-op for always-open channels). On a
        # windowed channel a non-conforming file is transcoded (if ffmpeg) or
        # blocked. Runs off-thread (ffprobe/ffmpeg are blocking).
        if not is_sandbox:
            caps = outbound.capabilities(channel_id)
            verdict = await asyncio.to_thread(video_validate.validate_video, str(dest), caps)
            if not verdict.ok:
                # Re-encode toward the limits THIS channel declared (plano 65).
                limits = video_validate.video_limits(caps)
                transcoded = await asyncio.to_thread(
                    video_transcode.transcode_to_limits, str(dest), limits)
                if transcoded:
                    # Move the transcoded mp4 into the outbox so its media_path
                    # resolves for the panel render; drop the original upload.
                    new_dest = statics_outbox_dir / unique_media_name(
                        "video/mp4", "video.mp4", default_ext=".mp4")
                    try:
                        os.replace(transcoded, new_dest)
                    except OSError:
                        import shutil as _shutil
                        _shutil.move(transcoded, str(new_dest))
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                    dest = new_dest
                else:
                    # Block (F5A): remove the orphan upload and return a clear error.
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                    status = 413 if verdict.reason == video_validate.TOO_BIG else 415
                    return _err(verdict.message, status=status,
                                data={"reason": verdict.reason})

        # R14: shared operator media-send tail (send → persist → broadcast → emit).
        _u = current_user(request)
        result = await messaging.send_media(
            channel_id=channel_id, phone=phone, kind="video", dest=dest,
            is_sandbox=is_sandbox, content=caption or "[Vídeo]", emit_text=caption,
            caption=caption, error_label="vídeo",
            sent_by_user_id=(_u.get("id") if _u else None),
            sent_by_name=(_u.get("name") if _u else None),
            wire_phone=wire_phone)
        if not result["ok"]:
            verb = "Falha" if result["kind"] == "send" else "Erro"
            # Meta rejects a codec ffprobe could not inspect (131053) — surface it
            # as a friendly hint instead of the raw provider string (F5A).
            err = result["error"] or ""
            if "131053" in err:
                return _err(
                    "O WhatsApp recusou o vídeo (codec/formato). "
                    "Reexporte em MP4 H.264/AAC e tente novamente.",
                    status=422, data={"reason": "bad_codec"})
            return _err(f"{verb} ao enviar vídeo: {err}", status=500)
        logger.info("[Send] Video sent to %s", phone)
        return _ok({"message": "Vídeo enviado."})

    @app.post("/api/contacts/{phone}/presence")
    async def send_presence_to_contact(phone: str, body: dict, request: Request):
        """Send typing/stop presence indicator to a contact (operator-initiated).

        Capability-gated: channels without presence (e.g. WhatsApp Cloud) no-op."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        action = body.get("action", "start")
        # plano 37 (C2): honra channel_id quando o painel inicia conversa nova sem
        # conversation_id ainda — senão o presence cairia no 'default'.
        channel_id = _channel_for(phone, body.get("conversation_id"), body.get("channel_id"))
        # Colaboração entre atendentes (multi-operador): o MESMO sinal que vai ao
        # cliente avisa os outros painéis logados — "Fulano está digitando…" na linha
        # da conversa, para dois atendentes não responderem por cima um do outro.
        # Efêmero: só WebSocket, nada persistido. Emitido ANTES da ida ao provedor
        # (canal offline/sem capability de presence não pode calar o aviso interno).
        # Sem identidade de usuário (instalação aberta, sem login) não emite: o
        # painel não teria como filtrar o próprio autor e o operador veria a si mesmo.
        _u = current_user(request)
        if _u and _u.get("id") is not None:
            await ws_manager.broadcast("operator_typing", {
                "phone": phone,
                "channel_id": channel_id,
                "conversation_id": body.get("conversation_id"),
                "user_id": _u.get("id"),
                "user_name": _u.get("name") or _u.get("email") or "Atendente",
                "active": action == "start",
            })
        # Plano 96 I7 (D3): o atendente digitando SEGURA a resposta da IA, como a
        # digitação do cliente já fazia — o pior cenário é os dois responderem por
        # cima um do outro. Segurar, não cancelar: ele pode desistir do texto.
        # Escrito ANTES de ir ao provedor (mesma ordem do broadcast acima: canal
        # offline ou sem capability de presence não pode calar o efeito interno) e
        # sem exigir identidade de usuário — o pipeline não precisa saber QUEM digita.
        state.operator_typing_state[(channel_id, phone)] = {
            "active": action == "start", "last_ts": time.time()}
        await asyncio.to_thread(outbound.send_presence, channel_id, phone, action)
        return _ok({"status": "ok"})

    @app.post("/api/contacts/{phone}/read")
    async def mark_contact_read(phone: str, request: Request, body: dict = Body(default={})):
        """Mark all messages from this contact as read (reset unread_count)."""
        denied = permission_denied(request, "conversation.reply")
        if denied:
            return denied
        def _mark():
            contact = agent_handler._get_contact(phone)
            return contact.mark_as_read()
        msg_ids = await asyncio.to_thread(_mark)
        if msg_ids:
            # plano 38 F3: route the receipt through the conversation's channel.
            channel_id = _channel_for(phone, body.get("conversation_id"), body.get("channel_id"))
            asyncio.create_task(_send_read_receipts(phone, msg_ids, channel_id))
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
        # plano 37 (B3/P2): quando o painel diz QUAL conversa/canal está togglando,
        # resolve o inbox pra ancorar card + mirror ai_active NAQUELE canal (num
        # contato multicanal, não reflete no outro). Sem os campos → legado.
        toggle_conv_id = body.get("conversation_id")
        toggle_chan_id = body.get("channel_id")
        toggle_inbox_id = None
        if toggle_conv_id or toggle_chan_id:
            toggle_inbox_id = await asyncio.to_thread(
                _resolve_inbox_id, toggle_conv_id, toggle_chan_id)
        def _toggle():
            contact = agent_handler._get_contact(phone)
            contact.set_ai_enabled(bool(enabled))
            return contact.id, contact.ai_enabled
        contact_id, result = await asyncio.to_thread(_toggle)
        # The service emits contact.ai_toggled (WS + bus), writes the ai_on/ai_off
        # system-notice card, and mirrors the flip onto the contact's conversation
        # (P17: set_ai_active + WS conversation_ai_toggled — WS only, not the bus),
        # each exactly once. The route keeps owning the contact-level flip above.
        actor = (current_user(request) or {}).get("name") or None
        await conv_svc.toggle_contact_ai(
            deps, phone=phone, enabled=bool(result), contact_id=contact_id,
            actor_name=actor, inbox_id=toggle_inbox_id)
        return _ok({"ai_enabled": result})

    @app.get("/api/contacts/{phone}/avatar")
    async def get_contact_avatar(phone: str, request: Request,
                                 conversation_id: int | None = None,
                                 channel_id: str | None = None):
        """Return contact's profile photo (cached on disk)."""
        denied = permission_denied(request, "contact.read")
        if denied:
            return denied
        avatars_dir = statics_outbox_dir.parent / "avatars"
        avatars_dir.mkdir(parents=True, exist_ok=True)
        avatar_path = avatars_dir / f"{phone}.jpg"

        if avatar_path.exists():
            return FileResponse(str(avatar_path), media_type="image/jpeg")

        # plano 38 F5: fetch on-demand via the contact's channel (registry hook), not a
        # hardcoded GOWA call. A Telegram/Cloud-only contact returns None → 204.
        channel = _channel_for(phone, conversation_id, channel_id)
        try:
            data = await asyncio.to_thread(outbound.fetch_avatar, channel, phone)
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
            all_defs = await asyncio.to_thread(
                ca_repo.list_definitions, "contact", True)  # include soft-deleted
            defs = {d["attribute_key"]: d for d in all_defs if d.get("deleted_at") is None}
            known_keys = {d["attribute_key"] for d in all_defs}
            # Keys already stored on this contact but without any definition — e.g.
            # `cw_id`/`cw_identifier` left behind by the Chatwoot migration. The panel
            # re-sends the whole JSON on save; tolerating these avoids a 400 that would
            # abort the entire save (name, email, tags). Still preserves P50 for a
            # genuinely new + undefined key (typo from code) → 400.
            stored = await asyncio.to_thread(contact_repo.get_by_phone, phone)
            stored_keys = set((stored or {}).get("custom_attributes") or {})
            for key, value in custom_attrs.items():
                definition = defs.get(key)
                if definition is None:
                    # A value left behind by a DELETED attribute (soft-delete keeps
                    # stored values, P49) or by the Chatwoot migration (never defined,
                    # but already in the stored JSON) is tolerated — the frontend
                    # re-sends it. Leave it untouched instead of blocking the save.
                    # Only a key that is BOTH undefined AND not stored is a genuine
                    # typo → 400 (P50).
                    if key in known_keys or key in stored_keys:
                        continue
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
