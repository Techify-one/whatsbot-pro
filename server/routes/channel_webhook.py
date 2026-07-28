"""Per-provider webhook endpoints (plano 02 Fase 0.5 / Fase 2).

Generic ingress for provider-plugins that deliver inbound by HTTP path
(WhatsApp Cloud API, Telegram, …). The CORE owns the endpoint (plugins never
open their own webhook route — §2.3); it resolves the channel in the
``ChannelRegistry`` and delegates payload parsing to the provider's
``parse_inbound``.

Auth: these paths are exempt from the Bearer middleware (added to
``_AUTH_EXEMPT_PREFIXES`` as ``/api/webhook/``) — providers authenticate the
request themselves (Cloud API ``verify_token`` on GET; signature/verify on POST).
The exact ``/api/webhook`` (GOWA) and ``/health`` exemptions are preserved.

GET  = handshake (Cloud API ``hub.challenge`` verification).
POST = inbound delivery → ``parse_inbound`` (always answers 200 so the provider
       does not retry). NOTE: feeding the parsed events into the agentic
       reply loop reuses the batch orchestrator in ``webhook.py`` and lands with
       the live-pipeline refactor (0.5) — this route parses + records for now.
"""

import asyncio
import json
import logging
import time

from fastapi import Request
from fastapi.responses import PlainTextResponse

from db.repositories import (channel_repo, channel_credential_repo, message_repo,
                             contact_repo, conversation_repo, inbox_repo)
from agent import group_mentions
from plugins.events import emit_with_filter, apply_filter
from server.authz import permission_denied
from server.helpers import _ok, _err
from server.message_errors import describe_failure

logger = logging.getLogger(__name__)

# Last raw payloads per provider (in-memory, debug) — mirrors webhook-payloads.
_RECENT: list[dict] = []
_RECENT_CAP = 50

# Retentativa do casamento de um ``status=failed`` cuja linha ainda não existe
# (plano 75 F5, corrida com o writer do split de resposta — ver
# ``_schedule_failed_retry``). Módulo-level de propósito: os testes reduzem o
# intervalo por monkeypatch para não esperar segundos de relógio.
_FAILED_RETRY_ATTEMPTS = 3
_FAILED_RETRY_INTERVAL = 2.0
# Referências fortes das tasks em voo — sem isso o GC pode coletar uma task só
# referenciada pelo loop e a retentativa some no meio do caminho.
_RETRY_TASKS: set = set()


def _first_error(errors) -> dict:
    """Extract ``code``/``title``/``details`` from a provider ``errors`` array.

    Shape of the Meta payload (plano 75 §12.8)::

        "errors": [{"code": 131049, "title": "...", "message": "...",
                    "error_data": {"details": "..."}, "href": "..."}]

    Everything is optional and defensive: ``errors`` may be absent, empty, not a
    list, or carry different keys across providers/API versions — this never
    raises and always returns the three keys (``code`` may be ``None``, the two
    strings may be empty). Only the FIRST item is described; the full array
    stays available to subscribers through the event's ``raw``.
    """
    item = None
    if isinstance(errors, list) and errors:
        item = errors[0]
    elif isinstance(errors, dict):
        item = errors
    if not isinstance(item, dict):
        return {"code": None, "title": "", "details": ""}
    code = item.get("code")
    if isinstance(code, str):
        try:
            code = int(code.strip())
        except ValueError:
            pass  # keep the raw string — a caller may still log/display it
    data = item.get("error_data")
    if not isinstance(data, dict):
        data = {}
    return {
        "code": code,
        "title": item.get("title") or item.get("message") or "",
        "details": data.get("details") or item.get("details") or "",
    }


