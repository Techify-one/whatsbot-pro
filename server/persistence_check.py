"""Boot-time storage persistence self-check (deploy safeguard).

Detects the exact failure mode behind "plugins vanished after a Coolify redeploy":
the container filesystem was recreated (wiping ``storages/plugins/`` and the GOWA
session) while the external Postgres survived — i.e. ``/app/storages`` is NOT on a
persistent volume.

We detect it WITHOUT inspecting mount tables, via a sentinel token stored in BOTH
a file inside ``storages/`` (the thing whose persistence we test) and the ``config``
table in Postgres (known-persistent). A mismatch means the disk was wiped since the
last boot while the DB survived ⇒ NOT persistent ⇒ loud, actionable warning.

Fail-open: any error is logged and the boot proceeds. No-op under ``WHATSBOT_TEST``
so the suite never writes a marker into the repo's ``storages/`` dir (same guard as
``plugins.bootstrap.bootstrap_gowa_upgrade``); the logic is tested by calling
:func:`ensure_storage_persistence` directly with a tmp dir.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Config key (Postgres side of the sentinel) + marker filename (disk side).
_CONFIG_KEY = "storages_persistence_token"
_MARKER_NAME = ".persistence_marker"

# Most recent boot verdict, exposed via GET /api/admin/database. One of
# "persistent" | "ephemeral" | "initialized" | "unknown"; None until checked.
_LAST_STATUS: str | None = None


def last_status() -> str | None:
    """The most recent persistence verdict (None until the boot check runs)."""
    return _LAST_STATUS


def is_persistent() -> bool | None:
    """Tri-state for the admin diagnostics endpoint.

    "persistent"/"initialized" (no wipe detected) ⇒ ``True``; "ephemeral" (disk was
    wiped) ⇒ ``False``; "unknown" or not-yet-checked ⇒ ``None``.
    """
    if _LAST_STATUS in ("persistent", "initialized"):
        return True
    if _LAST_STATUS == "ephemeral":
        return False
    return None


def _new_token() -> str:
    return f"{uuid.uuid4().hex}-{int(time.time())}"


def _read_marker(marker: Path) -> str | None:
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
    except OSError as e:  # unreadable marker — treat as absent, but log it
        logger.warning("persistence-check: não consegui ler a marca %s: %s", marker, e)
        return None


def _write_marker(marker: Path, token: str) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(token, encoding="utf-8")


def ensure_storage_persistence(storages_dir: Path) -> str:
    """Compare a disk marker against the DB token; warn if the disk was wiped.

    Returns the verdict string and caches it in module state (see
    :func:`last_status` / :func:`is_persistent`). Never raises — fail-open so the
    boot is never blocked by this diagnostic. No-op under ``WHATSBOT_TEST``.
    """
    global _LAST_STATUS
    if os.environ.get("WHATSBOT_TEST"):
        _LAST_STATUS = "unknown"
        return _LAST_STATUS
    try:
        _LAST_STATUS = _run_check(Path(storages_dir))
    except Exception as e:  # noqa: BLE001 — a diagnostic must never break boot
        logger.warning("persistence-check: ignorada por erro: %s", e)
        _LAST_STATUS = "unknown"
    return _LAST_STATUS


def _run_check(storages_dir: Path) -> str:
    from db.repositories import config_repo

    marker = storages_dir / _MARKER_NAME
    db_token = config_repo.get(_CONFIG_KEY)
    file_token = _read_marker(marker)

    # First boot ever: nothing to compare — establish the token in both places.
    if not db_token and not file_token:
        token = _new_token()
        _write_marker(marker, token)
        config_repo.set(_CONFIG_KEY, token)
        logger.info("persistence-check: marca inicializada em %s (1º boot).", marker)
        return "initialized"

    # DB was reset but the disk survived: adopt the disk token (not the failure we
    # hunt — this means storage IS persistent, the DB is the one that changed).
    if not db_token and file_token:
        config_repo.set(_CONFIG_KEY, file_token)
        logger.info("persistence-check: token do DB re-semeado a partir do disco (banco resetado?).")
        return "persistent"

    # Happy path: disk survived across boots and matches the DB token.
    if file_token and file_token == db_token:
        logger.info("persistence-check: storages/ persistente (marca confere).")
        return "persistent"

    # DB token present, disk marker missing or different ⇒ the filesystem was wiped
    # while Postgres persisted. THIS is the Coolify "no Persistent Storage" failure.
    logger.error(
        "persistence-check: storages/ NÃO é persistente! O disco foi zerado desde o "
        "último boot (marca ausente/divergente) enquanto o banco sobreviveu. Plugins "
        "instalados e a sessão do WhatsApp serão PERDIDOS a cada redeploy. Configure "
        "Persistent Storage no Coolify mapeando /app/storages e /app/statics para um "
        "volume persistente. Ver docs/DEPLOY_COOLIFY.md."
    )
    # Re-anchor the marker so state is consistent within THIS container; it vanishes
    # again on the next ephemeral redeploy and warns again (that's the point).
    try:
        _write_marker(marker, db_token)
    except OSError as e:
        logger.warning("persistence-check: não consegui regravar a marca: %s", e)
    return "ephemeral"
