import os
from pathlib import Path
from typing import Any, Callable

from db.repositories import config_repo


def get_data_dir() -> Path:
    """Return the application data directory (project root)."""
    data_dir = Path(__file__).resolve().parent.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# LLM API base URL — OpenRouter-compatible proxy. Override via the
# LLM_API_BASE_URL env var to point back at OpenRouter or another proxy.
LLM_API_BASE_URL = os.environ.get(
    "LLM_API_BASE_URL", "https://llm.techify.one/api/v1"
).rstrip("/")

# Techify account provisioning — used by the first-run setup wizard. The
# WhatsBot fetches the current provisioning number from TECHIFY_SERVICE_NUMBER_URL
# and sends a WhatsApp message to it, Techify creates an account + API key, and
# the wizard polls TECHIFY_REQUEST_APIKEY_URL (keyed by the connected WhatsApp
# number) until the key is ready. Once the account is created the key stays
# downloadable for ~1 minute. TECHIFY_PROVISION_NUMBER is a fallback used only
# when the service-number endpoint is unreachable.
TECHIFY_SERVICE_NUMBER_URL = os.environ.get(
    "TECHIFY_SERVICE_NUMBER_URL", "https://llm.techify.one/service_number"
).rstrip("/")
TECHIFY_PROVISION_NUMBER = os.environ.get("TECHIFY_PROVISION_NUMBER", "5513981744038")
TECHIFY_REQUEST_APIKEY_URL = os.environ.get(
    "TECHIFY_REQUEST_APIKEY_URL", "https://llm.techify.one/request-apikey"
).rstrip("/")
TECHIFY_PROVISION_MESSAGE = "Quero Criar conta e receber minha Chave de API"


_ENV_OVERRIDES: dict[str, tuple[str, Callable[[str], Any]]] = {
    "OPENROUTER_API_KEY": ("openrouter_api_key", str),
    "WHATSBOT_AUDIO_MODEL": ("audio_model", str),
    "WHATSBOT_IMAGE_MODEL": ("image_model", str),
    "WHATSBOT_DOCUMENT_MODEL": ("document_model", str),
    "WHATSBOT_IMPROVEMENT_MODEL": ("improvement_model", str),
    "WHATSBOT_WEB_PORT": ("web_port", int),
    "WHATSBOT_GOWA_PORT": ("gowa_port", int),
    "WHATSBOT_AUTO_REPLY": ("auto_reply", lambda v: v.lower() in ("1", "true", "yes")),
    "WHATSBOT_MAX_CONTEXT": ("max_context_messages", int),
    "WHATSBOT_BATCH_DELAY": ("message_batch_delay", float),
    "WHATSBOT_AI_TOOLS_CODE": ("ai_tools_code_enabled", lambda v: v.lower() in ("1", "true", "yes")),
}

# Reverse lookup: config_key -> (env_key, cast). Used by get() to apply env overrides on-demand.
_ENV_OVERRIDES_BY_KEY: dict[str, tuple[str, Callable[[str], Any]]] = {
    cfg_key: (env_key, cast) for env_key, (cfg_key, cast) in _ENV_OVERRIDES.items()
}

# ── Config-key metadata (R17 — single source of truth) ─────────────────────────
#
# Previously the same keys were mirrored across THREE lists that drifted: the seed
# ``DEFAULT_CONFIG`` here, the GET ``/api/config`` projection, and the PUT allowlist
# (both in ``server/routes/config.py``). Consolidated to ONE per-key metadata
# table; the three lists are now DERIVED from it (exact same keys/defaults/exposure
# — this is a DRY refactor, not a behavior change).
#
# Each entry is ``ConfigKey``:
#   * ``default``   — the seed value written to the DB by ``Settings.load`` (the
#                     value used by ``DEFAULT_CONFIG``). ``_NO_SEED`` ⇒ the key is
#                     NOT seeded into the DB (e.g. ``max_executions``, ``access_token``
#                     stays write-only/derived).
#   * ``exposed``   — returned by ``GET /api/config``.
#   * ``get_default`` — the fallback the GET handler passes (``settings.get(key,
#                     get_default)``). Defaults to ``default`` when not given;
#                     given explicitly ONLY where the historical GET fallback
#                     diverged from the seed default (``auto_reply``,
#                     ``ai_tools_code_enabled``) or the key has no seed
#                     (``max_executions``). These divergent fallbacks are inert in
#                     practice (the key is always present in the DB once seeded /
#                     once written) but are preserved byte-for-byte.
#   * ``writable``  — accepted by ``PUT /api/config`` (the allowlist).
#
# Keys handled specially by the GET handler (masked / derived) are NOT exposed via
# this metadata: ``openrouter_api_key`` (masked), ``has_password`` (derived from
# ``web_password_hash``). They stay inline in the route.

