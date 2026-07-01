"""Shared media transcription helper.

Single place that decides whether to transcribe a piece of media and runs it,
honoring the core enabled-by-config gate plus the two plugin hooks
(`filter.transcription.should_run` / `filter.transcription.result`). Used both by
the inbound webhook pipeline and by the operator-initiated send routes, so the
behavior (and the plugin contract) stays identical everywhere.
"""

from __future__ import annotations

import asyncio
import logging

from plugins.events import apply_filter

logger = logging.getLogger(__name__)

# Fixed PT-BR labels prepended to a transcribed/described media payload. The
# label and the join order (prefix-before-text for images, text-before-prefix for
# audio/documents) are part of the observed behaviour — do NOT change them.
_MEDIA_PREFIX = {
    "audio": "[Transcrição do áudio]: ",
    "image": "[Descrição da imagem]: ",
    "document": "[Conteúdo do documento]: ",
}


# Audio transcription "mode" is a multi-select set drawn from these tokens: which
# message directions get transcribed. Inbound → "received"; outbound (echo from the
# phone / operator send) → "sent"; operator-recorded private note → "private".
_AUDIO_MODE_TOKENS = ("received", "sent", "private")


def parse_audio_modes(raw) -> set[str]:
    """Parse ``audio_transcription_mode`` into a set of {received, sent, private}.

    Backward-compatible with the legacy single-value strings so old channel
    overrides and the global config keep working unchanged:

    - ``None`` (key unset) → ``{"received"}`` (legacy default);
    - ``""``/``"off"``/``"none"`` → ``set()`` (transcription off);
    - ``"received"`` / ``"sent"`` → that single token;
    - ``"both"`` → ``{"received", "sent"}``;
    - a comma-joined list (``"received,sent,private"``) → the parsed set.

    A list/tuple/set is accepted too. Unknown tokens are dropped.
    """
    if raw is None:
        return {"received"}
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(t).strip().lower() for t in raw]
    else:
        s = str(raw).strip().lower()
        if s == "both":
            return {"received", "sent"}
        if s in ("", "off", "none"):
            return set()
        tokens = [t.strip() for t in s.split(",")]
    return {t for t in tokens if t in _AUDIO_MODE_TOKENS}


def format_media_content(media_kind: str, transcription: str, text: str = "") -> str:
    """Combine an existing text body with a media transcription/description.

    Single source of the ``[Transcrição]/[Descrição]/[Conteúdo]`` prefixing used
    when persisting transcribed inbound/outbound media and when building the LLM
    input. Join order matches the legacy call sites exactly:

    - ``image``  → ``"<prefix><transcription>\\n<text>"`` (prefix first);
    - ``audio``/``document`` → ``"<text>\\n<prefix><transcription>"`` (text first).

    With an empty ``text`` (all media-only sites) the result is just the prefixed
    transcription, identical to the previous inline code.
    """
    prefix = f"{_MEDIA_PREFIX[media_kind]}{transcription}"
    if not text:
        return prefix
    if media_kind == "image":
        return f"{prefix}\n{text}"
    return f"{text}\n{prefix}"


async def maybe_transcribe(
    media_kind: str,            # "audio" | "image" | "document"
    path: str,
    *,
    settings,
    agent_handler,
    phone: str,
    source: str,                # "batch" | "echo" | "operator" | "private" | "group_no_mention"
    is_group: bool = False,
    group_jid: str | None = None,
    file_name: str = "",        # document only — original filename
    mimetype: str = "",         # document only — best-effort mime hint
    force: bool = False,        # bypass the config gate (still honors plugin should_run)
) -> str:
    """Run audio transcription / image description / document reading.

    Returns the final transcription string — empty when the action was skipped
    (config gate or plugin brake), failed, or yielded nothing. Plugins can only
    *narrow* the policy, never widen it.

    Audio is gated by ``audio_transcription_mode``, a multi-select set of
    {received, sent, private}: inbound sources count as "received"; outbound
    sources (``echo`` = sent from the phone, ``operator`` = sent from the panel)
    count as "sent"; ``private`` = an operator-recorded private audio note.
    ``force=True`` bypasses this gate (e.g. the AI must read a private audio
    regardless of the channel's transcription setting) but still lets a plugin
    ``filter.transcription.should_run`` veto.
    """
    if force:
        allow = True
    elif media_kind == "audio":
        modes = parse_audio_modes(settings.get("audio_transcription_mode", "received"))
        if source in ("echo", "operator"):
            allow = "sent" in modes
        elif source == "private":
            allow = "private" in modes
        else:
            allow = "received" in modes
    elif media_kind == "document":
        allow = bool(settings.get("document_transcription_enabled", True))
    else:  # image
        allow = bool(settings.get("image_transcription_enabled", True))
    if not allow:
        return ""

    extras = {
        "phone": phone,
        "media_kind": media_kind,
        "media_path": path,
        "is_group": is_group,
        "group_jid": group_jid,
        "source": source,
    }
    should = await apply_filter("filter.transcription.should_run", True, extras)
    if not should:
        return ""

    try:
        if media_kind == "audio":
            raw = await asyncio.to_thread(agent_handler.transcribe_audio, path, phone)
        elif media_kind == "document":
            raw = await asyncio.to_thread(
                agent_handler.transcribe_document, path, phone, file_name, mimetype
            )
        else:
            raw = await asyncio.to_thread(agent_handler.describe_image, path, phone)
    except Exception as e:
        logger.error("[Transcription] %s failed for %s: %s", media_kind, phone, e)
        return ""

    extras["model"] = (
        getattr(agent_handler, "audio_model", None) if media_kind == "audio"
        else getattr(agent_handler, "document_model", None) if media_kind == "document"
        else getattr(agent_handler, "image_model", None)
    )
    final = await apply_filter("filter.transcription.result", raw or "", extras)
    return final or ""
