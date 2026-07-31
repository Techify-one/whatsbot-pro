"""Leitura das settings do plugin (prefixo ``plugin.trackify.``).

Os DEFAULTS repetem os de ``settings.Settings`` porque o form só materializa
valores no ``config`` quando o usuário salva — antes disso, ler com o default
correto é responsabilidade do call site. Centralizado aqui para não divergir.
"""

from __future__ import annotations

from db.repositories import config_repo

PREFIX = "plugin.trackify."

# Espelho dos defaults de settings.Settings (fonte da verdade lá).
DEFAULTS: dict = {
    "nexus_dsn": "",
    "nexus_base_url": "",
    "cache_ttl_seconds": 60,
    "timeline_page_size": 25,
    "statement_timeout_ms": 5000,
    "mirror_enabled": False,
    "mirror_dry_run": True,
    "ingestion_url": "",
    "rate_per_min": 40,
    "max_age_days": 7,
    "mirror_contact_types": "whatsapp",
    # Não declarado em settings.py de propósito (segredo, ver docstring de lá).
    "api_key": "",
    # Gerado uma vez no primeiro uso do espelho; entra no external_id para que
    # staging e produção nunca colidam no mesmo canal do Trackify.
    "install_id": "",
}


def setting(key: str, default=None):
    """Lê uma setting, caindo no default declarado e depois em ``default``.

    NUNCA levanta: ``trackify_db.ping``/``schema_check`` prometem não levantar, e
    ler config toca o banco do WhatsBot (que pode não estar inicializado — boot,
    harness de teste, script standalone). Falha vira o default, não exceção.
    """
    fallback = DEFAULTS.get(key, default)
    try:
        return config_repo.get(PREFIX + key, fallback)
    except Exception:  # noqa: BLE001
        return fallback


def nexus_dsn() -> str:
    return (setting("nexus_dsn") or "").strip()


def nexus_base_url() -> str:
    return (setting("nexus_base_url") or "").strip().rstrip("/")


def timeline_page_size() -> int:
    try:
        v = int(setting("timeline_page_size", 25))
    except (TypeError, ValueError):
        return 25
    return min(max(v, 5), 100)


def contact_link(contact_id: str) -> str:
    """Deep-link do contato no Trackify, ou ``""`` sem base configurada."""
    base = nexus_base_url()
    return f"{base}/contacts/{contact_id}" if (base and contact_id) else ""
