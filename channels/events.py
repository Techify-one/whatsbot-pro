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

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
