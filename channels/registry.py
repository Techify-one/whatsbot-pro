"""Channel registry (plano 02 Fase 0).

Two layers (pesquisa §3.4.2):
  1. provider_name -> Provider class (populated by core + plugins via entry.channels)
  2. channel_id -> live Channel instance

Also the single read/write API to the ``channels`` / ``channel_credentials``
tables that providers use — providers never touch those tables by raw SQL (P24).
Credential masking for the API boundary lives in ``server/routes/channels.py``
(P15 — no encryption in the MVP, just masking).
"""

from __future__ import annotations

import logging
from typing import Optional

from db.repositories import channel_repo, channel_credential_repo

logger = logging.getLogger(__name__)


class ChannelRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, type] = {}
        self._channels: dict[str, object] = {}

    # ── Layer 1: provider classes ────────────────────────────────────
    def register_provider(self, cls: type) -> None:
        name = getattr(cls, "provider", None)
        if not name:
            logger.warning("register_provider: class %r has no 'provider' attr", cls)
            return
        if name in self._providers:
            logger.warning("Channel provider %r already registered; replacing", name)
        self._providers[name] = cls
        logger.info("Registered channel provider: %s", name)

    def unregister_provider(self, name: str) -> None:
        self._providers.pop(name, None)

    def get_provider(self, name: str) -> Optional[type]:
        return self._providers.get(name)

    def providers(self) -> list[str]:
        return list(self._providers)

    # ── Layer 2: live channel instances ──────────────────────────────
    def add_channel(self, channel_id: str, channel: object) -> None:
        self._channels[channel_id] = channel

    def remove_channel(self, channel_id: str) -> None:
        self._channels.pop(channel_id, None)

    def get(self, channel_id: str) -> Optional[object]:
        return self._channels.get(channel_id)

    def all_channels(self) -> dict[str, object]:
        return dict(self._channels)

    # ── DB access API for providers (P24) ────────────────────────────
    def get_channel(self, channel_id: str) -> Optional[dict]:
        return channel_repo.get(channel_id)

    def list_channels(self) -> list[dict]:
        return channel_repo.list_all()

    def set_status(self, channel_id: str, **fields) -> None:
        channel_repo.set_status(channel_id, **fields)

    def get_credential(self, channel_id: str, key: str) -> Optional[str]:
        return channel_credential_repo.get(channel_id, key)

    def set_credential(self, channel_id: str, key: str, value: str) -> None:
        channel_credential_repo.set(channel_id, key, value)
