"""Catálogo de sons de notificação + normalização (plano 63).

Fonte ÚNICA dos metadados servidos por ``GET /api/sounds/catalog`` e da
normalização/validação aplicada tanto ao padrão GLOBAL (``config.sound_settings``,
no PUT /api/config) quanto ao override POR-USUÁRIO (``PUT /api/me/sound-prefs``).

Os IDs de som (``ding``/``chime``/…) casam com as receitas sintetizadas em
``web/static/js/utils/soundEngine.js`` — mantê-los em sincronia (adicionar um som
= receita no JS + entrada em ``SOUNDS`` aqui). ``duration_applies`` distingue a
classe do evento: ``notification`` (one-shot, duração N/A) × ``alert`` (sustained,
duração = por quantos segundos o alerta insiste).
"""

from __future__ import annotations

import re

# ── Eventos (o que dispara som) ────────────────────────────────────────────────
# Todos os eventos são editáveis pelo atendente (ativação, som, volume e — nos de
# classe ``alert`` — duração). O ADMIN define o padrão da equipe em
# ``config.sound_settings``; o servidor ainda decide SE o alerta de transferência
# é emitido (gate), lendo esse mesmo padrão via :func:`event_gate`.
EVENTS: list[dict] = [
    {"key": "new_message",    "label": "Mensagem nova",                 "group": "Mensagens",      "cls": "notification", "duration_applies": False},
    {"key": "mention",        "label": "Menção interna",                "group": "Mensagens",      "cls": "notification", "duration_applies": False},
    {"key": "ia_to_human",    "label": "Transferência da IA → atendente", "group": "Transferências", "cls": "alert", "duration_applies": True},
    {"key": "assigned_to_me", "label": "Conversa atribuída a você",     "group": "Transferências", "cls": "alert", "duration_applies": True},
]

# ── Catálogo de sons (MVP 100% sintetizado) ────────────────────────────────────
SOUNDS: list[dict] = [
    {"id": "ding",  "label": "Ding (2 notas)",     "cls": "once"},
    {"id": "chime", "label": "Carrilhão",          "cls": "once"},
    {"id": "blip",  "label": "Blip curto",         "cls": "once"},
    {"id": "soft",  "label": "Suave (grave)",      "cls": "once"},
    {"id": "pulse", "label": "Pulso (alerta gentil)", "cls": "alert"},
    {"id": "siren", "label": "Sirene (2 tons)",    "cls": "alert"},
    {"id": "none",  "label": "Silêncio",           "cls": "any"},
]

VALID_EVENT_KEYS: frozenset[str] = frozenset(e["key"] for e in EVENTS)
VALID_SOUND_IDS: frozenset[str] = frozenset(s["id"] for s in SOUNDS)

# Sons IMPORTADOS pela equipe (tabela ``custom_sounds``): o id da preferência é
# ``custom:<row id>``. A validação aqui é só de FORMATO — a existência não é
# checada (a normalização é pura, sem DB, e um som excluído não pode invalidar a
# preferência inteira: o motor cai no som padrão do evento).
_CUSTOM_SOUND_RE = re.compile(r"^custom:[1-9][0-9]{0,17}$")


def is_valid_sound_id(sound_id) -> bool:
    """True para um som do catálogo estático OU um importado (``custom:<n>``)."""
    if not isinstance(sound_id, str):
        return False
    return sound_id in VALID_SOUND_IDS or bool(_CUSTOM_SOUND_RE.match(sound_id))


def custom_entry(sound_id: int, name: str, filename: str, extra: dict | None = None) -> dict:
    """Entrada de catálogo para um som importado (mesma forma de :data:`SOUNDS`).

    ``kind="file"`` diz ao motor para tocar a ``url`` em vez de sintetizar, e
    ``cls="any"`` deixa o som disponível para QUALQUER evento (one-shot e alerta —
    num alerta o arquivo é repetido até a duração escolhida)."""
    entry = {
        "id": f"custom:{sound_id}",
        "label": name,
        "cls": "any",
        "kind": "file",
        "url": f"/statics/sounds/{filename}",
    }
    if extra:
        entry["size_bytes"] = extra.get("size_bytes")
        entry["created_by"] = extra.get("created_by")
    return entry


_DURATION_EVENTS: frozenset[str] = frozenset(
    e["key"] for e in EVENTS if e.get("duration_applies")
)

DURATION_MIN = 1
DURATION_MAX = 30


def catalog() -> dict:
    """Metadados estáticos para a tela (labels, classes, quais sliders mostrar)."""
    return {"events": EVENTS, "sounds": SOUNDS}


def _clamp_volume(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return max(0.0, min(1.0, f))


def _clamp_duration(v) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(DURATION_MIN, min(DURATION_MAX, n))


def _normalize_event(key: str, raw: dict) -> dict:
    """Keep only known fields of one event, coerced/clamped. Drops unknowns."""
    out: dict = {}
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    if "sound" in raw and is_valid_sound_id(raw["sound"]):
        out["sound"] = raw["sound"]
    if "volume" in raw:
        vol = _clamp_volume(raw["volume"])
        if vol is not None:
            out["volume"] = vol
    if "duration" in raw and key in _DURATION_EVENTS:
        dur = _clamp_duration(raw["duration"])
        if dur is not None:
            out["duration"] = dur
    return out


def normalize(value, *, sparse: bool = False) -> dict:
    """Coerce a sound-settings-shaped dict to a safe, known shape (fail-open).

    Drops unknown event keys / sound ids / fields; clamps volume to 0..1 and
    duration to 1..30. ``sparse=True`` (per-user override): omit ``master_enabled``
    and empty event maps unless the caller provided them. Non-dict input → ``{}``
    (sparse) or the caller's seed is used upstream (global).
    """
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    if "master_enabled" in value:
        out["master_enabled"] = bool(value["master_enabled"])
    elif not sparse:
        out["master_enabled"] = True
    events_in = value.get("events")
    events_out: dict = {}
    if isinstance(events_in, dict):
        for key, raw in events_in.items():
            if key in VALID_EVENT_KEYS and isinstance(raw, dict):
                ev = _normalize_event(key, raw)
                if ev or not sparse:
                    events_out[key] = ev
    if events_out or not sparse:
        out["events"] = events_out
    return out


def event_gate(raw_settings, key: str, *, legacy_enabled=True,
               legacy_duration: int = 5) -> tuple[bool, int]:
    """Resolve ``(enabled, duration)`` de um evento de ALERTA no servidor.

    O padrão da equipe (``config.sound_settings``) é a fonte preferida; quando o
    evento não traz o campo, cai nas keys legadas (``transfer_alert_*`` /
    ``agent_transfer_alert_*``), que continuam sendo o piso das instalações que
    nunca editaram o padrão. Fail-open: qualquer coisa estranha ⇒ legado.

    ``enabled`` é a AUTORIDADE do broadcast (o cliente não pode ligar o que o
    admin desligou); ``duration`` vai no payload apenas como FALLBACK — o
    atendente pode sobrescrever a duração na própria preferência.
    """
    enabled = bool(legacy_enabled)
    duration = _clamp_duration(legacy_duration)
    if duration is None:
        duration = 5
    try:
        ev = (normalize(raw_settings, sparse=False).get("events") or {}).get(key) or {}
    except Exception:  # noqa: BLE001 — nunca deixa o gate quebrar o broadcast
        return enabled, duration
    if "enabled" in ev:
        enabled = bool(ev["enabled"])
    if "duration" in ev:
        duration = ev["duration"]
    return enabled, duration
