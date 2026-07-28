"""Generic video validation against provider-declared limits (plano 65).

Policy vs mechanism: the numbers (max bytes, container extensions, codecs) are
NOT the core's — they are declared by each provider in
``ChannelCapabilities.media_limits["video"]`` as a ``VideoLimits`` (see
``channels.base``). This module only *evaluates* a file against whatever limits
the channel declared. It never checks provider name, and a channel that declares
no video limits (GOWA/Telegram — they deliver arbitrary mp4 as a file) gets an
immediate permissive verdict.

Retrocompat: a provider plugin built before ``media_limits`` existed declares
nothing, so a *windowed* channel (``session_window_hours > 0``, i.e. the WhatsApp
Cloud API) falls back to ``LEGACY_CLOUD_VIDEO_LIMITS`` below — an older installed
copy of the whatsapp_cloud plugin keeps being validated and transcoded exactly as
before. New/updated plugins declare their own and the fallback is never reached.

Codec/audio-stream checks require ``ffprobe`` on PATH. When it is absent,
validation degrades to extension + size only, exactly like ``_gif_to_mp4``
degrades without ffmpeg. A codec the panel could not inspect then falls to the
Meta ``131053`` error, which the caller surfaces as a friendly message (F5A).
"""

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

from channels.base import VideoLimits

logger = logging.getLogger(__name__)

# Retrocompat fallback ONLY (see module docstring): the WhatsApp Cloud limits, used
# for windowed channels whose provider plugin does not declare ``media_limits`` yet.
# The authoritative copy lives in the whatsapp_cloud plugin.
LEGACY_CLOUD_VIDEO_LIMITS = VideoLimits(
    max_bytes=16 * 1024 * 1024,
    extensions=(".mp4", ".3gp", ".3gpp"),
    video_codecs=("h264",),
    audio_codecs=("aac",),
    max_audio_streams=1,
)

# Kept as a module constant for callers/tests that reference the legacy cap.
MAX_VIDEO_BYTES = LEGACY_CLOUD_VIDEO_LIMITS.max_bytes

# Structured rejection reasons (the route maps these to HTTP status + PT-BR text).
OK = "ok"
TOO_BIG = "too_big"
BAD_FORMAT = "bad_format"
BAD_CODEC = "bad_codec"


@dataclass
class VideoVerdict:
    reason: str                 # one of OK / TOO_BIG / BAD_FORMAT / BAD_CODEC
    message: str = ""           # PT-BR, user-facing (empty when OK)
    codec_checked: bool = False  # True only when ffprobe actually inspected codecs

    @property
    def ok(self) -> bool:
        return self.reason == OK


def video_limits(caps) -> VideoLimits | None:
    """The video policy for a channel, or ``None`` when it imposes none.

    Resolution order: what the provider declared → legacy Cloud fallback for a
    windowed provider that predates ``media_limits`` → no limits. Never keys off
    provider name.
    """
    declared = (getattr(caps, "media_limits", None) or {}).get("video")
    if declared is not None:
        return declared
    if getattr(caps, "session_window_hours", 0):
        return LEGACY_CLOUD_VIDEO_LIMITS
    return None


def is_windowed(caps) -> bool:
    """Deprecated alias kept for callers written before ``video_limits`` (plano 65).

    True when the channel enforces a customer-care session window."""
    return bool(getattr(caps, "session_window_hours", 0))


def _fmt_size(num_bytes: int) -> str:
    """Human size for the rejection message (the cap comes from the provider, so
    it is not necessarily a round number of MB)."""
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    mb = num_bytes / (1024 * 1024)
    return f"{mb:.0f} MB" if abs(mb - round(mb)) < 0.05 else f"{mb:.1f} MB"


def _ffprobe_streams(path: str) -> list[dict] | None:
    """Return ffprobe's stream list, or ``None`` when ffprobe is absent/fails."""
    if not shutil.which("ffprobe"):
        return None
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or b"{}")
        streams = data.get("streams")
        return streams if isinstance(streams, list) else None
    except Exception:  # noqa: BLE001
        logger.warning("video_validate ffprobe failed for %s", path, exc_info=True)
        return None


def validate_video(path: str, caps) -> VideoVerdict:
    """Validate ``path`` against the video limits the channel declares.

    A channel with no declared limits gets an immediate ``OK``. Otherwise the file
    is checked for extension, size, and — when ffprobe is available — video/audio
    codec and audio-stream count.
    """
    limits = video_limits(caps)
    if limits is None:
        return VideoVerdict(OK)

    # A provider may declare a plain ``MediaLimits`` for "video" (size/container
    # only — Messenger/Instagram accept any common container). Read the codec
    # fields through ``getattr`` so that stays a valid, codec-free policy instead
    # of blowing up here and failing every video send.
    allowed_video_codecs = tuple(getattr(limits, "video_codecs", ()) or ())
    allowed_audio_codecs = tuple(getattr(limits, "audio_codecs", ()) or ())
    max_audio_streams = getattr(limits, "max_audio_streams", 0) or 0

    ext = os.path.splitext(path)[1].lower()
    if limits.extensions and ext not in limits.extensions:
        allowed = "/".join(e.lstrip(".").upper() for e in limits.extensions)
        return VideoVerdict(
            BAD_FORMAT,
            f"Formato de vídeo não suportado por este canal. Use {allowed}.")

    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if limits.max_bytes and size > limits.max_bytes:
        return VideoVerdict(
            TOO_BIG,
            f"Vídeo acima do limite de {_fmt_size(limits.max_bytes)} deste canal. "
            "Reduza o tamanho e tente novamente.")

    if not (allowed_video_codecs or allowed_audio_codecs or max_audio_streams):
        # No codec policy declared: extension + size are the whole contract.
        return VideoVerdict(OK, codec_checked=False)

    streams = _ffprobe_streams(path)
    if streams is None:
        # ffprobe unavailable: extension + size passed, codec unverifiable.
        return VideoVerdict(OK, codec_checked=False)

    video_codecs = [s.get("codec_name", "") for s in streams
                    if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    codec_hint = (f"Use {'/'.join(e.lstrip('.').upper() for e in limits.extensions)} "
                  f"com vídeo {'/'.join(c.upper() for c in allowed_video_codecs)} "
                  f"e áudio {'/'.join(c.upper() for c in allowed_audio_codecs)}."
                  ) if allowed_video_codecs and allowed_audio_codecs else ""

    if allowed_video_codecs and video_codecs \
            and not any(c in allowed_video_codecs for c in video_codecs):
        return VideoVerdict(
            BAD_CODEC, f"Codec de vídeo não suportado. {codec_hint}".strip(),
            codec_checked=True)
    if max_audio_streams and len(audio_streams) > max_audio_streams:
        return VideoVerdict(
            BAD_CODEC,
            f"O vídeo tem mais de uma faixa de áudio; este canal aceita apenas "
            f"{max_audio_streams}.",
            codec_checked=True)
    if allowed_audio_codecs and audio_streams:
        audio_codecs = [s.get("codec_name", "") for s in audio_streams]
        if not any(c in allowed_audio_codecs for c in audio_codecs):
            return VideoVerdict(
                BAD_CODEC, f"Codec de áudio não suportado. {codec_hint}".strip(),
                codec_checked=True)

    return VideoVerdict(OK, codec_checked=True)
