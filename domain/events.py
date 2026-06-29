"""Typed conversation DOMAIN EVENTS (Plano 23 · Fase C1).

A single, typed source for the conversation-lifecycle events that the services
and LLM tools emit onto the plugin bus. Each event is a ``frozen`` dataclass
whose field set MIRRORS — key for key — the primitive dict payload the call
sites emit today (the C0/B4 normalization already used primitive DTOs, never the
raw row), so ``asdict(event)`` reproduces the exact current payload. This is a
STRUCTURAL/TYPING upgrade (one source of truth for the payload shape), NOT a
payload-content change: goldens and the audit listener's ``_resource_id`` keep
resolving unchanged.

``EVENT_NAME`` maps each dataclass to its bus event name (already present in
``plugins.events.KNOWN_EVENTS``). :func:`emit_domain` / :func:`emit_domain_sync`
are thin wrappers over the bus' ``emit_with_filter`` / ``emit_with_filter_sync``
— live, AFTER-commit semantics (the services already emit post-commit; this just
gives them a typed entry point).

LEAF MODULE: imports only stdlib + the bus emit helpers. No ``server`` / ``db``
imports (same discipline as ``domain/permission_catalog.py``) so it stays a
dependency sink the data/service layers can use without a cycle.

§3.1 gap notes (intentionally NOT carried as fields, to keep payloads
byte-identical to today's emits — a future MINOR enrichment can add them):
  * ``ConversationStatusChanged`` / ``ConversationAssigned`` / ``ConversationAi*``
    / ``ConversationArchived`` / ``ConversationCreated`` / ``ConversationUpdated``
    / ``ConversationDeleted`` are emitted today through the shared
    ``conversation_service._broadcast`` payload (co-owned with the WS frontend) —
    they are dataclassed here for the typed catalogue but their EMIT relocation to
    ``emit_domain`` is C5's Contract step (broadcast-WS-becomes-listener). C1 only
    routes the FIVE standalone (non-``_broadcast``) emits through ``emit_domain``:
    reopened, attribute_set, transferred_to_human, agent_changed, ai_takeover.
  * ``ConversationTransferredToHuman`` target DTO lists no ``actor``; today's tool
    payload has none → matched. ``ConversationAiTakeover`` target lists ``agent_key``
    → matched. ``ConversationAttributeSet`` carries ``actor`` (route actor id).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time


# ── Conversation lifecycle / ownership events ──────────────────────────────────
#
# Field order/names MIRROR the current emitted payloads exactly. ``ts`` defaults
# to ``time.time()`` at construction so a call site can omit it (the old emits
# stamped ``time.time()`` inline at the emit) — pass an explicit ``ts`` only to
# reproduce a captured value.


@dataclass(frozen=True)
class ConversationCreated:
    """``conversation.created`` — §3.1 target DTO. Emitted today via the shared
    ``_broadcast`` payload (C5 relocates the emit); dataclassed for the catalogue."""
    conversation_id: int
    contact_id: int | None = None
    phone: str | None = None
    channel_id: str | None = None
    inbox_id: int | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationStatusChanged:
    """``conversation.status_changed`` — §3.1 target DTO (C5 relocates the emit)."""
    conversation_id: int
    from_status: str | None = None
    to_status: str | None = None
    actor: object | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationAssigned:
    """``conversation.assigned`` — §3.1 target DTO (C5 relocates the emit)."""
    conversation_id: int
    assignee_user_id: int | None = None
    previous_assignee: int | None = None
    actor: object | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationUnassigned:
    """``conversation.unassigned`` — §3.1 target DTO (C5 relocates the emit)."""
    conversation_id: int
    previous_assignee: int | None = None
    actor: object | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationAiToggled:
    """``conversation.ai_toggled`` — §3.1 target DTO (C5 relocates the emit)."""
    conversation_id: int
    contact_id: int | None = None
    ai_active: int | None = None
    scope: str | None = None
    actor: object | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationArchived:
    """``conversation.archived`` — §3.1 target DTO (C5 relocates the emit)."""
    conversation_id: int
    archived: int | None = None
    actor: object | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationUpdated:
    """``conversation.updated`` — generic DTO (C5 relocates the emit)."""
    conversation_id: int
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationDeleted:
    """``conversation.deleted`` — generic DTO (C5 relocates the emit)."""
    conversation_id: int
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationReopened:
    """``conversation.reopened`` — emitted on a closed→open transition.

    C1 routes the MANUAL (route) emit in ``conversation_service.set_status``
    through here; the AUTOMATIC inbound emit in ``agent/memory.py`` is C5's
    decoupling and stays a raw dict (it omits ``actor``). Payload mirrors the
    manual emit exactly: ``conversation_id, contact_id, phone, previous_status,
    trigger, actor, ts``.
    """
    conversation_id: int
    contact_id: int | None = None
    phone: str | None = None
    previous_status: str | None = None
    trigger: str = "manual"
    actor: object | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationTransferredToHuman:
    """``conversation.transferred_to_human`` — the ``transfer_to_human`` tool.

    Payload mirrors the tool's current emit: ``conversation_id, contact_id,
    reason, ts`` (no ``actor`` — the LLM tool has no operator identity)."""
    conversation_id: int
    contact_id: int | None = None
    reason: str | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationAgentChanged:
    """``conversation.agent_changed`` — the ``transferir_agente`` handoff tool.

    Payload mirrors the tool's current emit: ``conversation_id, from_agent,
    to_agent, reason, ts``."""
    conversation_id: int
    from_agent: str | None = None
    to_agent: str | None = None
    reason: str | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationAttributeSet:
    """``conversation.attribute_set`` — one event PER changed custom attribute
    (granular), emitted from ``conversation_service.set_attributes``.

    Payload mirrors the current emit: ``conversation_id, key, value, actor, ts``."""
    conversation_id: int
    key: str
    value: object = None
    actor: object | None = None
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationAiTakeover:
    """``conversation.ai_takeover`` — promoted from the internal dedupe; fired
    once per conversation when the AI first answers.

    Payload mirrors the current emit: ``conversation_id, agent_key, ts``."""
    conversation_id: int
    agent_key: str | None = None
    ts: float = field(default_factory=time.time)


# ── dataclass type -> bus event name ───────────────────────────────────────────

EVENT_NAME: dict[type, str] = {
    ConversationCreated: "conversation.created",
    ConversationStatusChanged: "conversation.status_changed",
    ConversationAssigned: "conversation.assigned",
    ConversationUnassigned: "conversation.unassigned",
    ConversationAiToggled: "conversation.ai_toggled",
    ConversationArchived: "conversation.archived",
    ConversationUpdated: "conversation.updated",
    ConversationDeleted: "conversation.deleted",
    ConversationReopened: "conversation.reopened",
    ConversationTransferredToHuman: "conversation.transferred_to_human",
    ConversationAgentChanged: "conversation.agent_changed",
    ConversationAttributeSet: "conversation.attribute_set",
    ConversationAiTakeover: "conversation.ai_takeover",
}


# ── emit helpers (thin wrappers over the bus) ──────────────────────────────────

def _payload(evt) -> tuple[str, dict]:
    """Resolve ``(bus_event_name, primitive_payload)`` for a domain event."""
    try:
        name = EVENT_NAME[type(evt)]
    except KeyError as e:  # pragma: no cover - guards a typo at the call site
        raise TypeError(f"unknown domain event type: {type(evt).__name__}") from e
    return name, asdict(evt)


async def emit_domain(evt) -> None:
    """Emit a typed domain event on the plugin bus (async path).

    Wraps ``plugins.events.emit_with_filter`` — same ``filter.event.before_emit``
    interception + live fan-out the ad-hoc emits had. ``asdict(evt)`` reproduces
    the exact primitive payload. Call AFTER the DB commit (the services already
    do). Imported lazily so this module stays a leaf for non-emitting importers.
    """
    from plugins.events import emit_with_filter

    name, payload = _payload(evt)
    await emit_with_filter(name, payload)


def emit_domain_sync(evt) -> None:
    """Sync sibling of :func:`emit_domain` for worker-thread / sync call sites
    (the LLM tools run synchronously). Wraps
    ``plugins.events.emit_with_filter_sync``."""
    from plugins.events import emit_with_filter_sync

    name, payload = _payload(evt)
    emit_with_filter_sync(name, payload)
