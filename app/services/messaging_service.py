"""Outbound messaging pipeline as ONE cohesive service (Plano 23 · Fase B3).

This module owns the OUTBOUND half of the message pipeline that previously lived
as closures inside ``server/routes/webhook.py::register_routes`` and the operator
send handlers in ``server/routes/contacts.py``:

  * the typing-aware batch orchestrator (``_orchestrate`` / ``_run_one_cycle`` /
    ``_schedule_orchestrator`` + the typing-pause guards),
  * ``send_reply`` (split → filter → send → persist → broadcast → ``message.sent``),
  * ``send_media`` (R14 — the unified operator image/audio/document send),
  * audio-transcription delivery + the inbound transcription wrapper,
  * the tool-call broadcast fan-out,
  * the once-per-conversation ``ai_takeover`` emit,
  * ``resolve_channel`` / ``session_window_guard`` (24h-window + sandbox gating),
  * ``error_bubble`` (reuses B2's ``_emit_send_error``),
  * ``broadcast_and_emit`` (the generalized lift of ``conversations._broadcast``).

Branch by Abstraction: the route closures now build ONE :class:`MessagingService`
(via :class:`MessagingContext`, which carries the same infra the closures used to
capture) and delegate to it; the orchestration wiring in ``register_routes`` is
unchanged. The service depends only on ``deps`` / repos / the bus — never on
``server.app``.

Semantics are preserved byte-for-byte (the golden-master characterization suite is
the contract). Methods that were ``async def`` stay async; the per-request
statelessness of the original closures is preserved (the service holds only the
captured infra, never per-conversation state — that lives in ``MessagingState``).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

from channels import ai_settings
from db.repositories import agent_repo, contact_repo, conversation_repo
from agent import group_mentions
from server import sound_catalog, system_notices
from server.execution import (
    astart_execution, aend_execution, atrack_step, prune_executions,
    astamp_execution_channel, aset_execution_texts, set_current_contact_id,
)
from server.helpers import parse_split_reply
from server.transcription import (
    maybe_transcribe,
    format_media_content,
    placeholder_for_unrenderable,
)
from plugins.events import apply_filter, emit_with_filter
from app.services.realtime_broadcast import build_inbound_saved_message

logger = logging.getLogger(__name__)


# ── error bubble (R3 — reused from B2) ────────────────────────────────────────

async def error_bubble(ws_manager, phone: str, content: str) -> None:
    """Broadcast a ``role:'error'`` message card for a failed send (R3 / B2).

    Single place that builds the error-bubble WS payload the panel renders as a
    centered error card. Shape is intentionally fixed (``phone`` + a ``message``
    with ``role``/``content``/``ts``). ``contacts._emit_send_error`` delegates here.
    """
    await ws_manager.broadcast("new_message", {
        "phone": phone,
        "message": {"role": "error", "content": content, "ts": time.time()},
    })


# ── broadcast_and_emit (R-bc — generalized lift of conversations._broadcast) ──

async def broadcast_and_emit(deps, ws_event: str, bus_event: str, payload: dict,
                             *, execution_id: int | None = None) -> None:
    """Emit a lifecycle change as ONE domain event; the WS broadcast is a LISTENER.

    Plano 23 Fase C5 (Contract — single source). This used to do BOTH effects in
    parallel: ``ws_manager.broadcast(ws_event, payload)`` AND
    ``emit_with_filter(bus_event, payload)``. That was the Expand half of the
    Parallel Change (the WS broadcast living next to the bus emit). C5 closes the
    Contract: the domain event (``bus_event``) is now the SINGLE trigger, and the
    panel WS broadcast (``ws_event``) is performed by the core listener in
    ``app.services.ws_projections`` (a SYNCHRONOUS core subscriber that runs INSIDE
    this very emit, so the broadcast is observable with identical timing — the
    frontend gets the same ``ws_event`` + payload as before).

    ``ws_event`` is kept in the signature (and threaded into the payload-free
    projection via the bus→WS table in ``ws_projections``) so the call sites in
    ``conversation_service`` read unchanged; it is no longer broadcast HERE.

    An emit failure never propagates (mirrors the original): the HTTP action or
    pipeline step must not fail because a plugin raised. The WS broadcast inside the
    projection is itself defensively wrapped there too.

    ``execution_id`` is threaded so a caller inside an execution can attribute the
    emit (and the WS broadcast the projection performs synchronously within it) to
    the right ``executions`` row (§1.4). Execution-step tracking keys off the
    contextvar set by ``astart_execution`` (inherited by ``to_thread``), so when
    ``execution_id`` is given we (re)assert it on the contextvar around the emit —
    keeping ``execution_steps`` population correct even if the call happens on a
    fresh task/thread that didn't inherit the contextvar.
    """
    from agent.execution import set_current_execution, get_current_execution_id

    prev = None
    restore = False
    if execution_id is not None:
        prev = get_current_execution_id()
        if prev != execution_id:
            set_current_execution(execution_id)
            restore = True
    try:
        try:
            # The WS broadcast (ws_event) is now a LISTENER of this domain event,
            # performed synchronously inside emit_with_filter by ws_projections.
            await emit_with_filter(bus_event, payload)
        except Exception as e:
            logger.debug("bus emit %s (ws %s) failed: %s", bus_event, ws_event, e)
    finally:
        if restore:
            set_current_execution(prev)


# ── service context ───────────────────────────────────────────────────────────

@dataclass
class MessagingContext:
    """Infra the outbound pipeline needs, lifted out of the ``register_routes``
    closures so :class:`MessagingService` can be constructed once and delegated to.

    Carries exactly what the webhook closures used to capture; no ``server.app``
    import. ``channel_ai_enabled`` is the per-request master-AI gate (global
    ``auto_reply`` + the channel's ``ai_enabled``) — kept as a callable so the
    resolution lives next to the webhook ``settings``/``ai_settings`` wiring.
    """

    deps: object
    agent_handler: object
    ws_manager: object
    state: object
    settings: object
    outbound: object
    channel_ai_enabled: object  # Callable[[str], bool]


class MessagingService:
    """The outbound pipeline (see module docstring). Stateless per call: holds only
    the captured infra (``MessagingContext``); per-conversation state lives in
    ``ctx.state.messaging`` (:class:`server.state.MessagingState`)."""

    def __init__(self, ctx: MessagingContext):
        self.ctx = ctx

    # Convenience accessors (read-only) so method bodies read like the originals.
    @property
    def _deps(self):
        return self.ctx.deps

    @property
    def agent_handler(self):
        return self.ctx.agent_handler

    @property
    def ws_manager(self):
        return self.ctx.ws_manager

    @property
    def state(self):
        return self.ctx.state

    @property
    def settings(self):
        return self.ctx.settings

    @property
    def outbound(self):
        return self.ctx.outbound

    def _channel_ai_enabled(self, channel_id: str) -> bool:
        return self.ctx.channel_ai_enabled(channel_id)

    # ── error bubble ──────────────────────────────────────────────────────────

    async def error_bubble(self, phone: str, content: str) -> None:
        await error_bubble(self.ws_manager, phone, content)

    # ── Operator media send (R14 — unifies image/audio/document) ──────────────

    async def send_media(self, *, channel_id: str, phone: str, kind: str,
                         dest, is_sandbox: bool, content: str,
                         emit_text: str, caption: str = "",
                         filename: str | None = None,
                         error_label: str,
                         transcribe_audio: bool = False,
                         sent_by_user_id: int | None = None,
                         sent_by_name: str | None = None) -> dict:
        """Send an operator-uploaded media file and persist/broadcast/emit it (R14).

        UNIFIES the three near-duplicate operator send handlers
        (``send-image`` / ``send-audio`` / ``send-document``) that lived in
        ``contacts.py``. The route still owns the parts that genuinely differ by
        endpoint and reference contacts-local closures (permission/inbox deny,
        sandbox detection, channel resolution, 24h-window block, writing the
        upload to ``dest`` with the kind-specific filename scheme); this method
        owns the IDENTICAL send tail:

          send (skipped for sandbox) → mark the external id processed → build the
          ``assistant`` msg row → ``contact.add_message`` → broadcast ``new_message``
          → emit ``message.sent`` (source=operator) → [audio only] transcribe +
          deliver a private ``transcription`` card.

        The per-kind variation is parameterised: ``content`` (the persisted/broadcast
        body), ``emit_text`` (the ``message.sent`` text — caption for image/document,
        ``""`` for audio), ``caption`` + ``filename`` (forwarded to the channel's
        media send), ``error_label`` (PT-BR wording), and ``transcribe_audio`` (the
        audio-only tail). ``dest`` is the already-written ``Path``.

        Returns ``{"ok": True, "msg_id": ..., "media_path": ...}`` on success or
        ``{"ok": False, "error": ..., "kind": "send"|"unexpected"}`` when the send
        failed (the error bubble was already broadcast). The route maps that to the
        same ``_err``/``_ok`` envelopes it returned before.
        """
        from gowa.client import GOWASendError

        outbound = self.outbound
        ws_manager = self.ws_manager
        state = self.state
        agent_handler = self.agent_handler

        msg_id = None
        try:
            if not is_sandbox:
                res = await asyncio.to_thread(
                    outbound.send_media, channel_id, phone, kind, str(dest),
                    caption=caption, filename=filename)
                if not res.ok:
                    raise GOWASendError(res.error or "Falha no envio de mídia")
                msg_id = res.external_msg_id or ""
        except GOWASendError as e:
            logger.error("[Send] Failed to send %s to %s: %s", kind, phone, e)
            await self.error_bubble(phone, f"Falha ao enviar {error_label}: {e}")
            return {"ok": False, "error": str(e), "kind": "send"}
        except Exception as e:
            logger.error("[Send] Failed to send %s to %s: %s", kind, phone, e)
            await self.error_bubble(
                phone, f"Erro inesperado ao enviar {error_label}: {e}")
            return {"ok": False, "error": str(e), "kind": "unexpected"}

        # msg_id is the channel external id (None for sandbox). Mark it processed so
        # the webhook ignores the WhatsApp echo of our own message.
        if msg_id:
            state.processed_messages.add(msg_id)

        rel_path = f"statics/outbox/{dest.name}"
        msg_data = {
            "role": "assistant",
            "content": content,
            "ts": time.time(),
            "media_type": kind,
            "media_path": rel_path,
            "status": "operator",
            "msg_id": msg_id,
            "sent_by_name": sent_by_name,
        }
        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        # Regra "ignorar abertura" (plugin): mantém a conversa fechada se a legenda casar
        # a regex (consistente com on_outbound, que já pula o protocolo). Sem plugin → None.
        _allow_reopen = await apply_filter(
            "filter.conversation.before_reopen", True,
            {"phone": phone, "role": "assistant", "text": emit_text})
        contact.add_message("assistant", content, media_type=kind,
                            media_path=rel_path, status="operator", msg_id=msg_id,
                            sent_by_user_id=sent_by_user_id, sent_by_name=sent_by_name,
                            reopen=(False if not _allow_reopen else None))

        await ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": channel_id, "message": msg_data})
        await emit_with_filter("message.sent", {
            "phone": phone, "text": emit_text, "msg_id": msg_id,
            "media_type": kind, "media_path": rel_path,
            "source": "operator", "status": "operator",
            "ts": time.time(),
        })

        if transcribe_audio:
            # Transcribe the operator-sent audio when enabled (audio_transcription_mode
            # in sent/both) so the panel/AI can read what was said — same private card
            # used for inbound media. Defensive: a transcription failure never breaks
            # the send (the audio was already delivered above).
            transcription = await maybe_transcribe(
                "audio", str(dest),
                settings=self.settings, agent_handler=agent_handler,
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

        return {"ok": True, "msg_id": msg_id, "media_path": rel_path}

    # ── Reply Splitting & Sending ─────────────────────────────────────────────

    async def send_reply(self, channel_id: str, phone: str, reply: str, *,
                         agent_key: str | None = None):
        """Send reply (possibly split into multiple parts) and broadcast.

        Channel-aware (plano 11): every leg goes through ``OutboundRouter`` so the
        reply lands on the conversation's own channel. Presence / @mentions are
        gated by ``ChannelCapabilities`` — a Cloud channel skips them silently.
        """
        outbound = self.outbound
        ws_manager = self.ws_manager
        state = self.state
        settings = self.settings
        agent_handler = self.agent_handler

        caps = outbound.capabilities(channel_id)
        is_group_target = caps.groups and "@g.us" in phone

        # Plugin filter: full raw reply before split
        reply = await apply_filter("filter.reply.raw", reply, {"phone": phone})
        if reply is None:
            logger.info("[Batch] reply for %s aborted by filter.reply.raw", phone)
            return

        # Nome exibível do agente (resolvido 1× por resposta) para "IA - <NOME>".
        agent_name = await asyncio.to_thread(agent_repo.display_name_for, agent_key)

        split_enabled = ai_settings.value(
            channel_id, "split_messages", settings.get("split_messages", True))

        if split_enabled:
            parts = parse_split_reply(reply)
        else:
            parts = [reply]

        # Plugin filter: list of parts (can add/remove/reorder)
        parts = await apply_filter("filter.reply.parts", parts, {"phone": phone})
        if parts is None or not parts:
            logger.info("[Batch] reply for %s aborted by filter.reply.parts", phone)
            return

        # Initial response delay (simulates typing)
        delay_min = settings.get("response_delay_min", 1.0)
        delay_max = settings.get("response_delay_max", 3.0)
        await asyncio.sleep(random.uniform(delay_min, delay_max))

        sent_parts = []  # collect (part_text, msg_id) for saving after send
        for i, part in enumerate(parts):
            # Plugin filter: each part right before send (signature, formatting, redact)
            part = await apply_filter(
                "filter.reply.part", part,
                {"phone": phone, "index": i, "total": len(parts)},
            )
            if part is None:
                logger.info("[Batch] part %d for %s skipped by filter.reply.part", i + 1, phone)
                continue

            if i > 0:
                # Inter-message delay with ±0.5s variation
                base_delay = ai_settings.value(
                    channel_id, "split_message_delay",
                    settings.get("split_message_delay", 2.0))
                if base_delay > 0:
                    await asyncio.sleep(base_delay + random.uniform(-0.5, 0.5))
                # Re-send typing indicator between parts (capability-gated)
                await asyncio.to_thread(outbound.send_presence, channel_id, phone, "composing")

            # Resolve @Name / @todos -> real mentions for group targets. We keep
            # `part` (friendly @Name) for save/broadcast and send `send_text`
            # (inline @<number>) + mentions on the wire. Only for channels that
            # support groups (Cloud/Telegram-1:1 skip this).
            send_text, mentions = part, None
            if is_group_target:
                send_text, mentions = await asyncio.to_thread(
                    group_mentions.resolve_outgoing, phone, part)

            # Track for echo-back filtering (key on the wire text we actually send)
            sent_key = f"{channel_id}:{phone}:{send_text[:120]}"
            state.recently_sent[sent_key] = time.time()

            send_result = await asyncio.to_thread(
                outbound.send_text, channel_id, phone, send_text,
                reply_to=None, mentions=mentions)
            if not send_result.ok:
                err = send_result.error or "envio falhou"
                logger.error("[Batch] Send failed for %s/%s (part %d/%d): %s",
                             channel_id, phone, i + 1, len(parts), err)
                await atrack_step("channel_send", {
                    "channel_id": channel_id, "phone": phone, "part": i + 1, "error": err,
                }, status="error")
                await asyncio.to_thread(outbound.send_presence, channel_id, phone, "paused")
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "message": {"role": "error", "content": f"Falha ao enviar: {err}", "ts": time.time()},
                })
                return
            await atrack_step("channel_send", {
                "channel_id": channel_id, "phone": phone,
                "part": i + 1, "total_parts": len(parts)})

            part_msg_id = send_result.external_msg_id or ""
            sent_parts.append((part, part_msg_id))

            # Broadcast each part to frontend individually
            _ai_msg = {"role": "assistant", "content": part, "ts": time.time(),
                       "status": "sent", "msg_id": part_msg_id}
            if agent_key:
                _ai_msg["agent_key"] = agent_key
            if agent_name:
                _ai_msg["agent_name"] = agent_name
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "channel_id": channel_id,
                "message": _ai_msg,
            })

            # Plugin event: AI reply leg
            await emit_with_filter("message.sent", {
                "phone": phone, "channel_id": channel_id, "text": part, "msg_id": part_msg_id,
                "media_type": None, "media_path": None,
                "source": "ai", "status": "sent",
                "ts": time.time(),
            })

        # Save each part as a separate message to preserve split across page refresh
        # plano 51 (01 F1): estampa a execução do turno em cada parte — o contextvar
        # é lido AQUI (contexto async do ciclo) e passado por valor ao to_thread.
        from agent.execution import get_current_execution_id
        turn_execution_id = get_current_execution_id()
        for part, part_msg_id in sent_parts:
            try:
                await asyncio.to_thread(agent_handler.save_assistant_message, phone, part,
                                        msg_id=part_msg_id, status="sent",
                                        channel_id=channel_id, agent_key=agent_key,
                                        execution_id=turn_execution_id)
                # Increment unread AI count (operator hasn't seen this reply yet)
                contact = agent_handler._get_contact(phone, channel_id=channel_id)
                if contact:
                    await asyncio.to_thread(contact.increment_unread_ai)
            except Exception as e:
                logger.error("[Batch] Failed to save reply for %s: %s", phone, e)

        await asyncio.to_thread(outbound.send_presence, channel_id, phone, "paused")
        state.msg_count += 1
        full_reply = "\n".join(parts)
        await atrack_step("response_sent", {
            "phone": phone,
            "channel_id": channel_id,
            "parts": len(parts),
            "reply_preview": full_reply[:200],
        })
        # Nexus plan: denormalize the final AI reply for the "Msg da IA" search.
        await aset_execution_texts(output_text=full_reply[:2000] or None)
        logger.info("[Batch] Replied to %s/%s (%d parts): %s",
                    channel_id, phone, len(parts), full_reply[:80])

        await ws_manager.broadcast("status", {
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
            "bot_phone": state.bot_phone,
            "bot_name": state.bot_name,
        })

    async def broadcast_tool_calls(self, phone: str, tool_calls: list[dict],
                                   contact_info: dict | None = None,
                                   *, channel_id: str = "default",
                                   agent_key: str | None = None):
        """Broadcast private messages for each tool call executed by the LLM.

        ``agent_key`` (do ProcessResult do turno) atribui os cards de tool ao agente
        que os executou, para o painel exibir "Ferramenta IA - <NOME>"."""
        ws_manager = self.ws_manager
        agent_handler = self.agent_handler
        settings = self.settings
        # Nome exibível do agente (resolvido 1× para todos os cards deste turno).
        agent_name = await asyncio.to_thread(agent_repo.display_name_for, agent_key)

        contact = agent_handler._get_contact(phone, channel_id=channel_id)
        for tc in tool_calls:
            tool_name = tc.get("tool", "unknown")
            args = tc.get("args", {})
            # Format: tool name + each arg on its own line
            lines = [f"\U0001f527 {tool_name}"]
            for key, value in args.items():
                lines.append(f"{key}: {value}")
            # Reflect the tool OUTCOME (success detail / error / where it saved)
            # instead of always implying success (plano 19). None-returning tools
            # (e.g. save_contact_info) add no line — the card stays as before.
            result = tc.get("result")
            if isinstance(result, str) and result.strip():
                lines.append(f"→ {result.strip()}")
            content = "\n".join(lines)

            # The live payload mirrors the SAVED row (private_note pattern in
            # server/routes/contacts.py): ``add_message`` resolves the
            # conversation inbox-aware, so its ``conversation_id`` is the thread
            # the panel has open. Resolving via ``get_open_for_contact`` here
            # returned the newest open conversation of ANY channel — with two
            # open threads the id diverged and the panel dropped the card. The
            # saved ``ts`` also keeps the panel's ts+role dedupe from colliding.
            saved = None
            try:
                saved = await asyncio.to_thread(
                    contact.add_message, "tool_call", content, agent_key=agent_key)
            except Exception as e:
                logger.error("[ToolCall] failed to save tool_call card for %s: %s",
                             phone, e)
            tc_message = {
                "role": "tool_call",
                "content": content,
                "ts": (saved or {}).get("ts", time.time()),
            }
            if agent_key:
                tc_message["agent_key"] = agent_key
            if agent_name:
                tc_message["agent_name"] = agent_name
            if saved and saved.get("conversation_id") is not None:
                tc_message["conversation_id"] = saved["conversation_id"]
            if saved and saved.get("id"):
                tc_message["_id"] = saved["id"]
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "channel_id": channel_id,
                "message": tc_message,
            })

        # Live-refresh the open info panel after a custom-attribute write (plano 19).
        # set_custom_attribute doesn't populate contact_info, so without this the
        # "Dados do contato" / conversation panel only updates on reopen.
        attr_scopes = {
            ((tc.get("args") or {}).get("scope") or "contact")
            for tc in tool_calls
            if tc.get("tool") == "set_custom_attribute" and not tc.get("skipped")
        }
        if "contact" in attr_scopes:
            full = await asyncio.to_thread(contact_repo.get_full_contact, phone)
            if full:
                await ws_manager.broadcast("contact_info_updated", {
                    "phone": phone, "info": full.get("info")})
        if "conversation" in attr_scopes:
            conv = await asyncio.to_thread(conversation_repo.get_open_for_contact_scoped, contact)
            if conv:
                await ws_manager.broadcast("conversation_updated", {
                    "conversation_id": conv["id"], "contact_id": contact.id,
                    "fields": {"custom_attributes": conv.get("custom_attributes")},
                    "ts": time.time()})

        # Broadcast updated contact info so the frontend refreshes name/details
        if contact_info:
            logger.info("[ToolCall] Broadcasting contact_info_updated for %s: %s", phone, contact_info)
            await ws_manager.broadcast("contact_info_updated", {
                "phone": phone,
                "info": contact_info,
            })

        # If transfer_to_human was called, broadcast alert + state updates
        if any(tc.get("tool") == "transfer_to_human" for tc in tool_calls):
            # Alerta sonoro GLOBAL: ativação/duração moram no padrão da equipe
            # (``config.sound_settings`` → evento ``ia_to_human``, editável na aba
            # "Notificações e sons"), com fallback nas keys legadas. Deixou de ser
            # per-canal — o alerta é sobre QUEM recebe, não sobre o canal.
            ta_enabled, ta_duration = sound_catalog.event_gate(
                settings.get("sound_settings", None), "ia_to_human",
                legacy_enabled=settings.get("transfer_alert_enabled", True),
                legacy_duration=settings.get("transfer_alert_duration", 5))
            await ws_manager.broadcast("human_transfer_alert", {
                "phone": phone, "enabled": ta_enabled, "duration": ta_duration})
            await ws_manager.broadcast("contact_ai_toggled", {
                "phone": phone,
                "ai_enabled": False,
            })
            await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
            await ws_manager.broadcast("contact_tags_updated", {
                "phone": phone,
                "tags": list(contact.tags),
            })
            # The conversation was unassigned by the tool — push the change so the
            # row moves to the "Não atribuídas" inbox live (plano 10).
            conv = await asyncio.to_thread(
                conversation_repo.get_open_for_contact_scoped, contact)
            if conv:
                await ws_manager.broadcast("conversation_assigned", {
                    "conversation_id": conv["id"],
                    "contact_id": contact.id,
                    "status": conv.get("status"),
                    "assignee_user_id": None,
                    "active_agent_key": None,
                    "ai_active": conv.get("ai_active"),
                    "ts": time.time(),
                })
                # Card "IA pausada" no fio: a IA se auto-desligou ao transferir para
                # humano. Sem ``actor`` (não foi um operador) → texto genérico. Gated
                # pelo grupo ``system_notice_ai``; best-effort (emit engole exceções).
                await asyncio.to_thread(
                    system_notices.emit_conversation_notice,
                    event_type="ai_off", conversation_id=conv["id"],
                    contact_id=contact.id, phone=phone)

    # ── Audio Transcription Delivery ──────────────────────────────────────────

    async def deliver_audio_transcription(self, phone: str, contact, transcription: str,
                                          *, channel_id: str = "default"):
        """Deliver an audio transcription based on the configured target.

        target=private → save as 'transcription' role (operator-only card in the panel)
        target=chat    → send a new WhatsApp message with the configured prefix
        """
        outbound = self.outbound
        ws_manager = self.ws_manager
        state = self.state
        settings = self.settings

        target = ai_settings.value(
            channel_id, "audio_transcription_target",
            settings.get("audio_transcription_target", "private"))

        if target == "chat":
            chat_prefix = ai_settings.value(
                channel_id, "audio_transcription_chat_prefix",
                settings.get("audio_transcription_chat_prefix", "")) or ""
            chat_message = f"{chat_prefix}{transcription}" if chat_prefix else transcription
            # Suppress echo-back for the message we're about to send
            sent_key = f"{channel_id}:{phone}:{chat_message[:120]}"
            state.recently_sent[sent_key] = time.time()
            send_result = await asyncio.to_thread(
                outbound.send_text, channel_id, phone, chat_message)
            if send_result.ok:
                sent_msg_id = send_result.external_msg_id or ""
                await asyncio.to_thread(
                    contact.add_message, "assistant", chat_message,
                    msg_id=sent_msg_id, status="operator")
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "channel_id": channel_id,
                    "message": {
                        "role": "assistant",
                        "content": chat_message,
                        "ts": time.time(),
                        "status": "operator",
                        "msg_id": sent_msg_id,
                    },
                })
                return
            logger.error("[Webhook] Failed to send transcription to chat for %s: %s",
                         phone, send_result.error)
            state.recently_sent.pop(sent_key, None)
            # Fall through to private so the transcription is not lost.

        # private target (or fallback after a failed chat send)
        await asyncio.to_thread(contact.add_message, "transcription", transcription)
        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "message": {
                "role": "transcription",
                "content": transcription,
                "ts": time.time(),
            },
        })

    async def maybe_transcribe(
        self,
        media_kind: str,            # "audio" | "image" | "document"
        path: str,
        *,
        phone: str,
        source: str,                # "batch" | "echo" | "operator" | "private" | "group_no_mention"
        is_group: bool = False,
        group_jid: str | None = None,
        file_name: str = "",        # document only — original filename
        mimetype: str = "",         # document only — best-effort mime hint
        channel_id: str = "default",
        force: bool = False,        # bypass the config gate (still honors plugin should_run)
    ) -> str:
        """Inbound-path wrapper around the shared transcription helper.

        Delegates to ``server.transcription.maybe_transcribe`` so the gate +
        plugin hooks (``filter.transcription.should_run`` / ``.result``) live in
        one place, shared with the operator send routes. The transcription gates
        (describe image / read document / audio mode) are resolved PER CHANNEL
        (plano 21) via a settings view that overlays the channel's overrides.
        """
        return await maybe_transcribe(
            media_kind, path,
            settings=ai_settings.view(channel_id, self.settings),
            agent_handler=self.agent_handler,
            phone=phone, source=source, is_group=is_group, group_jid=group_jid,
            file_name=file_name, mimetype=mimetype, force=force,
        )

    # ── Typing-Aware Orchestrator ─────────────────────────────────────────────

    async def _wait_typing_paused(self, channel_id: str, phone: str, max_wait: float = 30.0):
        """Block while the contact is typing/recording. Defensive timeout to avoid hangs.

        WhatsApp emits a single `composing` event when the user starts typing and a
        `paused` event when they stop — there is no heartbeat in between. The stale
        check below is a fallback for cases where `paused` never arrives (dropped
        connection, app killed, etc.) — set generously so genuine long typing isn't cut.
        Keyed by (channel_id, phone); channels without presence simply never set it.
        """
        state = self.state
        key = (channel_id, phone)
        start = time.time()
        while True:
            ts = state.typing_state.get(key)
            if not ts or not ts.get("active"):
                return
            # No event for 25s → assume paused (defensive)
            if time.time() - ts.get("last_ts", 0) > 25:
                logger.info("[Orchestrator] %s typing event stale, assuming paused", phone)
                state.typing_state[key] = {**ts, "active": False}
                return
            if time.time() - start > max_wait:
                logger.warning("[Orchestrator] %s typing wait timeout %.1fs", phone, max_wait)
                state.typing_state[key] = {**ts, "active": False}
                return
            await asyncio.sleep(0.3)

    async def _send_with_typing_guard(self, channel_id: str, phone: str, reply: str, *,
                                      agent_key: str | None = None):
        """Wait for contact to stop typing, mark sending=True, then send (uncancellable phase)."""
        state = self.state
        key = (channel_id, phone)
        await self._wait_typing_paused(channel_id, phone)
        state.sending[key] = True
        try:
            await self.send_reply(channel_id, phone, reply, agent_key=agent_key)
        finally:
            state.sending[key] = False

    async def maybe_emit_ai_takeover(self, phone: str, channel_id: str):
        """Emit 'A IA assumiu o atendimento' once per conversation (plano 12 §3.3).

        Deduped por conversa: ``has_event`` checa se o card já existe no fio. Gateado
        pela config (grupo ``ai``). Best-effort — nunca quebra o envio da resposta.

        plano 23 Fase C0: além do card painel-only, promove ``conversation.ai_takeover``
        a evento de domínio no bus de plugins (1×/conversa, preservando o dedupe — o
        emit só dispara quando o card ainda não existia).
        """
        agent_handler = self.agent_handler

        def _emit():
            contact = agent_handler._get_contact(phone, channel_id=channel_id)
            if contact is None:
                return None
            conv = conversation_repo.get_open_for_contact_scoped(contact)
            if conv is None or system_notices.has_event(conv["id"], "ai_takeover"):
                return None
            system_notices.emit_conversation_notice(
                event_type="ai_takeover", conversation_id=conv["id"],
                contact_id=contact.id, phone=phone)
            return {"conversation_id": conv["id"],
                    "agent_key": conv.get("active_agent_key")}
        try:
            fired = await asyncio.to_thread(_emit)
            if fired is not None:
                from domain.events import emit_domain, ConversationAiTakeover
                await emit_domain(ConversationAiTakeover(
                    conversation_id=fired["conversation_id"],
                    agent_key=fired["agent_key"],
                ))
        except Exception:
            logger.debug("[Webhook] ai_takeover notice failed for %s", phone)

    def schedule_orchestrator(self, channel_id: str, phone: str):
        """Cancel existing orchestrator (unless mid-send) and spawn a new one."""
        state = self.state
        key = (channel_id, phone)
        existing = state.processing_tasks.get(key)
        if existing and not existing.done():
            if state.sending.get(key) or state.processing.get(key):
                # Mid-send (state.sending) OR mid-cycle (state.processing — the batch
                # was already popped, plano 33 F6). Don't cancel: cancelling here
                # discards the popped items → the customer's message is lost from the
                # DB too. The new message stays in pending_messages and the running
                # orchestrator's tail re-checks pending and spawns a follow-up cycle.
                return
            existing.cancel()
        state.processing_tasks[key] = asyncio.create_task(self._orchestrate(channel_id, phone))

    async def _run_one_cycle(self, channel_id: str, phone: str, items: list[dict]):
        """One processing cycle: text batch (single LLM call) + each media item separately.

        Cancellable via task.cancel() up until the SEND phase, which is guarded by
        state.sending[(channel_id, phone)]=True so the webhook does not interrupt mid-send.
        """
        agent_handler = self.agent_handler
        ws_manager = self.ws_manager
        outbound = self.outbound
        settings = self.settings

        exec_id = await astart_execution(phone, "webhook")
        try:
            await atrack_step("webhook_received", {
                "phone": phone,
                "items": [
                    {k: v for k, v in it.items() if k != "audio_path" or v}
                    for it in items
                ],
            })

            contact = agent_handler._get_contact(phone, channel_id=channel_id)

            # plano 36: stamp conversation_id + channel onto the execution (best-effort)
            # now that the contact/inbox is materialised. Cheap read; failure → NULL.
            await astamp_execution_channel(contact, channel_id)
            # Nexus plan: expose the contact of this turn on the contextvar so a deep
            # call (e.g. the vendas_ia plugin recording embedding usage) can attribute
            # cost to it without threading the id through every layer.
            try:
                if contact is not None and getattr(contact, "id", None):
                    set_current_contact_id(contact.id)
            except Exception:
                pass

            text_parts: list[str] = []
            text_msg_ids: list[str] = []
            text_reply_to: str | None = None
            media_items: list[dict] = []
            for item in items:
                if (item.get("image_path") or item.get("audio_path")
                        or item.get("media_type")):
                    media_items.append(item)
                else:
                    text_parts.append(item.get("text", ""))
                    if item.get("msg_id"):
                        text_msg_ids.append(item["msg_id"])
                    # Best-effort: the combined batch quotes the last quoted item.
                    if item.get("reply_to_msg_id"):
                        text_reply_to = item["reply_to_msg_id"]

            combined_preview = "\n".join(t for t in text_parts if t)
            await atrack_step("batch_accumulated", {
                "text_count": len(text_parts),
                "media_count": len(media_items),
                "combined_preview": combined_preview[:200],
            })
            # Nexus plan: denormalize the client message + origin msg_id onto the
            # execution row so the list can search/filter without scanning steps.
            await aset_execution_texts(
                input_text=combined_preview[:2000] or None,
                msg_id=(text_msg_ids[-1] if text_msg_ids else None),
            )

            # plano 25 Fase 1: only the AI auto-marking-read (+ real read-receipt) when
            # it is actually TAKING OVER this conversation. The gate needs all three AI
            # layers (plano 21): global auto_reply + channel ai_enabled (_channel_ai_enabled)
            # AND the per-conversation ai_active flag (_conversation_ai_active) — mirror of
            # the AI-reply gate at the text/media branches below. Without the per-conversation
            # check, an IA-OFF conversation would still clear the unread badge and send a
            # bogus "read"/blue-tick to the client on channels that support it.
            if self._channel_ai_enabled(channel_id) and _conversation_ai_active(contact):
                msg_ids = await asyncio.to_thread(contact.mark_user_messages_as_read)
                if msg_ids:
                    for mid in msg_ids:
                        await asyncio.to_thread(outbound.mark_read, channel_id, phone, mid)
                    await ws_manager.broadcast("messages_read", {"phone": phone, "only_user": True})

            # ── Text batch ──────────────────────────────────
            if text_parts:
                combined = "\n".join(t for t in text_parts if t)
                if combined:
                    logger.info("[Batch] Processing %d text messages from %s: %s",
                                len(text_parts), phone, combined[:80])
                    last_msg_id = text_msg_ids[-1] if text_msg_ids else None
                    # Regra "ignorar abertura" (plugin): mantém a conversa fechada se a
                    # mensagem recebida casar a regex (ela ainda foi salva/exibida). Sem
                    # plugin registrado → apply_filter devolve True → reopen=None (default).
                    _allow_reopen = await apply_filter(
                        "filter.conversation.before_reopen", True,
                        {"phone": phone, "role": "user", "text": combined})
                    saved = contact.add_message("user", combined, msg_id=last_msg_id,
                                        reply_to_msg_id=text_reply_to,
                                        reopen=(False if not _allow_reopen else None))
                    # plano 57: re-emite um new_message AUTORITATIVO pós-save (com o _id/ts
                    # reais da linha) — fecha a janela "broadcast-antes-do-save" em que a 1ª
                    # mensagem de uma conversa nova (ou quem abre na janela t=0↔save) nunca
                    # renderiza ao vivo. `supersedes` = os msg_ids que o batch combinou nesta
                    # única linha, p/ o front colapsar as bolhas otimistas das anteriores.
                    # Defensivo: nunca quebra o save/IA.
                    try:
                        await ws_manager.broadcast("new_message", {
                            "phone": phone, "channel_id": channel_id,
                            "message": build_inbound_saved_message(saved, supersedes=text_msg_ids),
                        })
                    except Exception:
                        logger.exception("[Batch] falha ao re-emitir new_message pós-save para %s", phone)
                    await emit_with_filter("message.saved", {
                        "phone": phone, "text": combined, "msg_id": last_msg_id,
                        "media_type": None, "media_path": None,
                        "is_group": contact.is_group,
                        "source": "batch_text",
                        "ts": time.time(),
                    })
                    if self._channel_ai_enabled(channel_id) \
                            and _conversation_ai_active(contact):
                        if not agent_handler.api_key:
                            notice = "[WhatsBot] API key não configurada."
                            contact.add_message("system_notice", notice)
                            await ws_manager.broadcast("new_message", {
                                "phone": phone,
                                "message": {"role": "system_notice", "content": notice, "ts": time.time()},
                            })
                        else:
                            try:
                                await asyncio.to_thread(outbound.send_presence, channel_id, phone, "composing")
                                # Signal the panel the AI is working on this chat (cleared in the cycle's finally).
                                await ws_manager.broadcast("ai_typing", {"phone": phone, "channel_id": channel_id, "active": True})
                                # Cancellable LLM call
                                result = await agent_handler.aprocess_message(
                                    phone, combined,
                                    save_user_message=False, save_response=False,
                                    channel_id=channel_id)
                                if result.tool_calls:
                                    await self.broadcast_tool_calls(phone, result.tool_calls, result.contact_info, channel_id=channel_id, agent_key=result.agent_key)
                                if result.reply:
                                    if result.reply.startswith("[WhatsBot]"):
                                        contact.add_message("system_notice", result.reply)
                                        await ws_manager.broadcast("new_message", {
                                            "phone": phone,
                                            "message": {"role": "system_notice", "content": result.reply, "ts": time.time()},
                                        })
                                    else:
                                        await self._send_with_typing_guard(channel_id, phone, result.reply, agent_key=result.agent_key)
                                        await self.maybe_emit_ai_takeover(phone, channel_id)
                                elif not result.aborted:
                                    # A5 (plano 31 F4): reply vazio calava sem rastro —
                                    # loga e grava um card painel-only pro operador ver.
                                    # aborted=True (filter/resolução) NÃO gera card: é
                                    # silêncio intencional ou já sinalizado.
                                    logger.warning(
                                        "[Batch] IA não produziu resposta para %s "
                                        "(nada enviado — possível max_tokens baixo)", phone)
                                    try:
                                        empty_msg = contact.add_message(
                                            "error",
                                            "⚠️ A IA não produziu resposta para a última "
                                            "mensagem. Verifique o max_tokens do agente "
                                            "(modelos de raciocínio precisam de orçamento alto).")
                                        await ws_manager.broadcast("new_message", {
                                            "phone": phone, "channel_id": channel_id,
                                            "message": empty_msg,
                                        })
                                    except Exception:
                                        logger.exception(
                                            "[Batch] Falha ao gravar card de resposta "
                                            "vazia para %s", phone)
                            except asyncio.CancelledError:
                                raise
                            except Exception as e:
                                logger.error("[Batch] Agent error for %s: %s", phone, e)
                                await atrack_step("error", {"error": str(e), "phase": "text_processing"}, status="error")

            # ── Media items (each handled individually) ─────
            for item in media_items:
                text = item.get("text", "")
                image_path = item.get("image_path")
                audio_path = item.get("audio_path")
                document_path = (item.get("media_path")
                                 if item.get("media_type") == "document" else None)
                doc_extras = item.get("media_extras") or {}

                # media_type/media_path resolved by _extract_media; fall back to
                # the image/audio paths for items predating the typed fields.
                media_label = item.get("media_type") or ("image" if image_path else "audio")
                logger.info("[Batch] Processing %s from %s", media_label, phone)

                _saved_text = text or ("[Áudio recebido]" if audio_path else "")
                _saved_media_type = item.get("media_type") or ("image" if image_path else "audio")
                _saved_media_path = item.get("media_path") or image_path or audio_path
                # plano 75 F3 — safety net: a media_type the panel cannot draw and
                # with no file would persist an empty body (mute bubble). Real media
                # without a caption keeps its empty text (it has media_path).
                _saved_text = placeholder_for_unrenderable(
                    _saved_text, _saved_media_type, _saved_media_path)
                saved = contact.add_message(
                    "user", _saved_text,
                    media_type=_saved_media_type,
                    media_path=_saved_media_path,
                    msg_id=item.get("msg_id"),
                    reply_to_msg_id=item.get("reply_to_msg_id"),
                )
                # plano 57: new_message autoritativo pós-save (cada mídia é 1 linha própria,
                # com seu próprio msg_id → reconcilia no lugar; sem supersedes).
                try:
                    await ws_manager.broadcast("new_message", {
                        "phone": phone, "channel_id": channel_id,
                        "message": build_inbound_saved_message(saved),
                    })
                except Exception:
                    logger.exception("[Batch] falha ao re-emitir new_message (mídia) pós-save para %s", phone)
                await emit_with_filter("message.saved", {
                    "phone": phone, "text": _saved_text,
                    "msg_id": item.get("msg_id"),
                    "media_type": _saved_media_type,
                    "media_path": _saved_media_path,
                    "media_extras": item.get("media_extras"),
                    "is_group": contact.is_group,
                    "source": "batch_media",
                    "ts": time.time(),
                })

                transcription = ""
                if audio_path:
                    transcription = await self.maybe_transcribe(
                        "audio", audio_path,
                        phone=phone, source="batch",
                        is_group=contact.is_group,
                        group_jid=phone if contact.is_group else None,
                        channel_id=channel_id,
                    )
                elif image_path:
                    transcription = await self.maybe_transcribe(
                        "image", image_path,
                        phone=phone, source="batch",
                        is_group=contact.is_group,
                        group_jid=phone if contact.is_group else None,
                        channel_id=channel_id,
                    )
                elif document_path:
                    transcription = await self.maybe_transcribe(
                        "document", document_path,
                        phone=phone, source="batch",
                        is_group=contact.is_group,
                        group_jid=phone if contact.is_group else None,
                        file_name=doc_extras.get("file_name") or "",
                        mimetype=doc_extras.get("mimetype") or "",
                        channel_id=channel_id,
                    )

                if transcription:
                    if audio_path:
                        new_content = format_media_content("audio", transcription)
                    elif image_path:
                        new_content = format_media_content("image", transcription, text)
                    elif document_path:
                        new_content = format_media_content("document", transcription, text)
                    else:
                        new_content = None
                    if new_content:
                        await asyncio.to_thread(
                            agent_handler.update_last_user_message_content, phone,
                            new_content, channel_id
                        )
                    if audio_path:
                        await self.deliver_audio_transcription(phone, contact, transcription,
                                                               channel_id=channel_id)
                    else:
                        # Image/document content — delivered as a private panel card.
                        contact.add_message("transcription", transcription)
                        await ws_manager.broadcast("new_message", {
                            "phone": phone,
                            "message": {
                                "role": "transcription",
                                "content": transcription,
                                "ts": time.time(),
                            },
                        })

                if not self._channel_ai_enabled(channel_id) \
                        or not _conversation_ai_active(contact):
                    continue

                if not agent_handler.api_key:
                    notice = "[WhatsBot] API key não configurada."
                    contact.add_message("system_notice", notice)
                    await ws_manager.broadcast("new_message", {
                        "phone": phone,
                        "message": {"role": "system_notice", "content": notice, "ts": time.time()},
                    })
                    continue

                llm_text = text or ""
                if audio_path:
                    if transcription:
                        llm_text = format_media_content("audio", transcription)
                    else:
                        llm_text = llm_text or "[Áudio recebido]"
                elif image_path and transcription:
                    llm_text = format_media_content("image", transcription, text)
                elif document_path and transcription:
                    llm_text = format_media_content("document", transcription, text)

                try:
                    await asyncio.to_thread(outbound.send_presence, channel_id, phone, "composing")
                    await ws_manager.broadcast("ai_typing", {"phone": phone, "channel_id": channel_id, "active": True})
                    result = await agent_handler.aprocess_message(
                        phone,
                        llm_text,
                        save_user_message=False, save_response=False,
                        image_path=image_path if not transcription else None,
                        channel_id=channel_id,
                    )
                    if result.tool_calls:
                        await self.broadcast_tool_calls(phone, result.tool_calls, result.contact_info, channel_id=channel_id, agent_key=result.agent_key)
                    if result.reply:
                        if result.reply.startswith("[WhatsBot]"):
                            contact.add_message("system_notice", result.reply)
                            await ws_manager.broadcast("new_message", {
                                "phone": phone,
                                "message": {"role": "system_notice", "content": result.reply, "ts": time.time()},
                            })
                        else:
                            await self._send_with_typing_guard(channel_id, phone, result.reply, agent_key=result.agent_key)
                            await self.maybe_emit_ai_takeover(phone, channel_id)
                    elif not result.aborted:
                        # A5 (plano 31 F4): mesmo tratamento do caminho texto
                        # (aborted intencional não gera card).
                        logger.warning(
                            "[Batch] IA não produziu resposta para %s (%s) "
                            "(nada enviado — possível max_tokens baixo)",
                            phone, media_label)
                        try:
                            empty_msg = contact.add_message(
                                "error",
                                "⚠️ A IA não produziu resposta para a última "
                                "mensagem. Verifique o max_tokens do agente "
                                "(modelos de raciocínio precisam de orçamento alto).")
                            await ws_manager.broadcast("new_message", {
                                "phone": phone, "channel_id": channel_id,
                                "message": empty_msg,
                            })
                        except Exception:
                            logger.exception(
                                "[Batch] Falha ao gravar card de resposta "
                                "vazia para %s", phone)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("[Batch] Agent error for %s (%s): %s", phone, media_label, e)
                    await atrack_step("error", {"error": str(e), "phase": f"{media_label}_processing"}, status="error")

            await aend_execution(exec_id)
        except asyncio.CancelledError:
            await aend_execution(exec_id, error="cancelled")
            raise
        except Exception as exc:
            await aend_execution(exec_id, error=str(exc))
        finally:
            # Always clear the "IA respondendo" hint (success, cancel, or error),
            # even if it was never set (AI off for this chat) — a no-op then.
            await ws_manager.broadcast("ai_typing", {"phone": phone, "channel_id": channel_id, "active": False})

        max_exec = settings.get("max_executions", 200)
        retention_days = settings.get("execution_retention_days", 0)
        try:
            await asyncio.to_thread(prune_executions, max_exec, retention_days)
        except Exception:
            pass

    async def _orchestrate(self, channel_id: str, phone: str):
        """Typing-aware batch orchestrator: wait → batch_delay → wait → cycle.

        Phases (each cancellable except the final SEND inside _run_one_cycle):
          1. Wait until contact stops typing (defensive 30s timeout)
          2. Sleep for the configured batch_delay
          3. Wait again (typing may have resumed during the sleep)
          4. Snapshot pending and run the LLM + send cycle

        Cancellation by the webhook (new message arrived) drops the current run; the
        webhook then schedules a fresh orchestrator that picks up the new pending list.
        Keyed by (channel_id, phone) so concurrent channels never collide.
        """
        state = self.state
        settings = self.settings
        key = (channel_id, phone)
        try:
            batch_delay = ai_settings.value(
                channel_id, "message_batch_delay",
                settings.get("message_batch_delay", 3.0))
            await self._wait_typing_paused(channel_id, phone)
            await asyncio.sleep(batch_delay)
            await self._wait_typing_paused(channel_id, phone)

            items = list(state.pending_messages.get(key, []))
            if not items:
                return
            # Consume now: a NEW message arriving during _run_one_cycle goes into a fresh batch
            state.pending_messages.pop(key, None)
            # Plano 33 F6: from THIS point the batch is popped but not yet persisted.
            # Mark the cycle as processing so schedule_orchestrator won't cancel us in
            # the pop→persist window (which would drop `items`). Cleared in `finally`.
            state.processing[key] = True

            # Modo sequencial (plano 21): quando LIGADO no canal, só UM contato
            # roda o ciclo da IA por vez nesse canal. Um asyncio.Lock por canal
            # serializa o ciclo inteiro (LLM + envio) e espaça respostas
            # consecutivas por ``ai_sequential_delay`` segundos (mínimo 2s, sem
            # teto), reduzindo o risco de bloqueio da Meta quando o número
            # responde a vários clientes em paralelo. O toggle é per-canal
            # (``ai_sequential_enabled``); o default herdado é LIGADO, preservando
            # o comportamento legado sempre-ativo para canais sem o override.
            sequential_on = bool(ai_settings.value(
                channel_id, "ai_sequential_enabled", True))
            if sequential_on:
                lock = state.channel_ai_locks.get(channel_id)
                if lock is None:
                    lock = asyncio.Lock()
                    state.channel_ai_locks[channel_id] = lock
                async with lock:
                    delay = float(ai_settings.value(
                        channel_id, "ai_sequential_delay", 2.0) or 0)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await self._run_one_cycle(channel_id, phone, items)
            else:
                await self._run_one_cycle(channel_id, phone, items)

            # If new messages arrived during the SEND phase (when cancellation is blocked),
            # spawn another orchestrator so they get processed.
            if state.pending_messages.get(key):
                state.processing_tasks[key] = asyncio.create_task(self._orchestrate(channel_id, phone))
        except asyncio.CancelledError:
            return
        finally:
            # Plano 33 F6: clear the mid-cycle guard. The tail spawn (1117) and this
            # clear run with NO await between them, so a webhook message cannot slip
            # in unseen: it either arrived earlier (tail spawns a follow-up) or will
            # arrive after this clears (a fresh orchestrator is scheduled normally).
            state.processing.pop(key, None)
            cur = asyncio.current_task()
            if state.processing_tasks.get(key) is cur:
                state.processing_tasks.pop(key, None)


def _conversation_ai_active(contact) -> bool:
    """Per-conversation AI gate (plano 01 Fase 2, fatia 2 · plano 29 A5).

    Returns the active conversation's ``ai_active`` flag, defaulting to True
    (fail-open) — um erro de resolução ou ausência de conversa NUNCA silencia o
    bot. Permite pausar a IA numa conversa específica sem mexer no contato.

    Plano 29 A5 — gate de humano desacoplado do flag: mesmo com ``ai_active``
    dessincronizado em 1, a IA NÃO responde quando a conversa aberta tem um humano
    atribuído (``assignee_user_id``) sem agente de IA vinculado.

    Plano 37 (Cluster D / P1-a): a trava de transferência é 100% POR-CONVERSA. A
    ``transfer_to_human`` já grava ``ai_active=0`` (+ assignee) na conversa do canal;
    o gate por-conversa acima cobre o bloqueio. A tag ``transferido_atendente`` deixa
    de ser lida aqui (era contact-global → transferir num canal silenciava o outro
    do mesmo número); ela permanece só como RÓTULO visual. Fail-open (D2): sem
    conversa naquele inbox, ``conv=None`` → ``return True``; erro → ``return True``.
    """
    try:
        conv = conversation_repo.get_open_for_contact_scoped(contact)
        if conv:
            if not conv["ai_active"]:
                return False
            if conv.get("assignee_user_id") is not None \
                    and not conv.get("active_agent_key"):
                return False  # humano no comando — flag dessincronizado não fala mais alto
        return True
    except Exception:
        logger.exception("Falha no gate ai_active para %s", getattr(contact, "phone", "?"))
        return True
