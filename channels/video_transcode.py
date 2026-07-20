"""Best-effort video transcode to WhatsApp-Cloud-safe MP4 (plano 65 F5B).

Mirrors the graceful-degradation pattern of ``whatsapp_cloud._gif_to_mp4``:
guarded by ``shutil.which("ffmpeg")`` so it is a no-op when ffmpeg is absent
(the production image gets ffmpeg via the Dockerfile — plano 65). When ffmpeg
is missing the caller falls back to blocking the send with a clear message
(plano 65 F5A).

The target is MP4 H.264/AAC ≤ 16 MB. Bitrate is computed from the input
duration so the output fits under the Cloud limit, with a downscale cap for
oversized frames. Input is bounded (duration/size teto) so a pathological file
never pins the worker.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from channels.video_validate import MAX_VIDEO_BYTES

logger = logging.getLogger(__name__)

# Defensive input ceilings: refuse to even try transcoding huge/long inputs so a
# request never blocks the worker for minutes (plano 65 §5 / F5B).
MAX_INPUT_BYTES = 200 * 1024 * 1024        # 200 MB
MAX_INPUT_DURATION_S = 300                 # 5 minutes
FFMPEG_TIMEOUT_S = 180

# Leave headroom below the hard 16 MB cap so container/muxing overhead still fits.
_TARGET_BYTES = int(MAX_VIDEO_BYTES * 0.92)
_AUDIO_BITRATE_KBPS = 128
_MIN_VIDEO_BITRATE_KBPS = 200
_MAX_LONG_EDGE = 1280                       # downscale cap (720p-ish long edge)


def available() -> bool:
    """True when ffmpeg is on PATH (transcoding is possible)."""
    return bool(shutil.which("ffmpeg"))


def _probe_meta(path: str) -> Optional[dict]:
    """Return ``{duration, width, height}`` via ffprobe, or ``None`` on failure."""
    if not shutil.which("ffprobe"):
        return None
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or b"{}")
        duration = 0.0
        try:
            duration = float((data.get("format") or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        width = height = 0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                width = int(s.get("width") or 0)
                height = int(s.get("height") or 0)
                break
        return {"duration": duration, "width": width, "height": height}
    except Exception:  # noqa: BLE001
        logger.warning("video_transcode ffprobe failed for %s", path, exc_info=True)
        return None


def transcode_to_cloud_mp4(path: str) -> Optional[str]:
    """Transcode ``path`` to an MP4 H.264/AAC ≤ 16 MB and return the temp path.

    Returns ``None`` when ffmpeg is absent, the input exceeds the safety ceilings,
    or the transcode fails/does not fit — the caller then blocks the send (F5A).
    The caller owns cleanup of the returned temp file.
    """
    if not available():
        return None
    try:
        if os.path.getsize(path) > MAX_INPUT_BYTES:
            logger.info("video_transcode: input over %d bytes, refusing", MAX_INPUT_BYTES)
            return None
    except OSError:
        return None

    meta = _probe_meta(path)
    duration = (meta or {}).get("duration") or 0.0
    if duration and duration > MAX_INPUT_DURATION_S:
        logger.info("video_transcode: input longer than %ds, refusing", MAX_INPUT_DURATION_S)
        return None

    # Target video bitrate so audio+video fit under _TARGET_BYTES. Fall back to a
    # conservative constant when the duration is unknown.
    if duration and duration > 0:
        total_kbps = (_TARGET_BYTES * 8) / duration / 1000.0
        video_kbps = int(total_kbps - _AUDIO_BITRATE_KBPS)
    else:
        video_kbps = 1200
    if video_kbps < _MIN_VIDEO_BITRATE_KBPS:
        video_kbps = _MIN_VIDEO_BITRATE_KBPS

    out = os.path.join(
        tempfile.gettempdir(),
        f"wac_vid_{Path(path).stem}_{int(time.time() * 1000)}.mp4")

    # Downscale only when the long edge is oversized; keep aspect ratio and even
    # dimensions (yuv420p + even scale = broad WhatsApp compatibility).
    scale = (f"scale='if(gt(iw,ih),min({_MAX_LONG_EDGE},iw),-2)'"
             f":'if(gt(iw,ih),-2,min({_MAX_LONG_EDGE},ih))'"
             ",scale=trunc(iw/2)*2:trunc(ih/2)*2")

    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.2)}k",
        "-bufsize", f"{int(video_kbps * 2)}k",
        "-pix_fmt", "yuv420p", "-vf", scale,
        "-c:a", "aac", "-b:a", f"{_AUDIO_BITRATE_KBPS}k", "-ac", "2",
        "-movflags", "+faststart", out,
    ]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=FFMPEG_TIMEOUT_S)
        if proc.returncode == 0 and os.path.isfile(out) \
                and 0 < os.path.getsize(out) <= MAX_VIDEO_BYTES:
            return out
        # Did not fit or failed — discard the partial output.
        if os.path.isfile(out):
            try:
                os.remove(out)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        logger.warning("video_transcode ffmpeg failed for %s", path, exc_info=True)
        if os.path.isfile(out):
            try:
                os.remove(out)
            except OSError:
                pass
    return None
