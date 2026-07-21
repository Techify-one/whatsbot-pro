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

# ── Eventos (o que dispara som) ────────────────────────────────────────────────
# ``server_gated``: os campos ``enabled``/``duration`` são definidos pelo ADMIN nas
# keys legadas (``transfer_alert_*`` / ``agent_transfer_alert_*``), não por usuário
# — o usuário/dispositivo só customiza ``sound``/``volume`` dentro do habilitado.
EVENTS: list[dict] = [
    {"key": "new_message",    "label": "Mensagem nova",                 "group": "Mensagens",      "cls": "notification", "duration_applies": False},
    {"key": "mention",        "label": "Menção interna",                "group": "Mensagens",      "cls": "notification", "duration_applies": False},
    {"key": "ia_to_human",    "label": "Transferência da IA → atendente", "group": "Transferências", "cls": "alert", "duration_applies": True, "server_gated": True},
    {"key": "assigned_to_me", "label": "Conversa atribuída a você",     "group": "Transferências", "cls": "alert", "duration_applies": True, "server_gated": True},
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
    if "sound" in raw and raw["sound"] in VALID_SOUND_IDS:
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
