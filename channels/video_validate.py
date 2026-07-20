"""Video validation against the WhatsApp Cloud API limits (plano 65).

Capability-driven, never by provider name: the strict Cloud limits (mp4/3gp,
H.264/AAC, ≤1 audio stream, ≤16 MB) only apply to *windowed* channels
(``session_window_hours > 0`` — the WhatsApp Cloud API). Always-open channels
(GOWA/Telegram, ``session_window_hours == 0``) render/deliver arbitrary mp4 as
a file, so they get a permissive verdict here.

Codec/audio-stream checks require ``ffprobe`` on PATH. When it is absent (the
production Docker image today — see Dockerfile), validation degrades to
extension + size only, exactly like ``_gif_to_mp4`` degrades without ffmpeg.
A codec the panel could not inspect then falls to the Meta ``131053`` error,
which the caller surfaces as a friendly message (plano 65 F5A).
"""

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Cloud API hard limits.
MAX_VIDEO_BYTES = 16 * 1024 * 1024          # 16 MB (upload and link)
ALLOWED_EXTENSIONS = (".mp4", ".3gp", ".3gpp")
ALLOWED_VIDEO_CODECS = ("h264",)            # Meta: H.264 video
ALLOWED_AUDIO_CODECS = ("aac",)             # Meta: AAC audio

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


def is_windowed(caps) -> bool:
    """True when the channel enforces the Cloud 24h window — the discriminator
    for applying the strict Cloud video limits. Never checks provider name."""
    return bool(getattr(caps, "session_window_hours", 0))


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
    """Validate ``path`` for the given channel capabilities.

    Always-open channels get an immediate ``OK`` (they deliver any mp4 as a
    file). Windowed (Cloud) channels are checked for extension, size, and — when
    ffprobe is available — video/audio codec and audio-stream count.
    """
    # Always-open providers (GOWA/Telegram) have no Cloud limits.
    if not is_windowed(caps):
        return VideoVerdict(OK)

    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return VideoVerdict(
            BAD_FORMAT,
            "Formato de vídeo não suportado pelo WhatsApp. Use MP4 (H.264/AAC).")

    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > MAX_VIDEO_BYTES:
        return VideoVerdict(
            TOO_BIG,
            "Vídeo acima do limite de 16 MB do WhatsApp. Reduza o tamanho e tente novamente.")

    streams = _ffprobe_streams(path)
    if streams is None:
        # ffprobe unavailable: extension + size passed, codec unverifiable.
        return VideoVerdict(OK, codec_checked=False)

    video_codecs = [s.get("codec_name", "") for s in streams
                    if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if video_codecs and not any(c in ALLOWED_VIDEO_CODECS for c in video_codecs):
        return VideoVerdict(
            BAD_CODEC,
            "Codec de vídeo não suportado. Use MP4 com vídeo H.264 e áudio AAC.",
            codec_checked=True)
    if len(audio_streams) > 1:
        return VideoVerdict(
            BAD_CODEC,
            "O vídeo tem mais de uma faixa de áudio; o WhatsApp aceita apenas uma.",
            codec_checked=True)
    if audio_streams:
        audio_codecs = [s.get("codec_name", "") for s in audio_streams]
        if not any(c in ALLOWED_AUDIO_CODECS for c in audio_codecs):
            return VideoVerdict(
                BAD_CODEC,
                "Codec de áudio não suportado. Use MP4 com vídeo H.264 e áudio AAC.",
                codec_checked=True)

    return VideoVerdict(OK, codec_checked=True)
