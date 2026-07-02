"""Listeners for ``message.persisted`` — the after-INSERT lifecycle effects
moved OUT of ``agent.memory.add_message`` (Plano 23 · Fase C5, §2.4 decoupling).

``add_message`` is the universal hot path (every inbound + AI message save). It
used to perform its conversation-lifecycle side effects INLINE right after the
INSERT: the automatic created/reopened system-notice card, the ``conversation_created``
/ ``conversation_status_changed`` list-update WS broadcasts (so the sidebar / kanban
re-file the thread live), and the ``conversation.reopened`` plugin-bus event. C5
makes ``add_message`` only PERSIST + EMIT ``message.persisted``; these reactions are
now a LISTENER of that event (the hot path emits, listeners react — single source).

Invocation contract (exactly-once, any environment): ``add_message`` PERSISTS, emits
the PUBLIC ``message.persisted`` event (clean payload ``{conversation_id, contact_id,
role, msg_id, ts}``) onto the bus for plugins/audit, and then drives
:func:`on_message_persisted` DIRECTLY with the one-shot lifecycle context
(``transition`` + the ``conv`` projection only the hot path knows). The reaction runs
SYNCHRONOUSLY in the caller's thread — required because the WS broadcasts go through
``plugins.context.broadcast`` and the 876 suite asserts them right after a synchronous
``add_message`` call (legacy script: no running bus loop, so the deferred plugin fan-out
would never run). Driving the function directly (rather than subscribing it to the bus)
keeps it exactly-once whether or not a server lifespan registered any listeners, and
preserves the old inline observable timing. Defensive: a failed reaction never breaks
the save (same guarantee the inline code gave).
"""

from __future__ import annotations

import logging
import time

from db.repositories._mapping import LIST_PANEL_ONLY_ROLES

logger = logging.getLogger(__name__)


def broadcast_conversation_upsert(conversation_id: int, role: str) -> None:
    """Push the AUTHORITATIVE list row for a conversation live (plano 28).

    Event-Carried State Transfer: after a VISIBLE message is persisted, emit the
    whole enriched conversation row (same shape as a ``/api/atendimentos`` item) so
    the sidebar upserts it by ``conversation_id`` WITHOUT a refetch — no
    notification-then-stale-read race. Runs pós-commit (the caller is past the INSERT),
    so ``get_row_for_broadcast`` reads real preview/unread/ts.

    Gated by :data:`LIST_PANEL_ONLY_ROLES` — panel-only saves (``tool_call``,
    ``private_note``, ``system_notice``, ...) never change a conversation's
    last-message preview, so they must not emit a list update. Defensive: a failed
    broadcast never breaks the save."""
    if role in LIST_PANEL_ONLY_ROLES:
        return
    try:
        from db.repositories import conversation_repo
        from plugins.context import broadcast
        row = conversation_repo.get_row_for_broadcast(conversation_id)
        if row is not None:
            broadcast("conversation_upsert", row)
    except Exception:
        logger.exception("Falha ao emitir conversation_upsert para conversa %s", conversation_id)


def _broadcast_conversation_created(conversation_id: int, contact_id: int,
                                    phone: str, conv: dict) -> None:
    """``conversation_created`` WS so the conversa-cêntrica sidebar + conversation
    list add the new per-channel thread without a reload (plano 11 D1). Payload is
    byte-identical to the old ``ContactMemory._broadcast_conversation_created``."""
    try:
        from plugins.context import broadcast
        broadcast("conversation_created", {
            "conversation_id": conversation_id,
            "display_id": conv.get("display_id"),
            "contact_id": contact_id,
            "phone": phone,
            "inbox_id": conv.get("inbox_id"),
            "status": conv.get("status"),
        })
    except Exception:
        logger.exception("Falha ao emitir conversation_created para %s", phone)


