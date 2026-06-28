"""Conversation lifecycle + ownership service (Plano 23 · Fase B4).

This module owns the ROUTE-driven conversation lifecycle that previously lived
as inline logic in ``server/routes/conversations.py`` (status / assign / archive
/ attributes / agent / per-conversation AI) and the per-contact AI toggle in
``server/routes/contacts.py``. Branch by Abstraction: the routes now resolve the
permission + guard + body validation, then delegate the WRITE + the SIDE EFFECTS
(WS broadcast, plugin-bus emit, system-notice card) to the functions here.

Three responsibilities moved OUT of the repos into this service (the repos became
PURE DATA — plain writes/reads, no policy, no lazy-imports of ``system_notices``
or the bus):

  * **Status policy** — closing a conversation CLEARS the assignee + active agent
    (so it leaves any queue); opening clears ``resolved_at``. Validated against
    :data:`VALID_TRANSITIONS` HERE, not in the repo.
  * **Transfer policy** — the divergent ``set_ai`` / ``assign_agent`` /
    ``toggle_contact_ai`` ownership transitions are UNIFIED through the private
    :func:`_transfer` helper: who gets the conversation, whether the AI agent is
    (re)bound or cleared, and whether the contact-level AI gate is mirrored.
  * **Default seeding** — brand-new conversations still get their AI/agent
    defaults, but that lives in :func:`conversation_repo.create`'s thin
    config read (kept there — it is data-shaped, not lifecycle policy).

EMIT DISCIPLINE (the #1 hazard): every domain event
(``conversation.status_changed`` / ``.assigned`` / ``.unassigned`` /
``.archived`` / ``.ai_toggled`` / ``.reopened`` / ``.attribute_set`` /
``.updated`` / ``.deleted`` / ``contact.ai_toggled``) and every system-notice
card fires FROM HERE, EXACTLY ONCE per action. The routes no longer emit anything
they delegate (no double-emission). Payload shapes are preserved byte-for-byte —
DTO normalization is C1's job.

Filters: :func:`set_status` applies ``filter.conversation.before_status`` (relocated
from the route, same semantics — ``None`` aborts a close); :func:`assign` applies
the NEW ``filter.conversation.before_assign`` (``None`` aborts the assign).

The service never imports ``server.app``; it reuses
``app.services.messaging_service.broadcast_and_emit`` (the generalized lift of
the old ``conversations._broadcast``) and the plugin bus directly.
"""

from __future__ import annotations

import asyncio
import logging
import time

from db.repositories import (conversation_repo, contact_repo, user_repo,
                             agent_repo)
from server import system_notices
from plugins.events import apply_filter, emit_with_filter

logger = logging.getLogger(__name__)


# ── Lifecycle transition policy ────────────────────────────────────────────────

# Allowed status transitions. The route only exposes open/closed today; "reopened"
# is reserved for a future explicit state. Validating HERE (not in the repo) keeps
# the repo pure-data: it just writes whatever status it is handed.
VALID_TRANSITIONS: dict[str, set[str]] = {
    "open": {"closed"},
    "closed": {"open"},
    "reopened": {"closed"},
}


# ── WS + bus broadcast (delegates to the messaging_service lift) ───────────────

async def _broadcast(deps, ws_event: str, bus_event: str, conv: dict, **extra):
    """Push a conversation change to the panel (WS) and the plugin bus.

    Projects the conversation payload (UNCHANGED from the old
    ``conversations._broadcast``) and delegates to
    ``app.services.messaging_service.broadcast_and_emit``. Defensive: a broadcast
    failure never fails the action. Imported lazily to avoid an import cycle
    (messaging_service imports back into ``server``)."""
    from app.services.messaging_service import broadcast_and_emit

    payload = {
        "conversation_id": conv.get("id"),
        "display_id": conv.get("display_id"),
        "contact_id": conv.get("contact_id"),
        "status": conv.get("status"),
        "assignee_user_id": conv.get("assignee_user_id"),
        "active_agent_key": conv.get("active_agent_key"),
        "ai_active": conv.get("ai_active"),
        "is_archived": conv.get("is_archived"),
        "inbox_id": conv.get("inbox_id"),
        **extra,
        "ts": time.time(),
    }
    await broadcast_and_emit(deps, ws_event, bus_event, payload)