from dataclasses import dataclass


_NO_SEED = object()


@dataclass(frozen=True)
class ConfigKey:
    key: str
    default: Any = _NO_SEED
    exposed: bool = False
    get_default: Any = _NO_SEED
    writable: bool = False

    @property
    def effective_get_default(self):
        return self.default if self.get_default is _NO_SEED else self.get_default


# Order is preserved for the GET projection (matches the historical dict order so
# the response shape is byte-for-byte identical).
CONFIG_KEYS: tuple[ConfigKey, ...] = (
    ConfigKey("openrouter_api_key", default="", writable=True),  # GET: masked, handled in route
    ConfigKey("audio_model", default="google/gemini-2.5-flash", exposed=True, writable=True),
    ConfigKey("image_model", default="google/gemini-2.5-flash", exposed=True, writable=True),
    ConfigKey("document_model", default="google/gemini-2.5-flash", exposed=True, writable=True),
    # Model for the one-shot "improvement analysis" of a flagged AI reply.
    # Empty → falls back to the chat ``model``. Override env WHATSBOT_IMPROVEMENT_MODEL.
    ConfigKey("improvement_model", default="", exposed=True, writable=True),
    ConfigKey("group_reply_mode", default="mention_only", exposed=True, writable=True),
    # Historical GET fallback was True (inert — the key is always seeded False).
    ConfigKey("auto_reply", default=False, exposed=True, get_default=True, writable=True),
    ConfigKey("max_context_messages", default=10, exposed=True, writable=True),
    ConfigKey("message_batch_delay", default=3.0, exposed=True, writable=True),
    ConfigKey("split_messages", default=True, exposed=True, writable=True),
    ConfigKey("split_message_delay", default=2.0, exposed=True, writable=True),
    ConfigKey("audio_transcription_mode", default="received", exposed=True, writable=True),
    ConfigKey("audio_transcription_target", default="private", exposed=True, writable=True),
    ConfigKey("audio_transcription_chat_prefix", default="", exposed=True, writable=True),
    ConfigKey("image_transcription_enabled", default=True, exposed=True, writable=True),
    ConfigKey("document_transcription_enabled", default=True, exposed=True, writable=True),
    ConfigKey("transfer_alert_enabled", default=True, exposed=True, writable=True),
    ConfigKey("transfer_alert_duration", default=5, exposed=True, writable=True),
    # max_executions is NOT seeded (no DEFAULT_CONFIG entry); GET falls back to 200.
    ConfigKey("max_executions", exposed=True, get_default=200, writable=True),
    # audit_retention_days: dias de retenção da trilha de auditoria (lido por
    # server.background.audit_purge_loop). NÃO seedado; GET cai em 365. Antes
    # faltava aqui, então o campo do ConfigPanel nunca persistia (era inerte).
    ConfigKey("audit_retention_days", exposed=True, get_default=365, writable=True),
    ConfigKey("default_ai_enabled", default=True, exposed=True, writable=True),
    # ⚠️ KILL-SWITCH — code-in-DB. ``ai_tools`` rows hold Python code the installer
    # runs in an isolated subprocess at boot (RLIMIT_CPU/RLIMIT_AS/timeout —
    # P62/P67). Seeded ON so user tools register automatically; set env
    # WHATSBOT_AI_TOOLS_CODE=0 to lock (untrusted host). Historical GET fallback
    # was False (inert — the key is always seeded True). See DECISOES.md P62.
    ConfigKey("ai_tools_code_enabled", default=True, exposed=True, get_default=False, writable=True),
    # Guardrails do motor de agentes (plano 29 A1): teto default de chamadas POR
    # TOOL por mensagem, aplicado a toda tool sem ``call_limit`` próprio no
    # ``hooks_config`` do agente. 0 = ilimitado (comportamento legado).
    ConfigKey("ai_tool_call_limit_per_tool", default=0, exposed=True, writable=True),
    # Plano 29 A3: total de runs de agente por mensagem no routing within-turn
    # (1º hop + handoffs). Substitui o MAX_ROUTING_DEPTH hardcoded.
    ConfigKey("ai_max_route_depth", default=5, exposed=True, writable=True),
    # Plano 29 A8: teto TOTAL de tool calls por run do Agent AGNO (freio de loop
    # desenfreado). 0 = sem teto; env WHATSBOT_TOOL_CALL_LIMIT tem precedência.
    ConfigKey("ai_tool_call_limit_total", default=25, exposed=True, writable=True),
    ConfigKey("setup_completed", default=False, exposed=True, writable=True),
    # ── Installation identity (seeded once on first boot; see
    # ``_seed_installation_metadata``). Exposed in GET /api/config but NEVER
    # writable — ``installation_id`` is a stable, immutable per-install identity.
    ConfigKey("installation_id", exposed=True, get_default=""),
    ConfigKey("installed_at", exposed=True, get_default=None),
    ConfigKey("app_version", exposed=True, get_default=""),
    # Techify account — account_url is the customer's recharge page (exposed);
    # access_token is the credential for that account (kept server-side only).
    ConfigKey("account_url", default="", exposed=True),
    ConfigKey("low_balance_enabled", default=True, exposed=True, writable=True),
    ConfigKey("low_balance_threshold", default=0.50, exposed=True, writable=True),
    # Avisos de sistema no chat (plano 12) — gate GLOBAL por grupo de evento.
    # Grupo desligado ⇒ o aviso não é gerado (nada grava/emite) para ninguém.
    ConfigKey("system_notice_assignment", default=True, exposed=True, writable=True),
    ConfigKey("system_notice_tags", default=True, exposed=True, writable=True),
    ConfigKey("system_notice_conv_labels", default=True, exposed=True, writable=True),
    ConfigKey("system_notice_status", default=True, exposed=True, writable=True),
    ConfigKey("system_notice_ai", default=True, exposed=True, writable=True),
    # Trilha de auditoria (plano 07) — master global. Desligado ⇒ o listener core
    # (``server/audit_listener.py``) faz early-return e nenhum evento é gravado em
    # ``audit_log``. Checado por evento (sem restart). Toggle na tela Auditoria,
    # gated por ``audit.manage``.
    ConfigKey("audit_enabled", default=True, exposed=True, writable=True),
    # ── Seed-only keys (not exposed in GET, not part of the PUT allowlist) ──────
    ConfigKey("inactivity_timeout_min", default=30),
    ConfigKey("response_delay_min", default=1.0),
    ConfigKey("response_delay_max", default=3.0),
    ConfigKey("gowa_port", default=64996),
    ConfigKey("web_port", default=8090),
    ConfigKey("usd_brl_rate", default=5.50),
    ConfigKey("bot_phone", default=""),  # writable via the PUT special-case below
    ConfigKey("bot_name", default=""),
    ConfigKey("web_password_hash", default=""),
    ConfigKey("web_password_salt", default=""),
    ConfigKey("access_token", default=""),
)