def register_routes(app, deps):

    registry = getattr(deps, "channel_registry", None)
    ws_manager = getattr(deps, "ws_manager", None)
    agent_handler = getattr(deps, "agent_handler", None)
    state = getattr(deps, "state", None)

    def _resolve_presence_conv_id(channel_id: str, phone: str):
        """Resolve the conversation id for a typing indicator so the frontend
        scopes "digitando" to the exact conversation (it keys by conversation_id —
        a None would never match the open chat's ``conv:<id>`` key). Cached per
        (channel, phone) with a short TTL; best-effort → None on any failure."""
        now = time.time()
        cache = getattr(state, "presence_conv_cache", None) if state is not None else None
        ckey = (channel_id, phone)
        if cache is not None:
            cached = cache.get(ckey)
            if cached and cached[1] > now:
                return cached[0]
        conv_id = None
        try:
            contact = contact_repo.get_by_phone(phone)
            if contact:
                inbox = inbox_repo.get_by_channel(channel_id)
                inbox_id = inbox["id"] if inbox else conversation_repo.DEFAULT_INBOX_ID
                conv = conversation_repo.get_latest_for_contact_inbox(
                    contact["id"], inbox_id)
                if conv:
                    conv_id = conv["id"]
        except Exception:
            logger.debug("presence conv_id resolution failed for %s/%s",
                         channel_id, phone, exc_info=True)
        if cache is not None:
            cache[ckey] = (conv_id, now + 30.0)
        return conv_id

    def _clear_unread_for_msg_ids(msg_ids: list) -> list:
        """Clear unread state for any loaded contact whose unread ids intersect
        ``msg_ids`` (legacy GOWA ``message.ack`` read-receipt parity). Returns the
        phone keys that were marked read so the caller can broadcast
        ``messages_read``. Sync (DB writes) — call via ``asyncio.to_thread``."""
        cleared: list = []
        if agent_handler is None:
            return cleared
        # ``_contacts`` is keyed by (channel_id, phone) — plano 11.
        for (_ch, phone_key), contact in list(agent_handler._contacts.items()):
            try:
                unread_ids = contact.get_unread_msg_ids()
                if any(mid in unread_ids for mid in msg_ids):
                    contact.mark_as_read()
                    cleared.append(phone_key)
            except Exception:
                logger.debug("unread clear failed for %s", phone_key, exc_info=True)
        return cleared

    async def _emit_failure_card(ev, failed_row: dict, errors) -> None:
        """Grava o card ``role="error"`` no fio da conversa (plano 75 F6).

        Por que ``error``: o role já é painel-only em todas as camadas — fora do
        contexto do LLM (``message_repo.get_context``), fora do preview da
        sidebar (``LIST_PANEL_ONLY_ROLES``) e já tem card renderizado
        (``SystemMessageCard.js``). Zero mudança de frontend.

        Dedup (R7): quem chama só entra aqui com ``failed_row`` truthy, e essa
        row só existe quando ``mark_failed_by_msg_id`` fez a transição de fato —
        a reentrega rotineira do webhook da Meta devolve ``None`` e não gera um
        2º card.

        Defensivo (mesmo princípio de ``emit_conversation_notice``): um card que
        falha ao ser gravado é logado e engolido — nunca derruba o processamento
        do webhook nem os eventos de bus que vêm depois.
        """
        try:
            contact_id = failed_row.get("contact_id")
            if not contact_id:
                return
            err = _first_error(errors)
            content = describe_failure(
                code=err["code"], title=err["title"], details=err["details"])
            conversation_id = failed_row.get("conversation_id")
            msg = await asyncio.to_thread(
                message_repo.add, int(contact_id), "error", content,
                conversation_id=conversation_id)
            if ws_manager is not None:
                await ws_manager.broadcast("new_message", {
                    "phone": ev.chat_id,
                    "channel_id": ev.channel_id,
                    "message": {"role": "error", "content": content,
                                "ts": msg["ts"],
                                "conversation_id": conversation_id}})
        except Exception:
            logger.exception(
                "[Webhook %s] falha ao gravar o card de erro para msg_id=%s",
                getattr(ev, "channel_id", "?"), failed_row.get("msg_id"))

    async def _apply_failure(ev, failed_row: dict, errors) -> None:
        """Efeitos colaterais de UMA transição para ``failed`` (plano 75 F5/F6).

        Fatorado porque roda em DOIS caminhos: o normal (a linha já estava no
        banco quando o webhook chegou) e o da retentativa
        (``_retry_failed_match``, quando a linha só apareceu depois). Duplicar o
        corpo nos dois lugares é como bolha vermelha e card sairiam de sincronia.

        Só é chamado com ``failed_row`` truthy — ou seja, quando
        ``mark_failed_by_msg_id`` GANHOU a transição. Esse UPDATE com
        ``WHERE status IN ('sent','delivered','operator')`` é o guard de
        unicidade: duas chamadas concorrentes (reentrega da Meta + retentativa,
        ou duas retentativas) serializam na row e só a primeira recebe o
        ``RETURNING`` — a segunda vê ``failed`` e volta ``None``. Logo, no
        máximo UM card por mensagem.
        """
        if ws_manager is not None:
            # O painel já pinta a bolha vermelha a partir deste evento
            # (useConversationWsEvents → MessageBubble).
            await ws_manager.broadcast("message_status", {
                "phone": ev.chat_id, "msg_ids": [failed_row.get("msg_id")],
                "status": "failed"})
        # O PORQUÊ, no fio da conversa, em PT-BR (plano 75 F6) — a bolha
        # vermelha sozinha não diz o que fazer a seguir.
        await _emit_failure_card(ev, failed_row, errors)

    async def _retry_failed_match(ev, msg_id: str, errors) -> None:
        """Tenta de novo casar um ``status=failed`` com a linha da mensagem.

        Corrida real (plano 75 F5): a resposta da IA é enviada parte a parte com
        um ``sleep(split_message_delay)`` no meio e só é GRAVADA depois do loop
        inteiro — a parte 1 fica ~2 s enviada-mas-não-persistida. Um webhook
        ``failed`` que caia nessa janela não acha linha nenhuma e a falha se
        perderia em silêncio (sem bolha vermelha, sem card).

        Limitada de propósito (``_FAILED_RETRY_ATTEMPTS`` × ``_FAILED_RETRY_INTERVAL``
        ≈ 6 s, folga sobre o split padrão de 2 s) e SEMPRE em background: a
        resposta HTTP já foi devolvida, porque um 200 lento faz a Meta reentregar
        em loop. Nunca levanta: um erro aqui é logado e a task morre.
        """
        for attempt in range(1, _FAILED_RETRY_ATTEMPTS + 1):
            await asyncio.sleep(_FAILED_RETRY_INTERVAL)
            try:
                row = await asyncio.to_thread(message_repo.mark_failed_by_msg_id, msg_id)
                if row is not None:
                    logger.info(
                        "[Webhook %s] status=failed casado na retentativa %d/%d "
                        "para msg_id=%s", ev.channel_id, attempt,
                        _FAILED_RETRY_ATTEMPTS, msg_id)
                    await _apply_failure(ev, row, errors)
                    return
                if await asyncio.to_thread(message_repo.exists_by_msg_id, msg_id):
                    # A linha apareceu, mas outra passada (reentrega da Meta ou
                    # uma retentativa irmã) já ganhou a transição — ou o
                    # destinatário já tinha lido. Nada a fazer: o card é único.
                    logger.info(
                        "[Webhook %s] status=failed já tratado por outra passada "
                        "para msg_id=%s", ev.channel_id, msg_id)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "[Webhook %s] retentativa %d de status=failed falhou para msg_id=%s",
                    ev.channel_id, attempt, msg_id, exc_info=True)
        err = _first_error(errors)
        logger.warning(
            "[Webhook %s] status=failed DESCARTADO: msg_id=%s não apareceu no banco "
            "após %d tentativas (código=%s, motivo=%r). Provável mensagem de outra "
            "instalação, apagada ou id desconhecido.",
            ev.channel_id, msg_id, _FAILED_RETRY_ATTEMPTS, err["code"], err["title"])

    def _schedule_failed_retry(ev, msg_id: str, errors) -> None:
        """Dispara ``_retry_failed_match`` em background (fire-and-forget).

        Nunca dentro do request: a Meta precisa do 200 rápido. Guarda a
        referência forte em ``_RETRY_TASKS`` (o loop só mantém referência fraca)
        e a solta no término.
        """
        try:
            task = asyncio.create_task(_retry_failed_match(ev, msg_id, errors))
        except RuntimeError:
            # Sem loop rodando (chamada fora do servidor) — melhor perder a
            # retentativa do que derrubar o webhook.
            logger.warning("[Webhook %s] não foi possível agendar a retentativa "
                           "de status=failed para msg_id=%s",
                           getattr(ev, "channel_id", "?"), msg_id, exc_info=True)
            return
        _RETRY_TASKS.add(task)
        task.add_done_callback(_RETRY_TASKS.discard)

    async def _dispatch_events(events: list) -> int:
        """Route parsed InboundEvents (plano 11 Fase 2 / plano 13 Fase 0).

        message  → the agentic ingress (deps.ingest_event; handles echo via
                   ``direction='out'``), same orchestrator for every channel.
        reaction/receipt/edited/revoked/deleted/presence/group_*/call/newsletter
                 → persist + broadcast + bus event (panel + plugin parity with the
                   legacy GOWA handler — moved here so GOWA ingresses through this
                   same dispatch). Returns the number of events that produced an action.
        """
        ingest = getattr(deps, "ingest_event", None)
        handled = 0
        for ev in events:
            kind = getattr(ev, "kind", "message")
            extras = ev.media_extras or {}
            try:
                if kind == "message":
                    if ingest is not None:
                        await ingest(ev)
                        handled += 1
                elif kind == "reaction":
                    reacted_id = extras.get("reacted_message_id", "")
                    emoji = extras.get("emoji", "")
                    is_from_me = bool(extras.get("is_from_me", False))
                    if reacted_id:
                        reactor = "me" if is_from_me else (ev.sender_id or ev.chat_id or "")
                        reactions = await asyncio.to_thread(
                            message_repo.set_reaction, reacted_id, emoji, reactor)
                        if reactions is not None and ws_manager is not None:
                            await ws_manager.broadcast("message_reaction", {
                                "phone": ev.chat_id, "msg_id": reacted_id, "reactions": reactions})
                    await emit_with_filter("message.reaction", {
                        "id": ev.external_msg_id, "phone": ev.chat_id,
                        "from": ev.sender_id, "reaction": emoji,
                        "reacted_message_id": reacted_id, "is_from_me": is_from_me,
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "receipt":
                    status = extras.get("status")
                    mid = ev.external_msg_id
                    errors = extras.get("errors")
                    # Row of the message a delivery FAILURE landed on — also the
                    # dedup guard (see below) and the hook point for the error
                    # card in the thread (plano 75 F6): it carries id,
                    # contact_id, conversation_id and content.
                    failed_row = None
                    if status in ("delivered", "read") and mid:
                        updated = await asyncio.to_thread(
                            message_repo.update_status_by_msg_id, mid, status)
                        if updated and ws_manager is not None:
                            await ws_manager.broadcast("message_status", {
                                "phone": ev.chat_id, "msg_ids": updated, "status": status})
                        # Unread tracking parity with the legacy GOWA handler: a
                        # "read" ack on an INCOMING message we hadn't read yet
                        # clears its unread state (marks the contact read +
                        # broadcasts ``messages_read``). delivered acks never touch
                        # unread (only the outgoing status).
                        if status == "read" and agent_handler is not None:
                            cleared = await asyncio.to_thread(
                                _clear_unread_for_msg_ids, [mid])
                            if cleared and ws_manager is not None:
                                for phone_key in cleared:
                                    await ws_manager.broadcast(
                                        "messages_read", {"phone": phone_key})
                    elif status == "failed" and mid:
                        # The provider accepted the send and only later told us it
                        # was not delivered (plano 75 F5) — until now this whole
                        # branch fell into the void and the operator saw nothing.
                        # ``mark_failed_by_msg_id`` returns the row ONLY on a real
                        # transition, so it doubles as the redelivery guard: Meta
                        # re-posts the same status routinely and the 2nd pass finds
                        # the row already 'failed' → None → no duplicate side effect.
                        failed_row = await asyncio.to_thread(
                            message_repo.mark_failed_by_msg_id, mid)
                        if failed_row is None:
                            # ``None`` funde dois casos bem diferentes (docstring
                            # do repo). ``exists_by_msg_id`` os separa:
                            if await asyncio.to_thread(
                                    message_repo.exists_by_msg_id, mid):
                                # Dedup legítimo: a linha existe e já está
                                # 'failed' (reentrega da Meta) ou 'read'.
                                logger.info(
                                    "[Webhook %s] status=failed sem transição para "
                                    "msg_id=%s (já 'failed' ou já lida)",
                                    ev.channel_id, mid)
                            else:
                                # Não existe linha NENHUMA: ou a mensagem ainda
                                # não foi persistida (corrida com o writer do
                                # split de resposta — janela de ~2 s), ou o
                                # msg_id é mesmo desconhecido. Só o tempo diz;
                                # a retentativa em background decide.
                                logger.info(
                                    "[Webhook %s] status=failed sem linha para "
                                    "msg_id=%s — agendando retentativa",
                                    ev.channel_id, mid)
                                _schedule_failed_retry(ev, mid, errors)
                        else:
                            await _apply_failure(ev, failed_row, errors)
                    # ``sent``/``played``: nothing is persisted on purpose — an
                    # outgoing message is already born 'sent'/'operator' and
                    # writing here would break the monotonic ack semantics of
                    # ``update_status_by_msg_id``. The bus event below still fires.

                    # Bus emit for EVERY status (sent/delivered/read/failed/played).
                    # It used to live INSIDE the delivered/read branch, so no plugin
                    # could ever react to a delivery failure. Emitted AFTER the DB
                    # write so a subscriber that queries the DB sees the new state.
                    await emit_with_filter("receipt.changed", {
                        "phone": ev.chat_id, "msg_ids": [mid] if mid else [],
                        "status": status, "errors": errors,
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time()})
                    if status == "failed":
                        # The automation hook. Always emitted ON THIS FIRST PASS —
                        # a failure we cannot match to a stored message is still a
                        # failure worth surfacing, and automation must not depend
                        # on the row being persisted yet (plano 75 F5).
                        # ``is_new`` keeps its exact meaning: "the transition
                        # happened in THIS pass" — the dedup guard for the routine
                        # webhook redelivery. A retry that later matches the row
                        # does NOT emit a second ``message.failed`` (a subscriber
                        # would count the same failure twice); it only paints the
                        # bubble + writes the single error card. Same reason
                        # ``conversation_id`` stays ``None`` when there is no row:
                        # guessing "the contact's latest conversation" could
                        # attach the failure to the WRONG one — subscribers that
                        # need it can look it up by ``msg_id`` afterwards.
                        err = _first_error(errors)
                        await emit_with_filter("message.failed", {
                            "phone": ev.chat_id, "channel_id": ev.channel_id,
                            "msg_id": mid,
                            "error_code": err["code"],
                            "error_title": err["title"],
                            "error_details": err["details"],
                            "conversation_id": (failed_row or {}).get("conversation_id"),
                            "is_new": failed_row is not None,
                            "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "edited":
                    # A counterpart edited a message on their side: mirror it into
                    # the panel — update the stored content + stamp edited_ts, then
                    # broadcast so the open thread swaps the bubble in place (same
                    # contract as the operator edit endpoint). Keyed by the provider
                    # msg_id of the ORIGINAL message. Falls back to plugin-only when
                    # the message isn't in our DB (matched is None).
                    orig_id = extras.get("original_message_id", "") or ev.external_msg_id
                    new_text = ev.text or ""
                    edited_ts = None
                    db_id = None
                    if orig_id and new_text:
                        row = await asyncio.to_thread(message_repo.get_by_msg_id, orig_id)
                        if row:
                            db_id = row.get("_id")
                            edited_ts = await asyncio.to_thread(
                                message_repo.mark_edited, int(db_id), new_text)
                            if edited_ts and ws_manager is not None:
                                await ws_manager.broadcast("message_edited", {
                                    "phone": ev.chat_id, "msg_id": orig_id, "db_id": db_id,
                                    "content": new_text, "edited_ts": edited_ts,
                                    "conversation_id": row.get("conversation_id")})
                    await emit_with_filter("message.edited", {
                        "id": ev.external_msg_id, "phone": ev.chat_id, "from": ev.sender_id,
                        "original_message_id": extras.get("original_message_id", ""),
                        "body": ev.text, "is_from_me": bool(extras.get("is_from_me", False)),
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "revoked":
                    revoked_id = extras.get("revoked_message_id", "")
                    if revoked_id:
                        matched = await asyncio.to_thread(message_repo.mark_revoked, revoked_id, "all")
                        if matched and ws_manager is not None:
                            await ws_manager.broadcast("message_revoked", {
                                "phone": ev.chat_id, "msg_id": revoked_id})
                    await emit_with_filter("message.revoked", {
                        "id": ev.external_msg_id, "phone": ev.chat_id, "from": ev.sender_id,
                        "revoked_message_id": revoked_id,
                        "revoked_from_me": bool(extras.get("revoked_from_me", False)),
                        "revoked_chat": extras.get("revoked_chat", ""),
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "deleted":
                    deleted_id = extras.get("deleted_message_id", "")
                    if deleted_id:
                        matched = await asyncio.to_thread(message_repo.mark_revoked, deleted_id, "me")
                        if matched and ws_manager is not None:
                            await ws_manager.broadcast("message_deleted", {
                                "phone": ev.chat_id, "msg_id": deleted_id})
                    await emit_with_filter("message.deleted", {
                        "deleted_message_id": deleted_id, "phone": ev.chat_id, "from": ev.sender_id,
                        "original_content": extras.get("original_content", ""),
                        "original_sender": extras.get("original_sender", ""),
                        "original_timestamp": extras.get("original_timestamp"),
                        "was_from_me": bool(extras.get("was_from_me", False)),
                        "channel_id": ev.channel_id, "ts": ev.ts or time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "presence":
                    phone = ev.chat_id
                    pstate = extras.get("state", "")
                    media = extras.get("media", "") or "text"
                    if phone and pstate:
                        if state is not None:
                            state.typing_state[(ev.channel_id, phone)] = {
                                "active": pstate == "composing", "media": media,
                                "last_ts": time.time()}
                        # Resolve the exact conversation so the panel scopes the
                        # "digitando" indicator to it (frontend keys by conversation_id).
                        conv_id = await asyncio.to_thread(
                            _resolve_presence_conv_id, ev.channel_id, phone)
                        if ws_manager is not None:
                            await ws_manager.broadcast("chat_presence", {
                                "phone": phone, "channel_id": ev.channel_id,
                                "conversation_id": conv_id, "state": pstate, "media": media})
                        await emit_with_filter("presence.changed", {
                            "phone": phone, "state": pstate, "media": media,
                            "channel_id": ev.channel_id, "ts": ev.ts or time.time()})
                    handled += 1
                elif kind == "group_participants":
                    chat_id = ev.chat_id
                    ctype = extras.get("type", "")
                    jids = extras.get("jids", []) or []
                    if chat_id:
                        members = await asyncio.to_thread(
                            group_mentions.apply_participants_change, chat_id, ctype, jids)
                        if ws_manager is not None:
                            await ws_manager.broadcast("group_participants_changed",
                                                       {"group_jid": chat_id, "members": members})
                        existing = await asyncio.to_thread(contact_repo.get_by_phone, chat_id)
                        if existing and agent_handler is not None:
                            notice = await asyncio.to_thread(
                                group_mentions.describe_change, ctype, jids)
                            if notice:
                                # plano 37 (B4): grava o card no canal que recebeu o
                                # evento (todos os outros ramos já usam ev.channel_id),
                                # senão o roster card cairia no inbox 'default'.
                                contact_obj = agent_handler._get_contact(
                                    chat_id, channel_id=ev.channel_id)
                                await asyncio.to_thread(contact_obj.add_message,
                                                        "system_notice", notice)
                                if ws_manager is not None:
                                    await ws_manager.broadcast("new_message", {
                                        "phone": chat_id,
                                        "channel_id": ev.channel_id,
                                        "message": {"role": "system_notice",
                                                    "content": notice, "ts": time.time()}})
                    await emit_with_filter("group.participants_changed", {
                        "chat_id": chat_id, "phone": chat_id.split("@")[0] if chat_id else "",
                        "type": ctype, "jids": jids,
                        "channel_id": ev.channel_id, "ts": time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "group_joined":
                    await emit_with_filter("group.joined", {
                        "chat_id": ev.chat_id,
                        "phone": ev.chat_id.split("@")[0] if ev.chat_id else "",
                        "channel_id": ev.channel_id, "ts": time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "call":
                    await emit_with_filter("call.received", {
                        "call_id": extras.get("call_id", ""), "phone": ev.chat_id,
                        "auto_rejected": bool(extras.get("auto_rejected", False)),
                        "remote_platform": extras.get("remote_platform", ""),
                        "remote_version": extras.get("remote_version", ""),
                        "group_jid": extras.get("group_jid"),
                        "channel_id": ev.channel_id, "ts": time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "newsletter":
                    await emit_with_filter("newsletter.event", {
                        "subtype": extras.get("subtype", ""),
                        "channel_id": ev.channel_id, "ts": time.time(), "raw": ev.raw})
                    handled += 1
                elif kind == "system":
                    # plano 82: a channel LIFECYCLE notice (Cloud API
                    # ``user_changed_number``/``customer_identity_changed``, and the
                    # future Telegram ``migrate_to_chat_id``) — NOT a message from the
                    # counterpart. Surface it as a PANEL-ONLY card (``conversation_event``
                    # role — already black-listed from the LLM context, sidebar preview
                    # and unread badge) on the contact's EXISTING conversation and
                    # NOTHING else: it never materializes a contact, never creates or
                    # reopens a conversation, never runs the agent, and never emits
                    # ``message.saved``/``message.received`` — so automation plugins
                    # (protocolos) never fire. The distinct, opt-in bus event
                    # ``channel.system_event`` is the ONLY hook a plugin gets. Generic
                    # in the core: the provider merely DECLARES ``kind="system"`` (no
                    # ``if provider ==`` here). Idempotency mirrors the ingest funnel
                    # (P18) — a Meta redelivery must not duplicate the card nor re-fire
                    # the bus event.
                    dedup_key = (f"{ev.channel_id}:{ev.external_msg_id}"
                                 if ev.external_msg_id else None)
                    if state is not None and dedup_key:
                        if dedup_key in state.processed_messages:
                            logger.info("[Webhook %s] evento de sistema reentregue "
                                        "(%s) — ignorado", ev.channel_id,
                                        ev.external_msg_id)
                            continue
                        state.processed_messages.add(dedup_key)
                    system_type = extras.get("system_type", "")
                    wa_id = extras.get("wa_id")
                    body = extras.get("body", "")
                    card_text = ev.text or ev.display_text or body
                    # Resolve the EXISTING conversation only — never create one (P2).
                    # get_latest_for_contact returns the most recent thread regardless
                    # of status, and we attach WITHOUT set_status, so a closed
                    # conversation stays closed.
                    existing = await asyncio.to_thread(
                        contact_repo.get_by_phone, ev.chat_id)
                    conv = None
                    if existing:
                        conv = await asyncio.to_thread(
                            conversation_repo.get_latest_for_contact, existing["id"])
                    conv_id = conv["id"] if conv else None
                    if conv and card_text:
                        saved = await asyncio.to_thread(
                            message_repo.add, existing["id"], "conversation_event",
                            card_text, conversation_id=conv_id, msg_id=None,
                            ts=ev.ts or time.time())
                        if ws_manager is not None:
                            await ws_manager.broadcast("new_message", {
                                "phone": ev.chat_id, "channel_id": ev.channel_id,
                                "message": {"role": "conversation_event",
                                            "content": card_text,
                                            "conversation_id": conv_id,
                                            "ts": saved.get("ts")}})
                    else:
                        logger.info("[Webhook %s] evento de sistema (%s) não gerou "
                                    "card (contato=%s, conversa=%s, texto=%s) — "
                                    "plano 82 P2", ev.channel_id, system_type or "?",
                                    bool(existing), bool(conv), bool(card_text))
                    await emit_with_filter("channel.system_event", {
                        "phone": ev.chat_id, "channel_id": ev.channel_id,
                        "system_type": system_type, "wa_id": wa_id, "body": body,
                        "conversation_id": conv_id, "ts": ev.ts or time.time(),
                        "raw": ev.raw})
                    handled += 1
            except Exception:
                logger.warning("Falha ao processar evento %s do canal %s",
                               kind, getattr(ev, "channel_id", "?"), exc_info=True)
        return handled

    @app.get("/api/webhook/{provider}/{channel_id}")
    async def webhook_handshake(provider: str, channel_id: str, request: Request):
        # Cloud API verification handshake (§5.2): echo hub.challenge when the
        # verify_token matches the one stored for this channel.
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and challenge is not None:
            expected = channel_credential_repo.get(channel_id, "verify_token")
            if expected and token == expected:
                return PlainTextResponse(challenge, status_code=200)
            logger.warning("Webhook handshake: verify_token mismatch for %s/%s",
                           provider, channel_id)
            return PlainTextResponse("forbidden", status_code=403)
        return PlainTextResponse("ok", status_code=200)

    @app.post("/api/webhook/{provider}/{channel_id}")
    async def webhook_inbound(provider: str, channel_id: str, request: Request):
        # Must never 500 — a provider that gets an error retries forever.
        # Read the body as RAW BYTES first (plano 46 · 01-A): the Meta signature is
        # an HMAC over the exact bytes, so parsing to a dict and re-serializing
        # would break it. The dict is derived from the same bytes.
        try:
            body_bytes = await request.body()
        except Exception:
            body_bytes = b""
        try:
            raw = json.loads(body_bytes or b"{}")
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

        # Signature verification (plano 46 · 01-A, D3) — opt-in per PROVIDER, never
        # by name: the base ``Channel`` hook returns True, so GOWA/Telegram/Cloud are
        # byte-identical to before. Meta providers override it and validate
        # ``X-Hub-Signature-256`` over the RAW body with the channel's app_secret.
        # Runs BEFORE the plugin filter and the debug buffers, so a forged payload
        # never reaches a plugin nor is recorded. Answers 200 anyway (Meta retries a
        # 4xx forever) but ingests NOTHING. Called INLINE on purpose: the hook's
        # contract is fast + I/O-free (an HMAC), and this is EVERY inbound's hot
        # path — a thread hop per message would cost more than the check itself.
        # Resolved against the URL's channel (the GOWA device re-routing below only
        # matters for GOWA, which never verifies).
        inst = registry.get(channel_id) if registry is not None else None
        if inst is not None:
            try:
                signature_ok = inst.verify_inbound_signature(body_bytes, request.headers)
            except Exception:
                logger.warning("verify_inbound_signature falhou em %s/%s — payload "
                               "descartado", provider, channel_id, exc_info=True)
                signature_ok = False
            if not signature_ok:
                logger.warning("Webhook inbound %s/%s REJEITADO: assinatura inválida",
                               provider, channel_id)
                return _ok({"status": "bad_signature"})

        # Plugin filter: full webhook payload before any parse (plano 13 Fase 0 —
        # same hook the legacy /api/webhook handler offers, now for every provider).
        raw = await apply_filter("filter.webhook.payload", raw, {})
        if raw is None:
            return _ok({"status": "filtered_out"})

        _RECENT.append({"provider": provider, "channel_id": channel_id, "raw": raw})
        del _RECENT[:-_RECENT_CAP]
        # GOWA debug parity: also surface in /api/webhook-payloads.
        if state is not None:
            try:
                state.webhook_payloads.append({
                    "ts": time.time(), "event": raw.get("event", ""),
                    "payload": raw.get("payload", raw.get("data", raw))})
            except Exception:
                pass

        # GOWA delivers every device's inbound to the SAME webhook URL (launched as
        # .../gowa/default), so the URL's channel_id can't tell which number received
        # the message. Resolve the real channel from the GOWA v8 envelope (top-level
        # ``device_id`` = receiving JID, ``session_id`` = registered device string) so
        # a 2nd GOWA number lands in its own inbox/conversation and replies go out its
        # own device. Best-effort: only override when it maps to a DIFFERENT live
        # channel; otherwise keep the URL channel (legacy behaviour) — never worse.
        if provider == "gowa":
            sess = raw.get("session_id") or (raw.get("payload") or {}).get("session_id")
            djid = raw.get("device_id") or (raw.get("payload") or {}).get("device_id")
            resolved = await asyncio.to_thread(
                channel_repo.get_gowa_channel_for_device, sess, djid)
            if (resolved and resolved != channel_id
                    and registry is not None and registry.get(resolved) is not None):
                logger.info("[Webhook gowa] inbound routed by device to channel %r "
                            "(url=%r, session_id=%r, device_id=%r)",
                            resolved, channel_id, sess, djid)
                channel_id = resolved

        row = channel_repo.get(channel_id)
        if row is None:
            # Unknown channel: ack 200 (avoid retries) but record nothing useful.
            logger.warning("Webhook inbound for unknown channel %s/%s", provider, channel_id)
            return _ok({"status": "ignored", "reason": "unknown_channel"})

        events = []
        # Re-resolve: the GOWA device re-routing above may have changed channel_id.
        inst = registry.get(channel_id) if registry is not None else None
        if inst is not None and hasattr(inst, "parse_inbound"):
            try:
                # parse_inbound may do blocking I/O (GOWA resolves group name /
                # archive / filename via its client) — keep it off the event loop.
                events = await asyncio.to_thread(inst.parse_inbound, raw) or []
            except Exception as e:
                logger.warning("parse_inbound failed for %s/%s: %s", provider, channel_id, e)
        logger.info("Webhook inbound %s/%s → %d evento(s) parseado(s)",
                    provider, channel_id, len(events))
        # plano 11 Fase 2: feed the parsed events into the agentic pipeline (same
        # batch orchestrator as GOWA) and re-dispatch reaction/receipt to the bus.
        handled = await _dispatch_events(events)
        return _ok({"status": "received", "events": len(events), "handled": handled})

    @app.get("/api/channel-webhook-payloads")
    async def recent_payloads(request: Request, limit: int = 20):
        # Twin of /webhook-payloads: leaks raw bodies/phones/base64 — gate it.
        denied = permission_denied(request, "settings.manage")
        if denied:
            return denied
        return _ok(_RECENT[-max(1, min(limit, _RECENT_CAP)):])