def _broadcast_conversation_status(conversation_id: int, contact_id: int,
                                   phone: str, conv: dict) -> None:
    """``conversation_status_changed`` WS when an inbound (client) or outbound
    (operator/AI) message reopens a closed conversation, so the conversation list
    (sidebar + kanban) re-files it into "Abertas" live. Payload mirrors the manual
    Resolver/Reabrir path. Byte-identical to the old
    ``ContactMemory._broadcast_conversation_status``."""
    try:
        from plugins.context import broadcast
        broadcast("conversation_status_changed", {
            "conversation_id": conversation_id,
            "display_id": conv.get("display_id"),
            "contact_id": contact_id,
            "phone": phone,
            "inbox_id": conv.get("inbox_id"),
            "status": conv.get("status"),
            "assignee_user_id": conv.get("assignee_user_id"),
            "active_agent_key": conv.get("active_agent_key"),
            "ai_active": conv.get("ai_active"),
            "is_archived": conv.get("is_archived"),
        })
    except Exception:
        logger.exception("Falha ao emitir conversation_status_changed para %s", phone)


def _emit_lifecycle_notice(conversation_id: int, contact_id: int, phone: str,
                           transition: str, conv: dict, role: str) -> None:
    """Automatic conversation-lifecycle card (plano 12 §3).

    ``reopened`` ⇒ a closed conversation went active again (client message if
    ``role == "user"``, operator/AI reply otherwise); ``created`` ⇒ new conversation.
    The config gate (group ``status``) decides whether anything is written.
    Byte-identical to the old ``ContactMemory._emit_lifecycle_notice``."""
    try:
        from server import system_notices
        if transition == "reopened":
            event_type = ("status_reopened_auto" if role == "user"
                          else "status_reopened_auto_agent")
            system_notices.emit_conversation_notice(
                event_type=event_type, conversation_id=conversation_id,
                contact_id=contact_id, phone=phone)
        elif transition == "created":
            system_notices.emit_conversation_notice(
                event_type="created", conversation_id=conversation_id,
                contact_id=contact_id, phone=phone,
                display_id=conv.get("display_id"))
    except Exception:
        logger.exception("Falha ao emitir aviso de ciclo de vida para %s", phone)


def _emit_reopened_domain(conversation_id: int, contact_id: int, phone: str) -> None:
    """Promote the AUTOMATIC inbound/outbound reopen to a distinct domain event
    (``conversation.reopened``, trigger=inbound) on the plugin bus. The status WS
    broadcast above is for the panel list; this is for plugin subscribers. Mirrors
    the old inline emit in ``add_message`` exactly (omits ``actor``). Defensive."""
    try:
        from plugins.events import emit_with_filter_sync
        emit_with_filter_sync("conversation.reopened", {
            "conversation_id": conversation_id,
            "contact_id": contact_id,
            "phone": phone,
            "previous_status": "closed",
            "trigger": "inbound",
            "ts": time.time(),
        })
    except Exception:
        logger.debug("conversation.reopened emit falhou para %s", phone)


def on_message_persisted(conversation_id: int, contact_id: int, phone: str, *,
                         transition: str | None, conv: dict | None,
                         role: str = "user") -> None:
    """Run the after-INSERT lifecycle reactions for one save (the LISTENER body).

    No created/reopened transition ⇒ nothing to do (a plain save, e.g. a panel-only
    role, or an existing open conversation). Order preserves the old ``add_message``
    sequence exactly: notice → list broadcast(s) → (reopen) bus event."""
    if transition not in ("created", "reopened") or conv is None:
        return

    # plano 12 §3: automatic lifecycle card (gated by config group ``status``).
    _emit_lifecycle_notice(conversation_id, contact_id, phone, transition, conv, role)

    if transition == "created":
        # plano 11 D1: the new thread must materialize live on the sidebar /
        # conversation list (independent of the notice gate — a list update, not a card).
        _broadcast_conversation_created(conversation_id, contact_id, phone, conv)
    elif transition == "reopened":
        # closed→open: the list (sidebar + kanban) must re-file the conversation into
        # "Abertas" live (the backend already set status open).
        _broadcast_conversation_status(conversation_id, contact_id, phone, conv)
        # plano 23 Fase C0/C5: distinct ``conversation.reopened`` bus verb.
        _emit_reopened_domain(conversation_id, contact_id, phone)
