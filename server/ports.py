"""Typed ports (interfaces) the service layer depends on (Plano 23 · Fase B1).

A *port* is a minimal, structural interface that decouples a service from the
concrete infrastructure that implements it. Wave 2 services (``messaging_service``,
``conversation_service``, …) will depend on these protocols instead of importing
``server.app`` (which pulls in the whole web layer and would create an import
cycle). Today the existing callers already receive infra via the ``deps`` object
threaded through ``register_routes(app, deps)`` — this module does NOT rewire
them; it only declares the contract Wave 2 will program against.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BroadcastPort(Protocol):
    """The ``ws_manager.broadcast(event, data)`` capability, as a port.

    A Wave-2 service that needs to push a real-time WebSocket event takes a
    ``BroadcastPort`` (satisfied by the live ``ConnectionManager``/``ws_manager``
    in ``deps.ws_manager``) instead of importing ``server.app``. ``broadcast`` is
    an async coroutine: ``await port.broadcast("new_message", {...})``.
    """

    async def broadcast(self, event: str, data: Any) -> Any:  # pragma: no cover - protocol
        ...
