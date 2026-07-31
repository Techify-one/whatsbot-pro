"""Cliente HMAC do executor Claude Code (plano 51 · 02 F1).

Porta fiel do ``ai-server-client.service.ts`` do nexus: toda request ao executor
é assinada com HMAC-SHA256 sobre a string canônica
``METHOD\\npath\\nts\\nrequestId\\nbody`` (path SEM query; body = a string JSON
EXATA que sai no wire, ``""`` em GET), janela de 60s, headers ``X-WB-*``.

Config resolvida DB > env > default (molde ``settings.service.ts``):
- ``plugin.melhorias.ai_server_url``    (env ``WHATSBOT_MELHORIAS_AI_URL``)
- ``plugin.melhorias.ai_server_secret`` (env ``WHATSBOT_MELHORIAS_AI_SECRET``, ≥32 chars)
- ``plugin.melhorias.ai_model``         (modelo pedido ao executor; vazio = default dele)
- ``plugin.melhorias.ai_timeout_ms``    (timeout por request não-stream; default 30000)
- ``plugin.melhorias.callback_url``     (env ``WHATSBOT_MELHORIAS_CALLBACK_URL`` —
  URL base DESTA instância, que o executor chama de volta nos ``_internal/*``;
  vazio = usa a config global ``public_base_url``. Torna o executor
  multi-instância: cada conversa leva o próprio ``callbackUrl``)

O segredo NUNCA aparece em log/URL; o GET /config o mascara (``***``).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import logging
import os
import secrets
import time
from typing import AsyncIterator

import httpx

from db.repositories import config_repo

logger = logging.getLogger(__name__)

PLUGIN_ID = "melhorias"
SECRET_MIN_LEN = 32


# ── Config (DB > env > default) ──────────────────────────────────────────────

def _cfg(name: str, env: str, default: str = "") -> str:
    v = str(config_repo.get(f"plugin.{PLUGIN_ID}.{name}", "") or "").strip()
    if v:
        return v
    return (os.environ.get(env) or "").strip() or default


def server_url() -> str:
    return _cfg("ai_server_url", "WHATSBOT_MELHORIAS_AI_URL").rstrip("/")


def shared_secret() -> str:
    return _cfg("ai_server_secret", "WHATSBOT_MELHORIAS_AI_SECRET")


def default_model() -> str:
    return _cfg("ai_model", "WHATSBOT_MELHORIAS_AI_MODEL")


def timeout_seconds() -> float:
    raw = _cfg("ai_timeout_ms", "WHATSBOT_MELHORIAS_AI_TIMEOUT_MS", "30000")
    try:
        return max(1.0, float(raw) / 1000.0)
    except (TypeError, ValueError):
        return 30.0


def is_configured() -> bool:
    """URL setada + secret com tamanho mínimo (gate dormente, D02-e)."""
    return bool(server_url()) and len(shared_secret()) >= SECRET_MIN_LEN


def callback_url() -> str:
    """URL base desta instância para os callbacks ``_internal/*`` do executor.

    Override explícito (``plugin.melhorias.callback_url``) > ``public_base_url``
    global (auto-capturada no 1º acesso ao painel). Vazio ⇒ o start falha com
    mensagem acionável (o executor não tem mais fallback fixo)."""
    explicit = _cfg("callback_url", "WHATSBOT_MELHORIAS_CALLBACK_URL")
    if explicit:
        return explicit.rstrip("/")
    return str(config_repo.get("public_base_url", "") or "").strip().rstrip("/")


def _require_callback_url() -> str:
    url = callback_url()
    if not url:
        raise RuntimeError(
            "URL de callback indisponível: configure a 'URL de callback (este "
            "WhatsBot)' na seção Melhoria agêntica (ou acesse o painel uma vez "
            "para capturar a public_base_url)."
        )
    return url


# ── Assinatura ───────────────────────────────────────────────────────────────

def sign(secret: str, method: str, path: str, ts: str, request_id: str,
         body: str) -> str:
    """HMAC-SHA256 hex sobre a string canônica (molde ``hmac.util.ts:14-22``)."""
    payload = "\n".join([method.upper(), path, ts, request_id, body])
    return hmac_mod.new(secret.encode(), payload.encode(),
                        hashlib.sha256).hexdigest()


def new_request_id() -> str:
    """Nonce curto base16 (molde ``hmac.util.ts:70-77``)."""
    return f"{int(time.time() * 1000):x}-{secrets.token_hex(4)}"


def signed_headers(method: str, path: str, body: str,
                   on_behalf_of: int | str | None) -> dict:
    ts = str(int(time.time()))
    rid = new_request_id()
    sig = sign(shared_secret(), method, path, ts, rid, body)
    headers = {
        "X-WB-Timestamp": ts,
        "X-WB-Signature": sig,
        "X-WB-Request-Id": rid,
        "X-WB-On-Behalf-Of": str(on_behalf_of if on_behalf_of is not None else ""),
    }
    return headers


# ── Chamadas ao executor ─────────────────────────────────────────────────────

async def _post(path: str, payload: dict, on_behalf_of=None) -> dict:
    """POST assinado. O ``body`` assinado é a MESMA string enviada no wire."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    headers = signed_headers("POST", path, body, on_behalf_of)
    headers["Content-Type"] = "application/json"
    url = server_url() + path
    async with httpx.AsyncClient(timeout=timeout_seconds()) as client:
        resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {}


async def start(conversation_id: str, *, user_id, target: dict,
                model: str = "") -> dict:
    return await _post("/conversations", {
        "conversationId": conversation_id,
        "userId": str(user_id if user_id is not None else ""),
        "target": target,
        "model": model or default_model() or None,
        "callbackUrl": _require_callback_url(),
    }, on_behalf_of=user_id)


async def send(conversation_id: str, *, user_id, text: str = "",
               parts: list | None = None) -> dict:
    payload: dict = {}
    if parts:
        payload["parts"] = parts
    else:
        payload["text"] = text
    return await _post(f"/conversations/{conversation_id}/messages",
                       payload, on_behalf_of=user_id)


async def approve(conversation_id: str, *, user_id, approval_id: str,
                  approved: bool, reason: str = "") -> dict:
    return await _post(f"/conversations/{conversation_id}/approve", {
        "approvalId": approval_id, "approved": bool(approved),
        "reason": (reason or "")[:500],
    }, on_behalf_of=user_id)


async def cancel(conversation_id: str, *, user_id) -> dict:
    return await _post(f"/conversations/{conversation_id}/cancel", {},
                       on_behalf_of=user_id)


async def resume(conversation_id: str, *, user_id, target: dict,
                 history: list[dict], model: str = "") -> dict:
    return await _post(f"/conversations/{conversation_id}/resume", {
        "userId": str(user_id if user_id is not None else ""),
        "target": target,
        "history": history,
        "model": model or default_model() or None,
        "callbackUrl": _require_callback_url(),
    }, on_behalf_of=user_id)


async def relogin_start(user_id) -> dict:
    return await _post("/admin/relogin/start", {}, on_behalf_of=user_id)


async def relogin_complete(user_id, session_id: str, code: str) -> dict:
    return await _post("/admin/relogin/complete",
                       {"sessionId": session_id, "code": code},
                       on_behalf_of=user_id)


async def relogin_abort(user_id, session_id: str) -> dict:
    return await _post("/admin/relogin/abort", {"sessionId": session_id},
                       on_behalf_of=user_id)


async def auth_check(user_id=None) -> dict:
    """Prova que o secret bate (``/auth-check`` exige HMAC; ``/health`` não)."""
    body = ""
    headers = signed_headers("GET", "/auth-check", body, user_id)
    async with httpx.AsyncClient(timeout=timeout_seconds()) as client:
        resp = await client.get(server_url() + "/auth-check", headers=headers)
        return {"status_code": resp.status_code,
                "ok": resp.status_code == 200}


async def health(user_id=None) -> dict:
    """``GET /health`` do executor — best-effort (plano 60 · 2.8).

    O executor REAL provavelmente não implementa a rota (é a camada 3); por isso
    a resposta é honesta sobre o que NÃO sabe: ``supported=False`` quando a rota
    não existe, e o painel mostra "desconhecido" em vez de inventar um veredito.
    Vai assinado por precaução (a rota não exige HMAC — headers extras são inertes).
    """
    headers = signed_headers("GET", "/health", "", user_id)
    async with httpx.AsyncClient(timeout=timeout_seconds()) as client:
        resp = await client.get(server_url() + "/health", headers=headers)
    if resp.status_code == 404:
        return {"supported": False, "status_code": 404}
    try:
        data = resp.json()
    except ValueError:
        data = {}
    claude = data.get("claude") if isinstance(data, dict) else None
    return {"supported": resp.status_code == 200,
            "status_code": resp.status_code,
            "claude": claude if isinstance(claude, dict) else None}


async def open_stream(conversation_id: str, *, user_id) -> AsyncIterator[bytes]:
    """SSE do executor (hop executor→gateway, D5). Iterador de bytes crus;
    o consumidor (chat_logic) parseia frames e re-emite no /ws do operador."""
    path = f"/conversations/{conversation_id}/stream"
    headers = signed_headers("GET", path, "", user_id)
    headers["Accept"] = "text/event-stream"
    url = server_url() + path
    # Sem timeout de leitura: o stream vive enquanto a conversa (heartbeat 30s).
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk
