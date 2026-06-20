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
    kind: str = "message"                 # message | reaction | receipt | presence | ...
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

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