async def _emit_notice(conv: dict, event_type: str, *, actor_name: str | None = None,
                       **ctx):
    """Write a conversation lifecycle notice into the chat thread (plano 12).

    ``actor_name`` is the resolved actor (``None`` ⇒ neutral phrasing). The config
    gate (global, per group) decides whether anything is stored/emitted, so call
    sites stay dumb. Never raises — a failed notice never fails the action."""
    try:
        await asyncio.to_thread(
            system_notices.emit_conversation_notice,
            event_type=event_type,
            conversation_id=conv.get("id"),
            contact_id=conv.get("contact_id"),
            actor=actor_name,
            **ctx,
        )
    except Exception as e:
        logger.debug("conversation notice %s failed: %s", event_type, e)


# ── Lifecycle ──────────────────────────────────────────────────────────────────

async def create(deps, *, inbox_id: int, contact_id: int, contact_inbox_id: int,
                 opened_at: float | None = None, ai_active: int | None = None,
                 is_archived: int = 0, active_agent_key: str | None = None,
                 actor_id=None) -> dict:
    """Create a conversation and broadcast/emit ``conversation.created``.

    The route-driven create path (the inbound auto-create lives in
    ``conversation_repo.resolve_for_contact_ex`` and is Wave-3/C5 territory, left
    intact). Defaults (ai_active / active_agent_key seeding) stay in
    ``conversation_repo.create`` — they are config-read data, not lifecycle policy.
    """
    conv = await asyncio.to_thread(
        conversation_repo.create, inbox_id=inbox_id, contact_id=contact_id,
        contact_inbox_id=contact_inbox_id, opened_at=opened_at, ai_active=ai_active,
        is_archived=is_archived, active_agent_key=active_agent_key)
    await _broadcast(deps, "conversation_created", "conversation.created", conv)
    return conv


async def set_status(deps, conv: dict, status: str, *, actor_id=None,
                     actor_name: str | None = None) -> dict | None:
    """Open/close (and the reopened verb) for a conversation.

    Owns the LIFECYCLE ORCHESTRATION the route used to do inline: applies
    ``filter.conversation.before_status`` on a close (a plugin may abort by
    returning ``None`` → the caller raises the 403), persists the status via the
    repo's status-derived data write (close also drops the assignment + stamps
    ``resolved_at``; open clears ``resolved_at``), and emits — EXACTLY once each:
      * ``conversation.status_changed`` (WS ``conversation_status_changed``),
      * ``conversation.reopened`` (additive, only on a closed→open transition),
      * the ``status_closed`` / ``status_open`` system-notice card.

    Returns the updated conversation, ``None`` if the row vanished, or the string
    ``"blocked"`` when a plugin aborted the close (the route maps it to a 403).
    """
    conv_id = conv["id"]
    previous_status = conv.get("status")

    if status == "closed":
        allowed = await apply_filter(
            "filter.conversation.before_status",
            {"conversation_id": conv_id, "new_status": status},
            ctx_extras={"user_id": actor_id},
        )
        if allowed is None:
            return "blocked"

    updated = await asyncio.to_thread(conversation_repo.set_status, conv_id, status)
    if not updated:
        return None

    await _broadcast(deps, "conversation_status_changed",
                     "conversation.status_changed", updated)
    # plano 23 Fase C0: distinct ``conversation.reopened`` verb when a closed
    # conversation is reactivated by an explicit (manual) status change. Kept
    # alongside ``conversation.status_changed`` — the reopened verb is additive.
    if status == "open" and previous_status == "closed":
        await emit_with_filter("conversation.reopened", {
            "conversation_id": updated.get("id"),
            "contact_id": updated.get("contact_id"),
            "phone": updated.get("contact_phone"),
            "previous_status": previous_status,
            "trigger": "manual",
            "actor": actor_id,
            "ts": time.time(),
        })
    await _emit_notice(updated,
                       "status_closed" if status == "closed" else "status_open",
                       actor_name=actor_name)
    return updated


async def archive(deps, conv: dict, archived: int, *, actor_name: str | None = None
                  ) -> dict | None:
    """Archive/unarchive a conversation. Emits ``conversation.archived`` + the
    archived/unarchived system-notice card, EXACTLY once each."""
    updated = await asyncio.to_thread(
        conversation_repo.set_archived, conv["id"], archived)
    if not updated:
        return None
    await _broadcast(deps, "conversation_archived", "conversation.archived", updated)
    await _emit_notice(updated, "archived" if archived else "unarchived",
                       actor_name=actor_name)
    return updated