# ``bot_phone`` is writable through the PUT allowlist (it triggers mention-detection
# refresh in the route). Mark it without exposing it in GET.
_BOT_PHONE_WRITABLE = "bot_phone"


def _build_default_config() -> dict:
    """Seed-defaults map (only keys that ARE seeded) — preserves the historical
    ``DEFAULT_CONFIG`` exactly."""
    return {ck.key: ck.default for ck in CONFIG_KEYS if ck.default is not _NO_SEED}


def exposed_config_keys() -> list[ConfigKey]:
    """Keys returned by ``GET /api/config`` (in declaration order)."""
    return [ck for ck in CONFIG_KEYS if ck.exposed]


def writable_config_keys() -> set[str]:
    """The ``PUT /api/config`` allowlist (R17 single source)."""
    return {ck.key for ck in CONFIG_KEYS if ck.writable} | {_BOT_PHONE_WRITABLE}


# Prefix of the per-group "system notice" gate keys (plano 12 / plano 23 Fase C3).
# A notice GROUP auto-declares its config key as ``system_notice_<group>`` in
# ``server.system_notices.register_notice_group``; these CONFIG_KEYS entries are
# the backend single-source (DEFAULT_CONFIG/GET/PUT all derive from CONFIG_KEYS).
# ``system_notices`` cross-checks this set at import so the two can't silently
# drift — kept here (not derived FROM system_notices) to avoid inverting the
# settings→server import direction. Frontend ConfigPanel toggles still mirror
# these by name (deferred; see plano 23 Fase C3).
_SYSTEM_NOTICE_KEY_PREFIX = "system_notice_"


