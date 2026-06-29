"""Core audit listener (plano 07 Fase 0/1).

Registers a single ``*`` handler on the event bus under a sentinel plugin id so
every emitted event is checked against the ``AUDITABLE_EVENTS`` allowlist and,
when it matches, persisted to ``audit_log``. Runs fire-and-forget off the request
path; defensive (never raises into the bus).
"""

from __future__ import annotations

import logging

from db import audit_actions
from db.repositories import audit_repo
from server.audit_context import get_current_actor

logger = logging.getLogger(__name__)

_CORE_PLUGIN_ID = "__core_audit__"

# Where to find a resource id in a payload, in priority order.
_RESOURCE_ID_KEYS = ("phone", "id", "plugin_id", "name", "tag", "key", "contact_id")


def _resource_id(payload: dict):
    for k in _RESOURCE_ID_KEYS:
        v = payload.get(k)
        if v not in (None, ""):
            return v
    return None


def audit_event_handler(ctx, payload: dict) -> None:
    """Persist an auditable bus event. No-op for events not in the allowlist.

    Emit sites may attach two reserved keys to opt into before/after auditing:
      - ``_audit_before``: the prior state → stored in ``before_json``.
      - ``_audit_after``:  the explicit new state → stored in ``after_json``;
        when absent, the (reserved-key-stripped) payload is used as the "after".
    Both are sanitised (secret-masked) inside ``audit_repo.add``. Events that don't
    set them keep the legacy behaviour (after = payload, before = NULL).
    """
    try:
        spec = audit_actions.AUDITABLE_EVENTS.get(ctx.event_name)
        if not spec:
            return
        action, rtype = spec
        actor = get_current_actor()
        data = payload or {}
        before = data.get("_audit_before")
        if "_audit_after" in data:
            after = data["_audit_after"]
        else:
            after = {k: v for k, v in data.items() if not k.startswith("_audit_")}
        audit_repo.add(
            actor_user_id=actor.id,
            actor_type=actor.type,
            actor_label=actor.label,
            action=action,
            resource_type=rtype,
            resource_id=_resource_id(data),
            before=before,                 # sanitised inside add()
            after=after,                   # sanitised inside add()
            ip_address=actor.ip,
            request_id=actor.request_id,
        )
    except Exception as e:  # never let auditing break the bus
        logger.warning("audit listener failed for %s: %s", getattr(ctx, "event_name", "?"), e)


def register_audit_listener() -> None:
    """Wire the core ``*`` handler. Call once at lifespan start, after the bus runtime."""
    from plugins.events import register
    register(_CORE_PLUGIN_ID, "*", audit_event_handler)
    logger.info("Core audit listener registered (%d auditable events)",
                len(audit_actions.AUDITABLE_EVENTS))
