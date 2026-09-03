"""Per-channel AI settings resolver (plano 21).

Each channel may override a subset of the global AI settings under
``channels.config["ai"]``. This module reads that override map (cached briefly,
like the allowed-JID-types cache) and resolves an effective value, falling back
to a caller-supplied global default. Keeps the per-channel knobs (ligar IA,
resposta em grupos, transcrição, contexto, agrupamento, mensagens picadas) DRY
and in one place. O alerta sonoro de transferência NÃO é per-canal (é sobre quem
RECEBE, não sobre o canal): mora no padrão da equipe em ``config.sound_settings``.

The global ``auto_reply`` master switch (checked FIRST) and the LLM key / low
balance settings stay global — they are NOT in :data:`PER_CHANNEL_AI_KEYS`.
"""

from __future__ import annotations

import json
import logging
import time

from db.repositories import channel_repo

logger = logging.getLogger(__name__)

# Keys a channel may override. ``ai_enabled`` is channel-only (no global config
# equivalent) — the per-channel master switch; the rest mirror global config keys
# and fall back to the global value when the channel hasn't set them.
PER_CHANNEL_AI_KEYS = (
    "ai_enabled",
    "default_ai_enabled",
    "group_reply_mode",
    "image_transcription_enabled",   # legado (bool) — fallback do mode abaixo
    # plano 118 — direções da DESCRIÇÃO DE IMAGEM (received/sent/private), igual ao
    # áudio. Sem esta linha o override do canal seria silenciosamente ignorado.
    "image_transcription_mode",
    "document_transcription_enabled",
    "audio_transcription_mode",
    "audio_transcription_target",
    "audio_transcription_chat_prefix",
    "max_context_messages",
    "message_batch_delay",
    "split_messages",
    "split_message_delay",
    "ai_sequential_enabled",
    "ai_sequential_delay",
    # plano 71: "atendente padrão para novas conversas" — um user_id humano que
    # carimba o assignee no nascimento da conversa (que então nasce com a IA off).
    # Channel-only (sem equivalente global). ``value()`` já lê qualquer chave do
    # sub-objeto ``ai``; esta entrada documenta a intenção (e habilita o
    # ``ChannelSettingsView`` a expô-la, embora só o nascimento a leia via ``value``).
    "default_assignee_user_id",
    # plano 152: o MESMO campo aceita um AGENTE DE IA em vez de um humano — a
    # conversa nasce vinculada a ele (``active_agent_key``) e com a IA LIGADA.
    # As duas chaves são MUTUAMENTE EXCLUSIVAS (a UI zera a outra ao escolher) e,
    # se ambas vierem preenchidas por uma edição à mão, o HUMANO vence (é o
    # comportamento legado — nunca ligar a IA por acidente).
    "default_assignee_agent_key",
)

_CACHE: dict[str, tuple[dict, float]] = {}
_TTL = 30.0


def _read_overrides(channel_id: str) -> dict:
    """Read ``channels.config['ai']`` for one channel. Synchronous DB read."""
    try:
        row = channel_repo.get(channel_id)
        cfg = row.get("config") if row else None
        if isinstance(cfg, str) and cfg:
            cfg = json.loads(cfg)
        if isinstance(cfg, dict):
            ai = cfg.get("ai")
            if isinstance(ai, dict):
                return ai
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai_settings] read failed for %s: %s", channel_id, e)
    return {}


def overrides(channel_id: str) -> dict:
    """The channel's AI override map (``config['ai']``), cached briefly."""
    now = time.time()
    hit = _CACHE.get(channel_id)
    if hit and (now - hit[1]) < _TTL:
        return hit[0]
    data = _read_overrides(channel_id)
    _CACHE[channel_id] = (data, now)
    return data


def value(channel_id: str, key: str, default):
    """Effective value for one key: the channel override, else ``default``."""
    ov = overrides(channel_id)
    if key in ov and ov[key] is not None:
        return ov[key]
    return default


def reset_cache(channel_id: str | None = None) -> None:
    """Invalidate the cache (all channels, or just one). Call after a config edit."""
    if channel_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(channel_id, None)


class ChannelSettingsView:
    """``settings.get(key)`` overlaying a channel's AI overrides on the globals.

    Drop-in for the global ``settings`` object where a function reads only the
    per-channel keys (e.g. the transcription helper): override keys win, anything
    else falls through to the real global ``settings``.
    """

    __slots__ = ("_ov", "_settings")

    def __init__(self, channel_id: str, settings):
        self._ov = overrides(channel_id)
        self._settings = settings

    def get(self, key, default=None):
        ov = self._ov
        if key in ov and ov[key] is not None and key in PER_CHANNEL_AI_KEYS:
            return ov[key]
        return self._settings.get(key, default)

    def overridden_keys(self) -> frozenset:
        """As chaves que ESTE canal de fato sobrepõe (não as globais herdadas).

        Usado por ``server.transcription.modes_for`` (plano 118) para resolver a
        escada "mode → bool legado" DENTRO do mesmo escopo: um canal que só tem o
        booleano antigo não pode ser vencido por um ``*_transcription_mode`` que
        existe apenas no config global.
        """
        ov = self._ov
        return frozenset(
            k for k, v in ov.items()
            if v is not None and k in PER_CHANNEL_AI_KEYS
        )


def view(channel_id: str, settings) -> ChannelSettingsView:
    """A settings-like view that overlays ``channel_id``'s AI overrides."""
    return ChannelSettingsView(channel_id, settings)
