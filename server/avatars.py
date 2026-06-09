"""Avatar cache helpers.

Profile photos are cached on disk at ``statics/avatars/<phone>.jpg`` and served
to the frontend via the static mount. Because WhatsApp gives no reliable
"photo changed" event, freshness is handled by re-fetching from GOWA (on
conversation open and on a periodic background sweep) and overwriting the cached
file only when the bytes actually differ. The frontend cache-busts using the
file mtime (``avatar_v``), so a changed photo is picked up without a manual
reload; an ``avatar_updated`` WebSocket event updates it live.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def avatars_dir(settings):
    d = settings.data_dir / "statics" / "avatars"
    d.mkdir(parents=True, exist_ok=True)
    return d


def avatar_version(settings, phone: str) -> int:
    """Cache-busting version for a contact's avatar = file mtime (0 if missing)."""
    if not phone:
        return 0
    try:
        return int((avatars_dir(settings) / f"{phone}.jpg").stat().st_mtime)
    except OSError:
        return 0


async def refresh_avatar(deps, phone: str) -> bool:
    """Fetch the current avatar from GOWA and overwrite the cached file if it is
    new or has changed. Returns True when the file was created/updated.

    Best-effort: never raises. When GOWA returns no avatar, the existing cache
    (if any) is kept untouched.
    """
    if not phone:
        return False
    path = avatars_dir(deps.settings) / f"{phone}.jpg"
    try:
        data = await asyncio.to_thread(deps.gowa_client.get_avatar, phone)
    except Exception as e:
        logger.debug("[Avatar] refresh fetch failed for %s: %s", phone, e)
        return False
    if not data or not isinstance(data, bytes):
        return False
    try:
        if path.exists() and path.read_bytes() == data:
            return False
        path.write_bytes(data)
        logger.info("[Avatar] Updated avatar for %s", phone)
        return True
    except Exception as e:
        logger.debug("[Avatar] write failed for %s: %s", phone, e)
        return False


async def refresh_and_broadcast(deps, phone: str) -> bool:
    """Refresh a single avatar; if it changed, broadcast ``avatar_updated`` with
    the new version so connected clients update without a reload."""
    changed = await refresh_avatar(deps, phone)
    if changed:
        try:
            await deps.ws_manager.broadcast(
                "avatar_updated",
                {"phone": phone, "v": avatar_version(deps.settings, phone)},
            )
        except Exception as e:
            logger.debug("[Avatar] broadcast failed for %s: %s", phone, e)
    return changed
