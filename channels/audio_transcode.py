"""Best-effort audio re-encode to a channel-safe container/codec.

The audio sibling of ``channels.video_transcode`` and pure MECHANISM: the target
container comes from the ``AudioLimits`` the CHANNEL declared
(``transcode_targets``, else ``extensions``) and the bitrate from the cap it
declared — nothing here is a provider's number, and no provider name is checked.

Guarded by ``shutil.which("ffmpeg")`` so it is a no-op when ffmpeg is absent; the
caller then blocks the send with the validator's message, exactly like the video
path degrades.

Typical save: an Ogg/Vorbis file (which the WhatsApp Cloud API refuses — it only
accepts Ogg with OPUS) is re-encoded to Ogg/Opus instead of failing at the provider.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from channels.base import AudioLimits
from channels.video_transcode import _probe_meta, available

logger = logging.getLogger(__name__)

# Defensive input ceilings — a pathological upload never pins the worker.
MAX_INPUT_BYTES = 200 * 1024 * 1024        # 200 MB
MAX_INPUT_DURATION_S = 3 * 60 * 60         # 3 hours
FFMPEG_TIMEOUT_S = 180

# Leave headroom below the declared cap for container overhead.
_HEADROOM = 0.92
_MIN_KBPS = 24
_MAX_KBPS = 128
_MONO_BELOW_KBPS = 48                      # below this, stereo is wasteful

# Encoders the core knows how to produce, per container. MECHANISM (what ffmpeg
# can build), not policy (what a channel accepts) — the intersection with the
# provider's declared containers is what actually gets used.
_ENCODERS = {
    ".ogg": ("libopus", ()),
    ".opus": ("libopus", ()),
    ".m4a": ("aac", ()),
    ".mp4": ("aac", ()),
    ".aac": ("aac", ("-f", "adts")),
    ".mp3": ("libmp3lame", ()),
}

# Preference order when the provider does not state one: Opus first (best voice
# quality per byte and WhatsApp's own voice-note format), then AAC, then MP3.
_DEFAULT_PREFERENCE = (".ogg", ".m4a", ".mp3", ".aac", ".mp4", ".opus")


def _target_ext(limits: AudioLimits) -> Optional[str]:
    """The container to encode into: first provider-preferred one we can encode."""
    declared = tuple(limits.transcode_targets or limits.extensions or ())
    for ext in declared:
        if str(ext).lower() in _ENCODERS:
            return str(ext).lower()
    # Provider stated containers we cannot encode (e.g. AMR only) — give up rather
    # than silently sending something it did not declare.
    if declared:
        return None
    for ext in _DEFAULT_PREFERENCE:
        if ext in _ENCODERS:
            return ext
    return None


def _bitrate_kbps(duration: float, max_bytes: int) -> int:
    """Bitrate that fits ``duration`` under ``max_bytes``, clamped to sane bounds."""
    if not duration or duration <= 0 or not max_bytes:
        return 64
    kbps = int((max_bytes * _HEADROOM * 8) / duration / 1000.0)
    return max(_MIN_KBPS, min(_MAX_KBPS, kbps))


def transcode_to_limits(path: str, limits: Optional[AudioLimits] = None) -> Optional[str]:
    """Re-encode ``path`` to fit ``limits`` and return the temp file path.

    Returns ``None`` when ffmpeg is absent, the provider forbids re-encoding, no
    declared container is encodable, the input exceeds the safety ceilings, or the
    result still does not fit — the caller then blocks the send. The caller owns
    cleanup of the returned temp file.
    """
    if limits is None or not isinstance(limits, AudioLimits):
        return None
    if not limits.transcode or not available():
        return None
    target = _target_ext(limits)
    if not target:
        return None
    try:
        if os.path.getsize(path) > MAX_INPUT_BYTES:
            logger.info("audio_transcode: input over %d bytes, refusing", MAX_INPUT_BYTES)
            return None
    except OSError:
        return None

    duration = (_probe_meta(path) or {}).get("duration") or 0.0
    if duration and duration > MAX_INPUT_DURATION_S:
        logger.info("audio_transcode: input longer than %ds, refusing", MAX_INPUT_DURATION_S)
        return None

    encoder, extra_args = _ENCODERS[target]
    kbps = _bitrate_kbps(duration, limits.max_bytes)
    channels = "1" if kbps < _MONO_BELOW_KBPS else "2"
    out = os.path.join(
        tempfile.gettempdir(),
        f"wa_aud_{Path(path).stem}_{int(time.time() * 1000)}{target}")

    cmd = ["ffmpeg", "-y", "-i", path, "-vn", "-map_metadata", "-1",
           "-c:a", encoder, "-b:a", f"{kbps}k", "-ac", channels,
           *extra_args, out]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=FFMPEG_TIMEOUT_S)
        if proc.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0 \
                and (not limits.max_bytes or os.path.getsize(out) <= limits.max_bytes):
            logger.info("audio_transcode: %s -> %s (%dk, %s ch)",
                        Path(path).name, Path(out).name, kbps, channels)
            return out
    except Exception:  # noqa: BLE001
        logger.warning("audio_transcode ffmpeg failed for %s", path, exc_info=True)
    if os.path.isfile(out):
        try:
            os.remove(out)
        except OSError:
            pass
    return None
