"""Channel abstraction (plano 02 Fase 0).

Provider-agnostic messaging channel contract + registry. The core talks to a
``Channel`` through the ``ChannelRegistry`` instead of the GOWA client directly,
so additional providers (WhatsApp Cloud API, Telegram, …) plug in without
``if provider == "gowa"`` in the handler.
"""

from channels.base import Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent
from channels.registry import ChannelRegistry

__all__ = [
    "Channel", "ChannelCapabilities", "SendResult", "InboundEvent",
    "ChannelRegistry",
]
