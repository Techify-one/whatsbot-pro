"""Configuration endpoints (config, test-key, models, status)."""

import asyncio
import ipaddress
import logging
import os
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Request

from config.settings import (LLM_API_BASE_URL, exposed_config_keys,
                             writable_config_keys)
from server.auth import generate_salt, hash_password
from server.authz import permission_denied
from server.helpers import _ok, _err, _mask_key
from server import balance_monitor
from agent import group_mentions
from plugins.events import emit as emit_event, emit_with_filter

logger = logging.getLogger(__name__)


# ── Models cache ──────────────────────────────────────────────
_models_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_MODELS_CACHE_TTL = 600  # 10 minutes


def get_models_cache() -> dict[str, Any]:
    """Expose models cache for pricing lookup."""
    return _models_cache


# ── Public base URL capture ───────────────────────────────────
# The URL the operator uses to reach the panel is captured on the first panel
# load and stored as the core config ``public_base_url`` — a global variable
# reusable by any feature (e.g. the GOWA disconnect alert builds its reconnect
# link from it). Honors reverse-proxy headers so a Coolify deploy records the
# real public domain.


def _is_public_host(hostname: str) -> bool:
    """True para um host que um cliente externo alcançaria (domínio ou IP público).
    False para loopback, IP privado/LAN, link-local, localhost e ``*.local`` — ou
    seja, hosts que só valem dentro da própria máquina/rede."""
    h = (hostname or "").split(":")[0].strip().lower()
    if not h or h == "localhost" or h.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(h)
        return not (ip.is_private or ip.is_loopback
                    or ip.is_link_local or ip.is_unspecified)
    except ValueError:
        return True  # nome de domínio, não IP literal → assume público


def _is_public_url(url: str) -> bool:
    try:
        return _is_public_host(urlsplit(url).hostname or "")
    except ValueError:
        return False


def _request_origin(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or request.url.netloc or "").split(",")[0].strip()
    return f"{proto}://{host}" if host else ""


