"""Core WS-projection listeners (Plano 23 · Fase C5 — the Contract step).

Closes the Parallel Change for the conversation-lifecycle broadcasts: the domain
event (``conversation.created`` / ``.status_changed`` / ``.assigned`` /
``.unassigned`` / ``.ai_toggled`` / ``.archived`` / ``.updated`` / ``.deleted``)
is now the SINGLE trigger, and the WS broadcast the panel listens for is a
downstream LISTENER of that event — not a second, parallel effect emitted next to
it.

How (and why it stays observable exactly as before): the projection is registered
as a SYNCHRONOUS CORE subscriber on the bus (``events.register_core_sync_listener``),
so it runs INSIDE the emit — ``await``-ed in the async path, run inline in the sync
path — BEFORE the deferred plugin fan-out. The old code did
``ws_manager.broadcast(ws_event, payload)`` then ``emit_with_filter(bus_event,
payload)`` in one call (``broadcast_and_emit``); now ``broadcast_and_emit`` emits
ONLY the domain event and THIS listener performs the identical broadcast during
that emit. The frontend receives the same WS event name with the same payload and
the same timing; the characterization + 876 suites that assert ``ws_manager.broadcast``
right after an action still see it. Single source: no event is both emitted-and-
broadcast in two places.

The bus event → WS event mapping is the inverse of the explicit
``(ws_event, bus_event)`` pairs the lifecycle service passed to
``broadcast_and_emit``. Several bus events fold onto one WS name
(``conversation.assigned`` and ``conversation.unassigned`` both → the panel's
``conversation_assigned`` row update) — the mapping is N:1, never 1:N, so a static
table is faithful. Verbs that have NO paired lifecycle broadcast today
(``conversation.reopened`` / ``.attribute_set`` / ``.transferred_to_human`` /
``.agent_changed`` / ``.ai_takeover``) are intentionally ABSENT from the map: they
are emitted by ``emit_domain`` alongside a DIFFERENT WS broadcast (e.g. reopened
rides the ``conversation_status_changed`` broadcast) or have no panel projection.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Bus domain event name → the WS event name the panel switches on. The inverse of
# the lifecycle service's ``(ws_event, bus_event)`` pairs (N:1 only). KEEP THIS the
# exhaustive set of lifecycle bus events that ``broadcast_and_emit`` was paired with
# a WS broadcast for — adding a verb here is what makes its WS broadcast fire.
_LIFECYCLE_WS_EVENT: dict[str, str] = {
    "conversation.created": "conversation_created",
    "conversation.status_changed": "conversation_status_changed",
    "conversation.assigned": "conversation_assigned",
    "conversation.unassigned": "conversation_assigned",
    "conversation.ai_toggled": "conversation_ai_toggled",
    "conversation.archived": "conversation_archived",
    "conversation.updated": "conversation_updated",
    "conversation.deleted": "conversation_deleted",
}


def register_lifecycle_ws_projection(ws_manager: Any) -> None:
    """Wire the conversation-lifecycle WS projection onto the bus (call once at
    lifespan, after the bus runtime + ``ws_manager`` exist).

    Registers a synchronous core listener that, for every lifecycle bus event in
    ``_LIFECYCLE_WS_EVENT``, broadcasts the corresponding WS event with the SAME
    payload — the broadcast that ``broadcast_and_emit`` used to do inline. Defensive:
    a broadcast failure never propagates (mirrors the old ``broadcast_and_emit``).
    """
    from plugins.events import register_core_sync_listener

    async def _project(event_name: str, payload: dict) -> None:
        ws_event = _LIFECYCLE_WS_EVENT.get(event_name)
        if ws_event is None:
            return
        try:
            await ws_manager.broadcast(ws_event, payload)
        except Exception as e:
            logger.debug("WS projection %s→%s failed: %s", event_name, ws_event, e)

    register_core_sync_listener(_project)
    logger.info("Core conversation-lifecycle WS projection registered (%d verbs)",
                len(_LIFECYCLE_WS_EVENT))