def system_notice_config_keys() -> set[str]:
    """The ``system_notice_*`` group-gate keys declared in ``CONFIG_KEYS``."""
    return {ck.key for ck in CONFIG_KEYS if ck.key.startswith(_SYSTEM_NOTICE_KEY_PREFIX)}


DEFAULT_CONFIG = _build_default_config()


_MISSING = object()


class Settings:
    def __init__(self):
        self.data_dir = get_data_dir()
        self.logs_dir = self.data_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)
        self.load()

    def load(self):
        """Seed missing defaults into the DB. No in-memory cache is kept — reads are write-through to config_repo."""
        current = config_repo.get_all()
        # Migrate legacy audio_transcription_enabled → audio_transcription_mode
        if "audio_transcription_enabled" in current:
            legacy_enabled = current.pop("audio_transcription_enabled")
            if "audio_transcription_mode" not in current:
                migrated = "received" if legacy_enabled else "off"
                config_repo.set("audio_transcription_mode", migrated)
                current["audio_transcription_mode"] = migrated
            config_repo.delete_prefix("audio_transcription_enabled")
        # Persist defaults for any key missing in the DB
        missing = {k: v for k, v in DEFAULT_CONFIG.items() if k not in current}
        if missing:
            # An install that already has an API key configured is NOT a
            # first run — seed setup_completed=True so the setup wizard does
            # not ambush existing users after an update.
            if "setup_completed" in missing:
                env_key = os.environ.get("OPENROUTER_API_KEY", "")
                missing["setup_completed"] = bool(
                    current.get("openrouter_api_key") or env_key
                )
            config_repo.set_many(missing)
        self._seed_installation_metadata(current)

    def _seed_installation_metadata(self, current: dict):
        """Persist per-installation identity on first boot; refresh app_version.

        - ``installation_id``: a UUID minted ONCE and never overwritten — a stable
          identity for this install (support, telemetry, distinguishing instances
          that share the same Postgres). Mirrors Chatwoot's INSTALLATION_IDENTIFIER.
        - ``installed_at``: unix timestamp captured ONCE. For an install predating
          this feature it records the first boot AFTER the upgrade, not the true
          original install time.
        - ``app_version``: the running code version, refreshed on EVERY boot so the
          DB always reflects the deployed build.
        """
        import time
        import uuid

        from config.version import get_app_version

        to_set = {}
        if not current.get("installation_id"):
            to_set["installation_id"] = str(uuid.uuid4())
        if not current.get("installed_at"):
            to_set["installed_at"] = time.time()
        version = get_app_version()
        if current.get("app_version") != version:
            to_set["app_version"] = version
        if to_set:
            config_repo.set_many(to_set)

    @staticmethod
    def _env_override(key: str):
        """Return env-overridden value for ``key`` if present, else ``_MISSING``."""
        mapping = _ENV_OVERRIDES_BY_KEY.get(key)
        if mapping is None:
            return _MISSING
        env_key, cast = mapping
        raw = os.environ.get(env_key)
        if not raw:
            return _MISSING
        try:
            return cast(raw)
        except (ValueError, TypeError):
            return _MISSING

    def save(self):
        """No-op. Kept for backward compatibility — writes are write-through via set()/__setitem__."""
        return

    def get(self, key: str, default=None):
        override = self._env_override(key)
        if override is not _MISSING:
            return override
        value = config_repo.get(key, _MISSING)
        if value is _MISSING:
            return DEFAULT_CONFIG.get(key, default)
        return value

    def set(self, key: str, value):
        config_repo.set(key, value)

    def __getitem__(self, key):
        override = self._env_override(key)
        if override is not _MISSING:
            return override
        value = config_repo.get(key, _MISSING)
        if value is _MISSING:
            if key in DEFAULT_CONFIG:
                return DEFAULT_CONFIG[key]
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        config_repo.set(key, value)
