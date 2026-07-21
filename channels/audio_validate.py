"""Generic audio validation against provider-declared limits.

The audio sibling of ``channels.video_validate``: the numbers (cap, containers,
codecs) are NOT the core's — each provider declares them in
``ChannelCapabilities.media_limits["audio"]`` as an :class:`channels.base.AudioLimits`.
This module only *evaluates* a file against whatever the channel declared, never
checks provider name, and returns an immediate permissive verdict for a channel
that declares nothing (GOWA/Telegram deliver whatever the linked device accepts).

Why this exists on top of ``channels.media_limits`` (extension + size): the
WhatsApp Cloud API accepts ``audio/ogg`` **only with the OPUS codec**, so a
Vorbis ``.ogg`` passes every extension check and is still refused by Meta. Codec
inspection needs ffprobe; when ffprobe is absent validation degrades to
extension + size, exactly like the video path degrades.

A provider still declaring a plain ``MediaLimits`` for audio (an older installed
copy of the plugin) is untouched: ``audio_limits`` returns ``None`` and the caller
keeps the pre-existing extension+size block.
"""

import logging
import os
from dataclasses import dataclass

from channels import video_validate
from channels.base import AudioLimits

logger = logging.getLogger(__name__)

# Shared rejection vocabulary with the video path — the route maps these to the
# same HTTP status and the panel to the same popup copy.
OK = video_validate.OK
TOO_BIG = video_validate.TOO_BIG
BAD_FORMAT = video_validate.BAD_FORMAT
BAD_CODEC = video_validate.BAD_CODEC


@dataclass
class AudioVerdict:
    reason: str                  # OK / TOO_BIG / BAD_FORMAT / BAD_CODEC
    message: str = ""            # PT-BR, user-facing (empty when OK)
    codec_checked: bool = False  # True only when ffprobe actually inspected it
    codec: str = ""              # what ffprobe found (empty when unknown)

    @property
    def ok(self) -> bool:
        return self.reason == OK


def audio_limits(caps) -> AudioLimits | None:
    """The codec-aware audio policy a channel declares, or ``None``.

    ``None`` means "this channel did not opt into codec inspection/transcoding"
    — either it declares no audio limits at all, or it declares a plain
    ``MediaLimits`` (pre-``AudioLimits`` plugin copy). Never keys off provider name.
    """
    declared = (getattr(caps, "media_limits", None) or {}).get("audio")
    return declared if isinstance(declared, AudioLimits) else None


def _audio_codecs(path: str) -> list[str] | None:
    """Codec names of the file's audio streams, or ``None`` when ffprobe can't tell."""
    streams = video_validate._ffprobe_streams(path)  # noqa: SLF001 - same package
    if streams is None:
        return None
    return [s.get("codec_name", "") for s in streams if s.get("codec_type") == "audio"]


def validate_audio(path: str, caps) -> AudioVerdict:
    """Validate ``path`` against the audio limits the channel declares."""
    limits = audio_limits(caps)
    if limits is None:
        return AudioVerdict(OK)

    ext = os.path.splitext(path)[1].lower()
    if limits.extensions and ext not in limits.extensions:
        allowed = "/".join(e.lstrip(".").upper() for e in limits.extensions)
        return AudioVerdict(
            BAD_FORMAT,
            f"Formato de áudio não suportado por este canal. Use {allowed}.")

    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if limits.max_bytes and size > limits.max_bytes:
        return AudioVerdict(
            TOO_BIG,
            f"Áudio acima do limite de {video_validate._fmt_size(limits.max_bytes)} "  # noqa: SLF001
            "deste canal. Reduza o tamanho e tente novamente.")

    accepted = limits.codecs_for(ext)
    if not accepted:
        return AudioVerdict(OK)

    codecs = _audio_codecs(path)
    if codecs is None:
        # ffprobe unavailable: extension + size passed, codec unverifiable.
        return AudioVerdict(OK, codec_checked=False)
    if not codecs:
        return AudioVerdict(
            BAD_FORMAT, "O arquivo não contém uma faixa de áudio.",
            codec_checked=True)
    if any(c in accepted for c in codecs):
        return AudioVerdict(OK, codec_checked=True, codec=codecs[0])

    allowed = "/".join(c.upper() for c in accepted)
    return AudioVerdict(
        BAD_CODEC,
        f"Codec de áudio não suportado por este canal em {ext.lstrip('.').upper()} "
        f"(o arquivo é {codecs[0].upper()}). Este canal aceita {allowed}.",
        codec_checked=True, codec=codecs[0])