def _capture_public_base_url(settings, request: Request) -> None:
    """Mantém ``public_base_url`` (a URL que o operador usa pra acessar o painel).

    Prioridade: (1) override explícito por env ``WHATSBOT_PUBLIC_URL`` /
    ``PUBLIC_BASE_URL`` — autoritativo, para proxies que não repassam os headers
    ``x-forwarded-*``; (2) primeiro uso sem valor salvo → grava a origem atual;
    (3) self-heal — um valor salvo NÃO-público (loopback OU IP de LAN OU ``*.local``)
    é substituído assim que uma origem pública (domínio real) aparece. Um valor
    público já salvo (inclusive editado à mão na UI) nunca é sobrescrito."""
    env = (os.environ.get("WHATSBOT_PUBLIC_URL")
           or os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if env:
        if (settings.get("public_base_url", "") or "").strip() != env:
            settings["public_base_url"] = env
        return
    origin = _request_origin(request)
    if not origin:
        return
    saved = (settings.get("public_base_url", "") or "").strip()
    if not saved or (not _is_public_url(saved) and _is_public_url(origin)):
        settings["public_base_url"] = origin.rstrip("/")


def register_routes(app, deps):
    settings = deps.settings
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager
    state = deps.state

    @app.get("/api/config")
    async def get_config(request: Request):
        # Capture the panel's public base URL on first use (global var for reuse).
        _capture_public_base_url(settings, request)
        # R17: the exposed keys + their GET fallbacks come from the single config-key
        # metadata table (config.settings.CONFIG_KEYS). The two special keys —
        # ``openrouter_api_key`` (masked) and ``has_password`` (derived) — stay inline.
        out = {"openrouter_api_key": _mask_key(settings.get("openrouter_api_key", ""))}
        for ck in exposed_config_keys():
            out[ck.key] = settings.get(ck.key, ck.effective_get_default)
        out["has_password"] = bool(settings.get("web_password_hash", ""))
        return _ok(out)

    @app.put("/api/config")
    async def save_config(body: dict, request: Request):
        denied = permission_denied(request, "settings.manage")
        if denied:
            return denied
        # R17: the PUT allowlist is derived from the single config-key metadata table.
        allowed_keys = writable_config_keys()
        keys_changed = []
        audit_before = {}   # {key: old_value} for the audit trail
        audit_after = {}    # {key: new_value} for the audit trail
        for key, value in body.items():
            if key in allowed_keys:
                # Canoniza a URL base (sem espaços/barra final) para os consumidores
                # não dependerem de normalizar por conta própria.
                if key == "public_base_url" and isinstance(value, str):
                    value = value.strip().rstrip("/")
                audit_before[key] = settings.get(key)
                settings[key] = value
                audit_after[key] = value
                keys_changed.append(key)

        # Handle password set/change/remove
        if "web_password" in body:
            raw_password = body["web_password"]
            if raw_password:
                salt = generate_salt()
                settings["web_password_hash"] = hash_password(raw_password, salt)
                settings["web_password_salt"] = salt
                logger.info("Web panel password set/changed.")
            else:
                settings["web_password_hash"] = ""
                settings["web_password_salt"] = ""
                logger.info("Web panel password removed.")

        settings.save()

        # Bot phone changed → refresh mention detection (the bot's display name
        # comes from GOWA, not config — see background.py).
        if "bot_phone" in keys_changed:
            group_mentions.set_bot_identity(state.bot_phone, state.bot_name)

        # plano 43 — the history blacklist compiles from config with a 30s TTL cache;
        # invalidate it on save so an edit applies immediately (not after ≤30s).
        if "ai_history_exclude_patterns" in keys_changed:
            from agent import history_filter
            history_filter.reset_cache()

        agent_handler.update_config(
            api_key=settings.get("openrouter_api_key", ""),
            audio_model=settings.get("audio_model", "google/gemini-2.5-flash"),
            image_model=settings.get("image_model", "google/gemini-2.5-flash"),
            document_model=settings.get("document_model", "google/gemini-2.5-flash"),
            max_context_messages=settings.get("max_context_messages", 10),
            split_messages=settings.get("split_messages", True),
            default_ai_enabled=settings.get("default_ai_enabled", True),
        )

        await ws_manager.broadcast("config_saved", {})
        await emit_with_filter("config.changed", {
            "keys_changed": keys_changed,
            "ts": time.time(),
            "_audit_before": audit_before,
            "_audit_after": audit_after,
        })
        logger.info("Config saved.")
        return _ok({"message": "Configurações salvas!"})

    @app.post("/api/config/test-key")
    async def test_api_key(body: dict, request: Request):
        # Auto-saves the api_key on success ⇒ a disguised write; gate like PUT /config.
        denied = permission_denied(request, "settings.manage")
        if denied:
            return denied
        api_key = body.get("api_key", "").strip()
        if not api_key:
            return _err("Insira uma API key primeiro.")
        ok, msg = await asyncio.to_thread(agent_handler.test_api_key, api_key)
        # Auto-save valid key
        if ok:
            settings["openrouter_api_key"] = api_key
            settings.save()
            agent_handler.update_config(
                api_key=api_key,
                audio_model=settings.get("audio_model", "google/gemini-2.5-flash"),
                image_model=settings.get("image_model", "google/gemini-2.5-flash"),
                document_model=settings.get("document_model", "google/gemini-2.5-flash"),
                max_context_messages=settings.get("max_context_messages", 10),
            )
            logger.info("API key tested and auto-saved.")
        return _ok({"valid": ok, "message": msg})

    @app.get("/api/models")
    async def list_models():
        """Return OpenRouter-compatible model list (cached for 10 min)."""
        now = time.time()
        if _models_cache["data"] and now - _models_cache["fetched_at"] < _MODELS_CACHE_TTL:
            return _ok(_models_cache["data"])
        api_key = settings.get("openrouter_api_key", "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{LLM_API_BASE_URL}/models", headers=headers)
                resp.raise_for_status()
                raw = resp.json()
            models = []
            for m in raw.get("data", []):
                arch = m.get("architecture", {})
                models.append({
                    "id": m.get("id", ""),
                    "name": m.get("name", ""),
                    "input_modalities": arch.get("input_modalities", ["text"]),
                    "pricing": m.get("pricing", {}),
                })
            models.sort(key=lambda x: x["name"].lower())
            _models_cache["data"] = models
            _models_cache["fetched_at"] = now
            return _ok(models)
        except Exception as e:
            logger.error("Failed to fetch models from %s: %s", LLM_API_BASE_URL, e)
            if _models_cache["data"]:
                return _ok(_models_cache["data"])
            return _err(f"Erro ao buscar modelos: {e}", status=502)

    @app.get("/api/balance")
    async def get_balance():
        """Return current OpenRouter credit + threshold settings.

        Used by the frontend on boot to seed the low-balance check before any
        message goes through; the live updates come via the ``low_balance`` WS
        event emitted by ``balance_monitor`` after LLM calls.
        """
        api_key = settings.get("openrouter_api_key", "")
        if not api_key:
            return _err("API key não configurada.", status=400)
        threshold = float(settings.get("low_balance_threshold", 0.50) or 0.50)
        low_enabled = bool(settings.get("low_balance_enabled", True))
        account_url = settings.get("account_url", "")
        balance = await balance_monitor.fetch_balance(api_key)
        if balance is None:
            cached = balance_monitor.get_cached()
            if cached is None:
                # plano 42 C: degrade instead of 502 — the proxy /credits is down
                # AND there is no cached snapshot yet (e.g. right after boot, before
                # any LLM call). Respond 200 with available:false so the panel shows
                # "saldo indisponível" instead of an alarming error / "sem saldo".
                return _ok({
                    "available": False,
                    "balance": None,
                    "reason": "Não foi possível consultar o saldo (proxy indisponível).",
                    "threshold": threshold,
                    "low_balance_enabled": low_enabled,
                    "account_url": account_url,
                })
            balance = {
                "total_credits": cached.get("total_credits", 0.0),
                "total_usage": cached.get("total_usage", 0.0),
                "remaining": cached.get("remaining", 0.0),
            }
        return _ok({
            **balance,
            "available": True,
            "balance": balance["remaining"],   # plano 42 C: alias for the FE gate
            "threshold": threshold,
            "low_balance_enabled": low_enabled,
            "below_threshold": balance["remaining"] < threshold,
            "account_url": account_url,
        })

    @app.get("/api/status")
    async def get_status():
        return _ok({
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
            "notification": state.notification,
            "bot_phone": state.bot_phone,
            "bot_name": state.bot_name,
        })
