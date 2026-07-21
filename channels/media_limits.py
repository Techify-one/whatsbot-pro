"""Generic media pre-flight validation against provider-declared limits.

Same policy/mechanism split as ``channels.video_validate`` (plano 65), extended to
every media kind the panel can attach (image / audio / document / sticker /
video): the NUMBERS (max bytes, accepted containers) are declared BY THE PROVIDER
in ``ChannelCapabilities.media_limits`` as :class:`channels.base.MediaLimits`
(``VideoLimits`` for video, which adds codec rules). This module only *evaluates*
a candidate upload and *describes* the limits so the panel can block a
non-conforming file BEFORE sending — instead of the send failing at the provider.

It never checks provider name and hardcodes no provider's numbers: a channel that
declares nothing for a kind (GOWA/Telegram) is never blocked for that kind. The
only fallback in the core is the pre-existing legacy Cloud VIDEO one, which stays
where it was (``video_validate.video_limits``) for plugins built before plano 65.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from channels import video_validate
from channels.base import MediaLimits, VideoLimits

# Kinds the panel can attach. "sticker" has no panel path yet but a provider may
# already declare it, so describing it costs nothing.
KINDS = ("image", "video", "audio", "document", "sticker")

# Structured rejection reasons — shared vocabulary with ``video_validate`` so the
# routes and the panel map them to the same HTTP status / popup copy.
OK = video_validate.OK
TOO_BIG = video_validate.TOO_BIG
BAD_FORMAT = video_validate.BAD_FORMAT

_KIND_LABEL = {
    "image": "Imagem",
    "video": "Vídeo",
    "audio": "Áudio",
    "document": "Documento",
    "sticker": "Figurinha",
}


@dataclass
class MediaVerdict:
    reason: str          # OK / TOO_BIG / BAD_FORMAT
    message: str = ""    # PT-BR, user-facing (empty when OK)

    @property
    def ok(self) -> bool:
        return self.reason == OK


def fmt_size(num_bytes: int) -> str:
    """Human size for the rejection message (caps come from the provider, so they
    are not necessarily round numbers of MB)."""
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    mb = num_bytes / (1024 * 1024)
    return f"{mb:.0f} MB" if abs(mb - round(mb)) < 0.05 else f"{mb:.1f} MB"


def limits_for(caps, kind: str):
    """The limits a channel declares for ``kind``, or ``None`` when it imposes none.

    Video keeps its legacy Cloud fallback (``video_validate.video_limits``) for
    provider plugins that predate ``media_limits``; every other kind is
    declaration-only — no fallback, no provider-name check.
    """
    if kind == "video":
        return video_validate.video_limits(caps)
    declared = (getattr(caps, "media_limits", None) or {}).get(kind)
    return declared if isinstance(declared, (MediaLimits, VideoLimits)) else None


def validate_upload(filename: str, size: int, caps, kind: str) -> MediaVerdict:
    """Check a candidate upload's container + size against the channel's limits.

    Content-level checks (codecs) are video-only and stay in ``video_validate`` —
    they need the file on disk. This one is cheap enough to run on the raw upload
    (and is mirrored byte-for-byte by the panel's client-side pre-check).
    """
    limits = limits_for(caps, kind)
    if limits is None:
        return MediaVerdict(OK)

    label = _KIND_LABEL.get(kind, "Arquivo")
    ext = os.path.splitext(filename or "")[1].lower()
    extensions = tuple(getattr(limits, "extensions", ()) or ())
    if extensions and ext not in extensions:
        allowed = "/".join(e.lstrip(".").upper() for e in extensions)
        msg = (f"Formato de {label.lower()} não suportado por este canal. "
               f"Use {allowed}.")
        # Vídeo anexado como documento: o canal aceita o arquivo, só não por esse
        # caminho — aponta a saída em vez de só recusar.
        if kind != "video" and ext in tuple(
                getattr(limits_for(caps, "video"), "extensions", ()) or ()):
            msg += " Para enviar um vídeo, use o anexo Vídeo."
        return MediaVerdict(BAD_FORMAT, msg)

    max_bytes = getattr(limits, "max_bytes", 0) or 0
    if max_bytes and size > max_bytes:
        return MediaVerdict(
            TOO_BIG,
            f"{label} acima do limite de {fmt_size(max_bytes)} deste canal "
            f"(o arquivo tem {fmt_size(size)}). Reduza o tamanho e tente novamente.")

    return MediaVerdict(OK)


def describe(caps, *, video_transcode_available: bool = False) -> dict:
    """JSON-serializable view of a channel's media limits, for the panel.

    Shape: ``{kind: {max_bytes, extensions: [".mp4", …], transcode: bool}}`` —
    kinds with no declared limits are omitted, so an empty dict means "this
    channel blocks nothing" and the panel skips its pre-check entirely.

    ``transcode`` tells the panel whether an oversized/odd file may still be
    re-encoded server-side (video only, and only when ffmpeg is present); when it
    is True the panel must NOT block on that kind's limits — the server can fix it.
    """
    out: dict = {}
    for kind in KINDS:
        limits = limits_for(caps, kind)
        if limits is None:
            continue
        entry = {
            "max_bytes": int(getattr(limits, "max_bytes", 0) or 0),
            "extensions": list(getattr(limits, "extensions", ()) or ()),
        }
        if kind == "video":
            entry["transcode"] = bool(
                getattr(limits, "transcode", False) and video_transcode_available)
        out[kind] = entry
    return out
