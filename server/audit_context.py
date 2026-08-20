"""Current-actor context for the audit trail (plano 07 Fase 1).

A ``ContextVar`` carrying who is acting in the current async context. The auth
middleware sets it after resolving ``request.state.user``; the core audit
listener reads it so bus-captured events get the real human actor.

Propagation: ``asyncio.create_task`` (how the bus schedules the ``*`` handler)
snapshots the current context, so an event emitted inside a request inherits its
actor even though the middleware resets the var after the response. Events
emitted off-request (jobs / threaded sync emits) fall back to ``system``.
"""

from __future__ import annotations

import contextvars
import dataclasses


@dataclasses.dataclass(frozen=True)
class ActorCtx:
    id: int | None = None
    type: str = "system"          # system | user | ai | apikey
    label: str | None = None
    ip: str | None = None
    request_id: str | None = None
    # Procedência: id da chave de API quando a request entrou por ``X-Api-Key``.
    # O ator (id/label) continua sendo o USUÁRIO DONO — a ação é dele.
    api_key_id: int | None = None


_SYSTEM = ActorCtx()

_current_actor: contextvars.ContextVar[ActorCtx] = contextvars.ContextVar(
    "current_actor", default=_SYSTEM
)


def set_current_actor(actor: ActorCtx):
    """Set the actor for the current context. Returns the reset token."""
    return _current_actor.set(actor)


def reset_current_actor(token) -> None:
    try:
        _current_actor.reset(token)
    except (ValueError, LookupError):
        pass


def get_current_actor() -> ActorCtx:
    return _current_actor.get()
