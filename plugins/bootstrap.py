"""Bundled-plugin bootstrap + the GOWA upgrade path (plano 23 Fase C4).

Extracted from ``plugins.loader`` so the loader stays focused on
discovery/import/wiring and the hardcoded GOWA install/enable logic lives in one
place. Behavior is identical — including the ``WHATSBOT_TEST`` no-op guard on
:func:`bootstrap_gowa_upgrade`. ``plugins.loader`` re-exports these names, so any
caller importing them from ``plugins.loader`` keeps working.

Two entry points:

* :func:`bootstrap_initial_plugins` — first-run copy of the bundled example
  plugins into ``storages/plugins`` (only when that dir is empty), with GOWA
  enabled-by-default.
* :func:`bootstrap_gowa_upgrade` — idempotently install + enable the bundled
  GOWA plugin on an EXISTING install that predates the plugin (guarded by the
  WHATSBOT_TEST env, the uninstall tombstone, and a ``target.exists()`` check).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from plugins.manifest import load_manifest

logger = logging.getLogger(__name__)


# Which bundled example plugins a FRESH install auto-installs (plano 33 D3). Only
# GOWA — the default WhatsApp channel — comes automatic; every other provider
# (telegram, whatsapp_cloud) and example plugin is IMPORT-ONLY (via "Importar
# (.zip)" on the Plugins screen). Their source stays in ``assets/plugin_examples/``
# so the frontend descriptor renders them once imported and the importable zips
# can be built from it — but a clean boot no longer copies them.
BUNDLED_AUTO_INSTALL = ("gowa",)


def bootstrap_initial_plugins(plugins_dir: Path, source_dir: Path) -> list[str]:
    """Copy the auto-install bundled plugins into ``plugins_dir`` on first run.

    Runs only when ``plugins_dir`` is empty (no subdirectories) so user deletions
    stick across restarts. Only the plugins in :data:`BUNDLED_AUTO_INSTALL` (GOWA)
    are copied — a fresh install is born with just the default WhatsApp channel;
    telegram/whatsapp_cloud are imported on demand (plano 33 D3). Subsequent core
    updates never overwrite a user's installed plugins.
    """
    plugins_dir.mkdir(parents=True, exist_ok=True)
    has_anything = any(c.is_dir() and not c.name.startswith(".") for c in plugins_dir.iterdir())
    if has_anything:
        return []
    if not source_dir.is_dir():
        return []
    gowa_tombstoned = _gowa_is_tombstoned()
    copied: list[str] = []
    for name in BUNDLED_AUTO_INSTALL:
        child = source_dir / name
        if not child.is_dir():
            continue
        # Honor a deliberate gowa uninstall even when the user emptied
        # storages/plugins by removing every plugin (which would otherwise make
        # this "fresh install" copy gowa back and re-enable it — plano 13 goal #2).
        if name == "gowa" and gowa_tombstoned:
            logger.info("Skipping bundled gowa bootstrap: user uninstalled it (tombstone)")
            continue
        target = plugins_dir / name
        if target.exists():
            continue
        shutil.copytree(child, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copied.append(name)
        logger.info("Bootstrapped initial plugin: %s -> %s", name, target)
        # GOWA is bundled ACTIVE by default (plano 13 D-A): a fresh download has
        # WhatsApp working immediately. discover_and_load (which runs next)
        # preserves this enabled flag (upsert with enabled=None).
        if name == "gowa":
            _enable_bundled_gowa(target)
    return copied


def _gowa_is_tombstoned() -> bool:
    """True if the user deliberately UNINSTALLED the bundled gowa plugin.

    Uninstall (``DELETE /api/plugins/gowa``) writes config ``gowa_uninstalled=1``
    OUTSIDE the ``plugin.gowa.`` prefix (which the same delete wipes), so neither
    bootstrap path resurrects GOWA after a deliberate removal (plano 13 goal #2:
    "uninstalling a channel makes it stay gone"). The ``default`` gowa channel row
    persists, so without this tombstone ``bootstrap_gowa_upgrade`` /
    ``bootstrap_initial_plugins`` would re-copy + re-enable it on the next boot —
    the very restart the uninstall itself schedules. Cleared on an explicit
    reinstall via the Plugins import.
    """
    try:
        from db.repositories import config_repo
        return str(config_repo.get("gowa_uninstalled") or "").strip().lower() in ("1", "true")
    except Exception:  # noqa: BLE001
        return False


def _enable_bundled_gowa(target: Path) -> None:
    """Mark the bundled gowa plugin enabled=1 (its row is created here, before
    discover_and_load, which then preserves the flag)."""
    try:
        from db.repositories import plugin_repo
        ver = "1.0.0"
        try:
            ver = load_manifest(target).version
        except Exception:  # noqa: BLE001
            pass
        plugin_repo.upsert("gowa", ver, enabled=True)
        logger.info("Bundled gowa plugin enabled by default")
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not enable bundled gowa plugin: %s", e)


def bootstrap_gowa_upgrade(plugins_dir: Path, source_dir: Path) -> bool:
    """Idempotently install + enable the bundled gowa plugin on EXISTING installs.

    Fresh installs get gowa via :func:`bootstrap_initial_plugins` (which only runs
    when ``plugins_dir`` is empty). An install that already had ``storages/plugins``
    populated BEFORE the gowa plugin existed would otherwise never receive it. So:
    if this install actually uses GOWA (a ``default`` channel with provider
    ``gowa`` exists) and ``storages/plugins/gowa`` is missing, copy it from the
    bundled source and enable it — exactly once (guarded by ``target.exists()``).

    Returns True iff it copied+enabled this call. Test-guarded: the suite's
    Settings() data_dir is the repo root, so without ``WHATSBOT_TEST`` this would
    mutate the real (git-ignored) ``storages/plugins`` and change create_app.
    """
    if os.environ.get("WHATSBOT_TEST"):
        return False
    # A deliberate uninstall is sticky: never auto-reinstall (plano 13 goal #2).
    # The 'default' gowa channel row persists after uninstall, so the channel-row
    # guard below would otherwise resurrect GOWA on the very next boot.
    if _gowa_is_tombstoned():
        return False
    target = plugins_dir / "gowa"
    if target.exists():
        return False
    src = source_dir / "gowa"
    if not src.is_dir():
        return False
    # Only for installs that actually use GOWA (don't resurrect it on a fresh
    # Cloud-only / Telegram-only setup that has no default gowa channel).
    try:
        from db.repositories import channel_repo
        rows = channel_repo.list_all()
        if not any(r.get("id") == "default" and r.get("provider") == "gowa" for r in rows):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        _enable_bundled_gowa(target)
        logger.info("Upgrade bootstrap: installed + enabled bundled gowa plugin")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("gowa upgrade bootstrap failed: %s", e)
        return False