async def set_attributes(deps, conv: dict, merged: dict | None, changed: dict,
                         defs: dict, *, actor_id=None,
                         actor_name: str | None = None) -> dict | None:
    """Replace the conversation's custom_attributes and emit the changes.

    The route still validates the incoming attribute map (key existence + value
    type) and computes the ``merged`` final map + the ``changed`` (key→value) diff
    (it owns the 400s). When ``merged`` is ``None`` (no ``custom_attributes`` in the
    body) NOTHING is written — the existing ``conv`` is just re-broadcast (preserving
    the route's prior no-write path). Otherwise persists ``merged`` and emits, EXACTLY
    once each:
      * ``conversation.updated`` (WS ``conversation_updated``, fields=custom_attributes),
      * the aggregated ``attribute_set`` system-notice card (1 card per batch),
      * one ``conversation.attribute_set`` bus event PER changed key (granular).
    """
    if merged is None:
        updated = conv
    else:
        updated = await asyncio.to_thread(
            conversation_repo.set_custom_attributes, conv["id"], merged)
        if not updated:
            return None
    await _broadcast(deps, "conversation_updated", "conversation.updated", updated,
                     fields={"custom_attributes": updated.get("custom_attributes")})
    if changed:
        # Aggregate a batch of attribute changes into a single card (plano 12 §6).
        if len(changed) == 1:
            key, value = next(iter(changed.items()))
            label = (defs.get(key) or {}).get("display_name") or key
            await _emit_notice(updated, "attribute_set",
                               actor_name=actor_name, attribute=label, value=value)
        else:
            await _emit_notice(updated, "attribute_set",
                               actor_name=actor_name, count=len(changed))
        # plano 23 Fase C0: one ``conversation.attribute_set`` bus event PER changed
        # key (the card above is aggregated; the bus event is granular so a
        # subscriber gets each key/value pair).
        for key, value in changed.items():
            await emit_with_filter("conversation.attribute_set", {
                "conversation_id": updated.get("id"),
                "key": key,
                "value": value,
                "actor": actor_id,
                "ts": time.time(),
            })
    return updated


async def delete(deps, conv: dict) -> None:
    """Hard-delete a conversation (plano 16). The route already read the enriched
    ``conv`` row (so the payload survives the delete) and dropped the in-RAM
    contact cache. Emits ``conversation.deleted`` once. No lifecycle card (the
    thread it would land in is destroyed)."""
    await asyncio.to_thread(conversation_repo.delete, conv["id"])
    await _broadcast(deps, "conversation_deleted", "conversation.deleted", conv)


# ── Ownership (assign / agent / per-conversation AI) — unified via _transfer ──

async def _transfer(deps, conv: dict, *, assignee_user_id, active_agent_key,
                    ai_active, mirror_contact_ai: bool | None,
                    contact_id=None) -> dict | None:
    """UNIFY the ownership transition that ``set_ai`` / ``assign_agent`` /
    ``toggle_contact_ai`` each used to do divergently.

    One atomic conversation write of the three ownership columns
    (``assignee_user_id`` / ``active_agent_key`` / ``ai_active``), then the OPTIONAL
    contact-level AI gate mirror + its WS/bus events. ``ai_active=None`` leaves the
    AI gate column untouched (the repo skips it). ``mirror_contact_ai`` is the
    contact-level ``ai_enabled`` to write (True/False) or ``None`` to leave the
    contact gate alone. ``contact_id`` defaults to the conversation's contact.

    Returns the updated conversation, or ``None`` if it vanished. Does NOT emit the
    ``conversation.assigned``/``ai_toggled`` events itself — each public caller
    emits the verbs appropriate to its action (so the event-per-action contract is
    explicit at the call site and never double-fires).
    """
    updated = await asyncio.to_thread(
        conversation_repo.assign_agent, conv["id"],
        assignee_user_id=assignee_user_id, active_agent_key=active_agent_key,
        ai_active=ai_active)
    if not updated:
        return None
    if mirror_contact_ai is not None:
        cid = contact_id if contact_id is not None else updated.get("contact_id")
        contact = await asyncio.to_thread(contact_repo.get, cid)
        if contact:
            await asyncio.to_thread(
                contact_repo.update, contact["id"],
                ai_enabled=1 if mirror_contact_ai else 0)
            try:
                await deps.ws_manager.broadcast("contact_ai_toggled", {
                    "phone": contact["phone"], "ai_enabled": mirror_contact_ai})
                await emit_with_filter("contact.ai_toggled", {
                    "phone": contact["phone"], "ai_enabled": mirror_contact_ai})
            except Exception as e:
                logger.debug("contact_ai_toggled broadcast failed: %s", e)
    return updated


