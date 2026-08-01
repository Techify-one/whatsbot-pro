"""Regression tests for the plugin filter catalogue (plano 100 F0)."""

from __future__ import annotations

import logging

from plugins import events


def test_removed_unknown_media_filter_is_not_advertised(caplog):
    """The dead seam must warn instead of remaining a silent false promise."""
    name = "filter.media.unknown"
    assert name not in events.KNOWN_FILTERS

    # Alembic's logging.fileConfig runs during PostgreSQL bootstrap with
    # disable_existing_loggers=True, so a module imported at collection time may
    # be disabled by the time this test executes in a mixed DB suite.
    previous_disabled = events.logger.disabled
    events.logger.disabled = False
    caplog.set_level(logging.WARNING, logger=events.__name__)
    events.register_filter("legacy_media_plugin", name, lambda _ctx, value: value)
    try:
        assert "registered unknown filter" in caplog.text
        assert name in caplog.text
    finally:
        bucket = events._filters.get(name, [])
        events._filters[name] = [
            item for item in bucket if item[1] != "legacy_media_plugin"
        ]
        if not events._filters[name]:
            events._filters.pop(name, None)
        events.logger.disabled = previous_disabled
