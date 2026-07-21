"""Per-channel inbound mode for Telegram channels (webhook vs long-poll).

Each Telegram inbox decides INDEPENDENTLY how it receives updates, stored in
``channels.config["telegram"]["inbound_mode"]`` (``"webhook"`` or ``"poll"``).
This replaces the old GLOBAL ``plugin.telegram.inbound_mode`` switch, which broke
when two inboxes needed different modes (a bot's webhook blocks ``getUpdates`` for
the WHOLE bot, so a global flag forced every channel into the same mode).

Resolution order for a channel with no per-channel value set:
  1. legacy global ``plugin.telegram.inbound_mode`` (back-compat for old installs)
  2. ``"poll"`` (safe default — works on desktop/EXE with no public host)

The poll loop reads the mode each cycle and SKIPS channels in ``webhook`` mode
(their updates arrive via the core's ``/api/webhook/telegram/{channel_id}`` route),
so switching a channel's mode takes effect WITHOUT a restart.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

VALID_MODES = ("poll", "webhook")


def _global_default() -> str:
    """Legacy global fallback (``plugin.telegram.inbound_mode``)."""
    try:
        from db.repositories import config_repo
        val = config_repo.get("plugin.telegram.inbound_mode")
        if val in VALID_MODES:
            return val
    except Exception:  # noqa: BLE001
        pass
    return "poll"


def _parse_config(cfg) -> dict:
    if isinstance(cfg, str) and cfg:
        try:
            cfg = json.loads(cfg)
        except Exception:  # noqa: BLE001
            return {}
    return cfg if isinstance(cfg, dict) else {}


def mode_from_row(row: dict | None) -> str:
    """Effective mode from an already-loaded channel row (no DB hit).

    The poll loop calls this with rows from ``registry.list_channels()`` so it
    doesn't issue an extra query per channel per cycle."""
    cfg = _parse_config((row or {}).get("config"))
    tg = cfg.get("telegram")
    if isinstance(tg, dict):
        m = tg.get("inbound_mode")
        if m in VALID_MODES:
            return m
    return _global_default()


def get_mode(channel_id: str) -> str:
    """Effective inbound mode for a channel ('webhook' or 'poll')."""
    try:
        from db.repositories import channel_repo
        return mode_from_row(channel_repo.get(channel_id))
    except Exception:  # noqa: BLE001
        return _global_default()


def set_mode(channel_id: str, mode: str) -> None:
    """Persist a channel's inbound mode into ``channels.config['telegram']``.

    Read-modify-write of the JSON config column, preserving sibling keys (e.g.
    the per-channel ``ai`` overrides)."""
    if mode not in VALID_MODES:
        raise ValueError(f"invalid telegram inbound mode: {mode!r}")
    from db.repositories import channel_repo
    row = channel_repo.get(channel_id)
    if row is None:
        raise ValueError(f"unknown channel: {channel_id!r}")
    cfg = _parse_config(row.get("config"))
    tg = cfg.get("telegram")
    if not isinstance(tg, dict):
        tg = {}
    tg["inbound_mode"] = mode
    cfg["telegram"] = tg
    channel_repo.update(channel_id, config=json.dumps(cfg))
    logger.info("telegram: channel %s inbound_mode -> %s", channel_id, mode)
