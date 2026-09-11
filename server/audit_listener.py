"""Core audit listener + write path (plano 07 Fase 0/1).

Registers a single ``*`` handler on the event bus under a sentinel plugin id so
every emitted event is checked against the ``AUDITABLE_EVENTS`` allowlist and,
when it matches, persisted to ``audit_log``. Runs fire-and-forget off the request
path; defensive (never raises into the bus).

:func:`record` is the DIRECT write path used by code that has no bus event to
piggyback on — notably plugins, via the ``plugins.context.audit`` helper. It
applies the same two rules as the listener: the ``audit_enabled`` master gate and
the current-actor resolution. Nothing else in the process writes to ``audit_log``
(the only other caller is the export route, which audits itself).
"""

from __future__ import annotations

import logging

from db import audit_actions
from db.repositories import audit_repo, config_repo
from server.audit_context import get_current_actor

logger = logging.getLogger(__name__)

_CORE_PLUGIN_ID = "__core_audit__"

# Where to find a resource id in a payload, in priority order.
_RESOURCE_ID_KEYS = ("phone", "id", "plugin_id", "name", "tag", "key", "contact_id",
                     "channel_id")

_ACTOR_TYPES = ("system", "user", "ai", "apikey")


def _resource_id(payload: dict):
    for k in _RESOURCE_ID_KEYS:
        v = payload.get(k)
        if v not in (None, ""):
            return v
    return None


def enabled() -> bool:
    """Master gate (plano 07). Read per write so the toggle vale sem restart."""
    try:
        return bool(config_repo.get("audit_enabled", True))
    except Exception:  # DB hiccup must never break the audited action
        return False


def record(*, action: str, resource_type: str, resource_id=None,
           before=None, after=None, actor_type: str | None = None,
           actor_label: str | None = None) -> int | None:
    """Persist ONE audit row. Blocking (DB write) — never raises, returns the id.

    The actor comes from the request-scoped ``ContextVar`` (so a plugin route
    records the real human automatically); ``actor_type``/``actor_label`` override
    it for machine actors (e.g. an external executor applying AI-proposed
    changes → ``actor_type="ai"``). ``before``/``after`` are secret-masked inside
    ``audit_repo.add``.
    """
    try:
        if not action or not resource_type:
            logger.warning("audit record ignorado: action/resource_type vazio")
            return None
        if not enabled():
            return None
        actor = get_current_actor()
        a_type = actor_type if actor_type in _ACTOR_TYPES else actor.type
        # Um ator forçado (ai/system) não pode herdar o id/rótulo do humano da
        # request — seria atribuir a ação à pessoa errada.
        overridden = actor_type in _ACTOR_TYPES and actor_type != actor.type
        return audit_repo.add(
            actor_user_id=None if overridden else actor.id,
            actor_type=a_type,
            actor_label=actor_label or (None if overridden else actor.label),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before,
            after=after,
            ip_address=actor.ip,
            request_id=actor.request_id,
            # Procedência: mantida mesmo quando o ator é forçado (ai/system) —
            # continua sendo verdade por onde a request entrou.
            api_key_id=actor.api_key_id,
        )
    except Exception as e:  # auditing never breaks the audited action
        logger.warning("audit record falhou para %s: %s", action, e)
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
        # Master global (plano 07): desligado ⇒ nada é gravado. Checado por evento
        # para que o toggle na tela Auditoria valha sem restart.
        if not enabled():
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
            api_key_id=actor.api_key_id,
        )
    except Exception as e:  # never let auditing break the bus
        logger.warning("audit listener failed for %s: %s", getattr(ctx, "event_name", "?"), e)


def register_audit_listener() -> None:
    """Wire the core ``*`` handler. Call once at lifespan start, after the bus runtime."""
    from plugins.events import register
    register(_CORE_PLUGIN_ID, "*", audit_event_handler)
    logger.info("Core audit listener registered (%d auditable events)",
                len(audit_actions.AUDITABLE_EVENTS))
