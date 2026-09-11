"""Channel inbound event type (plano 02 Fase 0).

``InboundEvent`` is the provider-agnostic shape a channel emits from
``parse_inbound(raw)``. Fields mirror what ``filter.message.before_save`` already
manipulates, so the inbound pipeline stays uniform across providers.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional


@dataclasses.dataclass
class InboundEvent:
    channel_id: str
    provider: str
    kind: str = "message"                 # message | reaction | receipt | presence | system | ...
    direction: str = "in"                 # in | out
    external_msg_id: str = ""
    chat_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    is_group: bool = False
    text: str = ""
    media_type: Optional[str] = None
    media_path: Optional[str] = None
    media_extras: dict = dataclasses.field(default_factory=dict)
    ts: float = 0.0
    raw: dict = dataclasses.field(default_factory=dict)
    # ── Optional enrichment (plano 13 Fase 0) ────────────────────────
    # Filled by providers that resolve more than the raw payload (GOWA does
    # group @mention + sender-name prefixing). Defaults keep the funnel identical
    # for providers that don't set them (Cloud/Telegram).
    #   display_text   — operator/LLM-facing text (e.g. "[Fulano]: olá @Bot"); when
    #                    empty the funnel uses ``text``.
    #   trigger_ai     — False = save + broadcast but do NOT run the agent (a group
    #                    message with no @mention under group_reply_mode=mention_only).
    #   reply_to_msg_id— quoted/replied message id (GOWA nests it inconsistently).
    #   mentioned      — the bot was @mentioned in a group (raises the panel flag).
    display_text: str = ""
    trigger_ai: bool = True
    reply_to_msg_id: Optional[str] = None
    mentioned: bool = False
    # Contact-level metadata a provider may resolve about the chat (GOWA resolves
    # these from its client). ``None`` = "not resolved, leave the contact as is".
    group_name: Optional[str] = None
    can_send: Optional[bool] = None
    is_archived: Optional[bool] = None
    # ── Non-actionable SYSTEM inbound (plano 82) ─────────────────────────────
    # ``kind="system"`` is a channel LIFECYCLE notice about the chat itself, not a
    # message from the counterpart — e.g. the WhatsApp Cloud API ``type: system``
    # (``user_changed_number``/``customer_identity_changed``), or Telegram's future
    # ``migrate_to_chat_id``. The core dispatch (``_dispatch_events`` in
    # server/routes/channel_webhook.py) writes it as a PANEL-ONLY card
    # (``conversation_event`` role) attached to the contact's EXISTING conversation
    # and NOTHING else:
    #   • does NOT create/reopen a conversation (a system event never opens a thread);
    #   • does NOT materialize a new contact (only surfaces if the contact exists);
    #   • does NOT run the agent (the ``conversation_event`` role is already
    #     black-listed from the LLM context / sidebar / unread badge);
    #   • does NOT emit ``message.saved``/``message.received`` — so automation
    #     plugins (protocolos) never fire. It emits the distinct, opt-in bus event
    #     ``channel.system_event`` instead, for plugins that WANT to react.
    # The provider only DECLARES the kind; the gate lives entirely in the core
    # (policy-vs-mechanism, no ``if provider ==``). Providers should populate
    # ``media_extras`` with the structured subtype so subscribers get more than the
    # rendered ``text``:
    #   media_extras = {"system_type": str, "wa_id": str | None, "body": str}
    # and set ``text`` to the human-facing card line (Cloud uses ``describe_system``,
    # prefixed with ``ℹ️``). ``chat_id`` is the OLD/current chat identifier (for
    # ``user_changed_number`` that is the old number); the new identity, when the
    # provider knows it, rides in ``media_extras["wa_id"]``.

    def __post_init__(self) -> None:
        """Coage ``ts`` para float — o contrato, não só o parser (plano 141).

        A anotação ``ts: float`` de uma dataclass **não é enforcement**: o GOWA
        injetava uma string RFC 3339 aqui e ela viajava intacta até o INSERT em
        ``messages.ts`` (``double precision``), onde derrubava o save e destruía
        a mensagem do cliente em silêncio. O parser corrigido resolve ESTE
        payload; esta camada impede que QUALQUER provider — inclusive plugin de
        terceiro, que o core não revisa — volte a deixar um não-float escapar.

        ⚠️ O escopo é o TIPO, não o formato: aqui um RFC 3339 é "ininterpretável"
        e vira ``0.0`` (⇒ ``time.time()`` a jusante), porque reparsear data é
        trabalho do parser do provider, que é quem conhece a convenção dele.
        A garantia desta linha é só esta, e é a que importa: **carimbo estranho
        nunca custa a mensagem**.

        Nunca levanta — um provider mal-comportado não pode derrubar o webhook.
        """
        if isinstance(self.ts, float):
            return
        try:
            self.ts = 0.0 if isinstance(self.ts, bool) else float(self.ts)
        except (TypeError, ValueError):
            self.ts = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