async def assign(deps, conv: dict, assignee_user_id, *, actor_id=None,
                 actor_name: str | None = None) -> dict | None:
    """Set/clear the human assignee of a conversation.

    Applies the NEW ``filter.conversation.before_assign`` — a plugin may abort by
    returning ``None`` (the caller maps it to a 403). Emits, EXACTLY once:
      * ``conversation.assigned`` (assignee set) OR ``conversation.unassigned``
        (assignee cleared) — the WS event stays ``conversation_assigned`` either way;
      * the ``assigned`` / ``unassigned`` system-notice card.

    Returns the updated conversation, ``None`` if it vanished, or the string
    ``"blocked"`` when a plugin aborted the assign (the route maps it to a 403).
    """
    allowed = await apply_filter(
        "filter.conversation.before_assign",
        {"conversation_id": conv["id"], "assignee_user_id": assignee_user_id},
        ctx_extras={"user_id": actor_id},
    )
    if allowed is None:
        return "blocked"
    previous_assignee = conv.get("assignee_user_id")
    updated = await asyncio.to_thread(
        conversation_repo.set_assignee, conv["id"], assignee_user_id)
    if not updated:
        return None
    # plano 23 Fase C0: distinct verb for the removal case (WS unchanged).
    bus_event = "conversation.assigned" if assignee_user_id else "conversation.unassigned"
    await _broadcast(deps, "conversation_assigned", bus_event, updated,
                     previous_assignee=previous_assignee)
    if assignee_user_id:
        target = await asyncio.to_thread(user_repo.get, assignee_user_id)
        await _emit_notice(updated, "assigned", actor_name=actor_name,
                           target=(target or {}).get("name")
                           or f"usuário #{assignee_user_id}")
    else:
        await _emit_notice(updated, "unassigned", actor_name=actor_name)
    return updated


async def assign_me(deps, conv: dict, user_id: int, *, actor_name: str | None = None
                    ) -> dict | None:
    """Assign a conversation to the calling user (assign-me). Emits
    ``conversation.assigned`` + the ``assigned_me`` system-notice card, once each."""
    updated = await asyncio.to_thread(
        conversation_repo.set_assignee, conv["id"], user_id)
    if not updated:
        return None
    await _broadcast(deps, "conversation_assigned", "conversation.assigned", updated,
                     by_user_id=user_id)
    await _emit_notice(updated, "assigned_me", actor_name=actor_name)
    return updated


async def assign_unified(deps, conv: dict, *, kind: str, user_id=None,
                         agent_key: str | None = None, actor_id=None,
                         actor_name: str | None = None) -> dict | None:
    """Unified assignment (plano 10): route a conversation to a HUMAN, an AI agent,
    or unassign. Converges the three ownership transitions through :func:`_transfer`:

      * ``user`` → set assignee, clear AI agent, AI OFF; mirror contact AI → OFF.
      * ``ai``   → set AI agent, clear human assignee, AI ON; mirror contact AI → ON.
      * ``none`` → unassign both; leave the AI gate + contact gate untouched.

    Emits ``conversation.assigned`` (WS ``conversation_assigned``) once, plus the
    assigned/assigned_me/unassigned/agent_changed system-notice card matching the
    action. Validation of ``kind``/``user_id``/``agent_key`` stays in the route
    (it owns the 400s)."""
    if kind == "user":
        updated = await _transfer(
            deps, conv, assignee_user_id=user_id, active_agent_key=None,
            ai_active=0, mirror_contact_ai=False)
    elif kind == "ai":
        updated = await _transfer(
            deps, conv, assignee_user_id=None, active_agent_key=agent_key,
            ai_active=1, mirror_contact_ai=True)
    else:  # none
        updated = await _transfer(
            deps, conv, assignee_user_id=None, active_agent_key=None,
            ai_active=None, mirror_contact_ai=None)
    if not updated:
        return None
    await _broadcast(deps, "conversation_assigned", "conversation.assigned", updated)
    # Parity with the sidebar right-click (assign / assign-me / agent): surface a
    # lifecycle card in the thread. Defensive — a failed notice never fails the action.
    if kind == "user":
        if actor_id is not None and user_id == actor_id:
            await _emit_notice(updated, "assigned_me", actor_name=actor_name)
        else:
            target = await asyncio.to_thread(user_repo.get, user_id)
            await _emit_notice(updated, "assigned", actor_name=actor_name,
                               target=(target or {}).get("name") or f"usuário #{user_id}")
    elif kind == "none":
        await _emit_notice(updated, "unassigned", actor_name=actor_name)
    elif kind == "ai":
        ag = await asyncio.to_thread(agent_repo.get, agent_key)
        await _emit_notice(updated, "agent_changed", actor_name=actor_name,
                           agent=(ag or {}).get("display_name") or agent_key)
    return updated


