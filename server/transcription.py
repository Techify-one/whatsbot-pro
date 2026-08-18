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


# A transcription "mode" is a multi-select set drawn from these tokens: which
# message directions get transcribed. Inbound → "received"; outbound (echo from the
# phone / operator send) → "sent"; operator-recorded private note → "private".
# Shared by audio (``audio_transcription_mode``) and, since plano 118, by image
# (``image_transcription_mode``).
_MODE_TOKENS = ("received", "sent", "private")
_AUDIO_MODE_TOKENS = _MODE_TOKENS  # legacy alias (kept: imported by the suite)


def parse_media_modes(raw) -> set[str]:
    """Parse a ``<kind>_transcription_mode`` into a set of {received, sent, private}.

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
    return {t for t in tokens if t in _MODE_TOKENS}


# Backwards-compatible alias: the name predates image having directions and is
# imported by ``app/services/messaging_service.py`` and by the test suite.
parse_audio_modes = parse_media_modes


def direction_of(source: str) -> str:
    """Map a call-site ``source`` to the direction token it is gated by.

    ``echo`` (sent from the user's own phone) and ``operator`` (sent from the
    panel) are both outbound → ``"sent"``; ``private`` is the operator's
    panel-only note; anything else (``batch``, ``group_no_mention``) is inbound.
    """
    if source in ("echo", "operator"):
        return "sent"
    if source == "private":
        return "private"
    return "received"


def modes_for(settings, media_kind: str) -> set[str]:
    """Which directions of ``media_kind`` are transcribed, per the config ladder.

    Resolution (plano 118 §4.1), in one place so every call site agrees:

    1. ``<kind>_transcription_mode`` present → parse it;
    2. else the legacy boolean ``<kind>_transcription_enabled`` → ``True`` means
       ``{"received"}`` (what the bool always meant: inbound only), ``False``
       means "off";
    3. else ``{"received"}``.

    ⚠️ Step 0 exists because ``settings`` may be a
    :class:`channels.ai_settings.ChannelSettingsView`, which overlays ONE
    channel's overrides on the globals: a channel that only carries the legacy
    boolean (never re-saved since plano 118) must not have it beaten by a mode
    key that exists only GLOBALLY — that would silently flip the operator's
    unchecked box back on. When the view tells us which keys it actually
    overrides, the channel's own legacy bool wins over a global mode.
    """
    mode_key = f"{media_kind}_transcription_mode"
    legacy_key = f"{media_kind}_transcription_enabled"
    overridden = getattr(settings, "overridden_keys", None)
    if callable(overridden):
        try:
            ov = overridden()
        except Exception:  # never let a settings double break transcription
            ov = ()
        if legacy_key in ov and mode_key not in ov:
            return {"received"} if settings.get(legacy_key) else set()
    raw = settings.get(mode_key, None)
    if raw is not None:
        return parse_media_modes(raw)
    legacy = settings.get(legacy_key, None)
    if legacy is not None:
        return {"received"} if legacy else set()
    return {"received"}


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


# Media kinds the panel knows how to render on its own (see
# web/static/js/components/contacts/MediaContent.js): each of these draws a
# visible body (player, <img>, link, map link) even with an empty text, so an
# empty caption is legitimate and must NOT be replaced by a placeholder.
RENDERABLE_MEDIA_TYPES = frozenset({
    "image",
    "audio",
    "video",
    "sticker",
    "document",
    "location",
    "live_location",
})


def placeholder_for_unrenderable(
    text: str | None,
    media_type: str | None,
    media_path: str | None,
) -> str:
    """Return the text to persist/show, never letting a message render mute.

    Safety net (plano 75 F3), provider-agnostic: when a channel hands us a
    message whose text is empty, that carries no file (`media_path`) and whose
    ``media_type`` is not one the panel can draw, the bubble would come out
    completely blank. In that case we substitute a readable PT-BR placeholder.

    Anything else is returned untouched — in particular a real media message
    without a caption (image/audio/video/sticker/document/location) keeps its
    empty text, because the bubble already shows the media itself.
    """
    body = text or ""
    if body or media_path:
        return body
    if not media_type or media_type in RENDERABLE_MEDIA_TYPES:
        return body
    return f'[Mensagem do tipo "{media_type}" não suportada]'


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
    elif media_kind == "document":
        # Documento segue no booleano nesta entrega (plano 118 · P1); ``modes_for``
        # já o atende genericamente quando ganhar direções.
        allow = bool(settings.get("document_transcription_enabled", True))
    else:  # audio | image — gated by direction
        allow = direction_of(source) in modes_for(settings, media_kind)
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