async def set_agent(deps, conv: dict, agent_key: str | None, *,
                    actor_name: str | None = None) -> dict | None:
    """Bind a specific AI agent to this conversation (plano 06). Emits
    ``conversation.updated`` (WS ``conversation_updated``) + the ``agent_changed``
    system-notice card, once each."""
    updated = await asyncio.to_thread(conversation_repo.set_agent, conv["id"], agent_key)
    if not updated:
        return None
    await _broadcast(deps, "conversation_updated", "conversation.updated", updated,
                     fields={"active_agent_key": updated.get("active_agent_key")})
    agent_name = None
    if updated.get("active_agent_key"):
        ag = await asyncio.to_thread(agent_repo.get, updated["active_agent_key"])
        agent_name = (ag or {}).get("display_name") or updated["active_agent_key"]
    await _emit_notice(updated, "agent_changed", actor_name=actor_name, agent=agent_name)
    return updated


async def set_ai(deps, conv: dict, active: int, *, actor_id=None,
                 actor_name: str | None = None) -> dict | None:
    """Toggle the AI for ONE conversation AND (re)assign it (plano 17), via
    :func:`_transfer` (the unified ownership policy, moved out of
    ``conversation_repo.set_conversation_ai``):

      * OFF → AI off, AI agent cleared, conversation handed to whoever turned it
        off (``actor_id``); when no operator identity (legacy/open mode) it lands
        UNASSIGNED, mirroring a close.
      * ON  → AI on, the inbox's default AI agent re-bound, human assignee cleared.

    Emits, EXACTLY once each: ``conversation.assigned`` (the row repositions) AND
    ``conversation.ai_toggled`` (the badge) — both over WS too. Plus the
    ``ai_on``/``ai_off`` system-notice card, and (when an operator took it by
    turning the AI off) the ``assigned_me`` card."""
    new_assignee = actor_id if not active else None
    if active:
        # ON re-binds the inbox's default AI agent (policy moved out of the repo).
        agent_key = await asyncio.to_thread(
            conversation_repo.default_agent_key_for_inbox, conv.get("inbox_id"))
        updated = await _transfer(
            deps, conv, assignee_user_id=None, active_agent_key=agent_key,
            ai_active=1, mirror_contact_ai=None)
    else:
        updated = await _transfer(
            deps, conv, assignee_user_id=new_assignee, active_agent_key=None,
            ai_active=0, mirror_contact_ai=None)
    if not updated:
        return None
    await _broadcast(deps, "conversation_assigned", "conversation.assigned", updated)
    await _broadcast(deps, "conversation_ai_toggled", "conversation.ai_toggled", updated)
    await _emit_notice(updated, "ai_on" if active else "ai_off", actor_name=actor_name)
    # Surface the takeover as an assignment card too when an operator turned the
    # AI off and thereby took the conversation.
    if not active and new_assignee is not None:
        await _emit_notice(updated, "assigned_me", actor_name=actor_name)
    return updated


async def toggle_contact_ai(deps, *, phone: str, enabled: bool, contact_id: int,
                            actor_name: str | None = None) -> None:
    """Per-CONTACT AI gate toggle (POST /api/contacts/{phone}/toggle-ai).

    The route already flipped the contact-level ``ai_enabled`` (via the
    ``ContactMemory`` wrapper) and captured ``contact_id`` + the resulting
    ``enabled``. This emits, EXACTLY once each:
      * ``contact.ai_toggled`` (WS ``contact_ai_toggled``) — the contact verb,
      * the ``ai_on``/``ai_off`` system-notice card on the contact's conversation,
      * P17 mirror: flip the contact's conversation ``ai_active`` (low-level, no
        (un)assign) + WS ``conversation_ai_toggled`` so the sidebar badge flips —
        this mirror is a WS broadcast ONLY (NOT a bus emit), preserving the
        characterized behavior that the bus sees only ``contact.ai_toggled`` here.
    """
    await deps.ws_manager.broadcast("contact_ai_toggled", {
        "phone": phone, "ai_enabled": enabled})
    await emit_with_filter("contact.ai_toggled", {
        "phone": phone, "ai_enabled": enabled, "ts": time.time()})
    conv = await asyncio.to_thread(
        system_notices.emit_for_contact,
        event_type="ai_on" if enabled else "ai_off",
        contact_id=contact_id, phone=phone, actor=actor_name)
    if conv is not None:
        await asyncio.to_thread(
            conversation_repo.set_ai_active, conv["id"], 1 if enabled else 0)
        await deps.ws_manager.broadcast("conversation_ai_toggled", {
            "conversation_id": conv["id"], "contact_id": contact_id,
            "ai_active": 1 if enabled else 0, "ts": time.time()})
