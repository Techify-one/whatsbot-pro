"""Website widget routes (plano 46 · 05.2 / 05.3).

Two audiences, split by path:

* ``/api/plugins/website/public/…`` — PUBLIC, auth-exempt (the core exempts any
  ``/api/plugins/<id>/public/`` route — see server/app.py). The BROWSER calls
  these; they authenticate by widget_token + per-visitor session token +
  allowed-domains, never by the operator bearer. Covers: the embeddable widget
  page (with a per-channel ``frame-ancestors`` CSP), session bootstrap +
  offline history (``config``), inbound (``messages``), HMAC ``identify``,
  visitor ``typing``, and the per-visitor ``ws`` (outbound delivery).
* ``/api/plugins/website/…`` (no ``/public/``) — OPERATOR, gated by the normal
  auth middleware. Feeds the plugin's config screen (list channels, reveal the
  HMAC secret).

Inbound reuses the whole funnel: ``parse_inbound`` → ``ctx.ingest_event`` (the
same path every provider uses). Outbound is pushed by ``WebsiteChannel.send_text``
through ``bridge.hub``; the WS endpoint here just registers/unregisters the
visitor sockets.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from db.repositories import (channel_repo, channel_credential_repo, config_repo,
                             contact_repo, conversation_repo, inbox_repo,
                             message_repo)
from plugins.context import broadcast, core_permission, get_channel_runtime

from whatsbot_plugins.website import sessions
from whatsbot_plugins.website.bridge import hub

logger = logging.getLogger(__name__)

router = APIRouter()

PLUGIN_ID = "website"

# ── Trilha de auditoria (docs/PLUGINS_AUDITAVEIS.md) ──────────────────────────
# Import defensivo: o plugin é importável por .zip e pode cair num core anterior
# ao seam — sem o helper ele continua funcionando, só não registra.
#
# NOTA de escopo: as rotas ``/public/*`` (mensagem do visitante, identify, typing)
# NÃO são auditadas — é tráfego de cliente final, de alto volume, e o histórico de
# ``messages`` já é o registro dele. A configuração do canal website (token do
# widget, domínios, cores) é editada pelo core via ``PUT /api/channels/{id}``, que
# já grava ``channel.update``. Sobra daqui a revelação do segredo HMAC.
try:
    from plugins.context import audit as _core_audit
except ImportError:  # pragma: no cover — core antigo
    _core_audit = None


def _audit(action: str, channel_id: str, **kw) -> None:
    """Registra uma ação deste plugin na Auditoria. Nunca quebra a rota.

    Plugin de CANAL: grava como ``channel:<channel_id>`` (não ``plugin:website``)
    para cair no MESMO recurso dos eventos de canal do core.
    """
    if _core_audit is None:
        return
    try:
        _core_audit(PLUGIN_ID, action, resource_type="channel",
                    resource_id=channel_id, **kw)
    except Exception:  # noqa: BLE001 — auditoria nunca derruba a ação auditada
        pass

_HISTORY_LIMIT = 50


# ── helpers ───────────────────────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    """Best-effort client IP for the rate-limit bucket on these PUBLIC endpoints.

    Behind the documented reverse proxy (Coolify/Traefik/nginx) the RIGHTMOST
    X-Forwarded-For entry is the address the trusted proxy actually saw: a caller
    can PREPEND forged entries but can't control the one the proxy appends. Using
    the rightmost hop (not the forgeable leftmost) stops an attacker resetting the
    per-IP bucket every request. No XFF (direct/uvicorn) → the real TCP peer."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else ""


def _widget_config(cfg: dict) -> dict:
    """The non-secret slice of the channel config the browser needs to render."""
    return {
        "title": cfg.get("welcome_title") or "Fale conosco",
        "tagline": cfg.get("welcome_tagline") or "Responderemos em instantes",
        "color": cfg.get("widget_color") or "#0ea5a4",
        "hmac_mandatory": bool(cfg.get("hmac_mandatory")),
    }


def _rate_enabled(cfg: dict) -> bool:
    v = cfg.get("rate_limit_enabled")
    return True if v is None else bool(v)


def _load_history(channel_id: str, chat_id: str, limit: int = _HISTORY_LIMIT) -> list[dict]:
    """Recent user/assistant messages for the session's conversation (offline
    re-sync). ``get_context_by_conversation`` already drops panel-only roles and
    failed sends, so this is exactly the chat the widget should show."""
    contact = contact_repo.get_by_phone(chat_id)
    if not contact:
        return []
    inbox = inbox_repo.get_by_channel(channel_id)
    inbox_id = inbox["id"] if inbox else conversation_repo.DEFAULT_INBOX_ID
    conv = conversation_repo.get_latest_for_contact_inbox(contact["id"], inbox_id)
    if not conv:
        return []
    # get_context_by_conversation already returns chronological order (oldest→newest);
    # the widget renders top→bottom oldest→newest, so pass it through as-is.
    rows = message_repo.get_context_by_conversation(conv["id"], limit)
    out: list[dict] = []
    for r in rows:
        role = r.get("role")
        if role not in ("user", "assistant"):
            continue
        item = {"role": role, "content": r.get("content") or "",
                "msg_id": r.get("msg_id") or "", "ts": r.get("ts") or 0}
        if r.get("media_type"):
            item["media_type"] = r.get("media_type")
            mp = r.get("media_path")
            if mp:
                item["media_url"] = mp if str(mp).startswith(("http", "/")) else "/" + str(mp)
        out.append(item)
    return out


async def _ingest_browser_message(channel_id: str, chat_id: str, text: str,
                                  name: str = "") -> str:
    """Build the inbound event via the channel's ``parse_inbound`` and feed it to
    the shared funnel. Returns the assigned ``msg_id`` (so the widget reconciles
    its optimistic bubble)."""
    registry, _outbound, ingest = get_channel_runtime()
    if registry is None or ingest is None:
        raise RuntimeError("channel runtime not wired")
    inst = registry.get(channel_id)
    if inst is None:
        raise RuntimeError("channel not live")
    msg_id = "wmsg_" + secrets.token_hex(8)
    raw = {"chat_id": chat_id, "text": text, "name": name, "msg_id": msg_id,
           "ts": time.time()}
    events = await asyncio.to_thread(inst.parse_inbound, raw)
    for ev in events or []:
        await ingest(ev)
    return msg_id


# ── PUBLIC: embeddable widget page (per-channel frame-ancestors CSP) ──────

@router.get("/public/widget")
async def widget_page(widget_token: str = "", request: Request = None):
    """Serve the iframe app. Sets its OWN ``Content-Security-Policy`` so only the
    channel's ``allowed_domains`` may embed it (the core middleware respects a
    route-provided CSP). An unknown token still returns a page, but locked to
    ``frame-ancestors 'none'`` so it can't be framed anywhere."""
    row = await asyncio.to_thread(sessions.find_channel_by_widget_token, widget_token)
    if row is None:
        csp = "frame-ancestors 'none'"
        body = _widget_html(widget_token="", cfg={}, error="Widget não encontrado.")
        return HTMLResponse(body, headers={"Content-Security-Policy": csp})
    cfg = sessions.channel_config(row)
    domains = sessions.parse_allowed_domains(cfg.get("allowed_domains"))
    csp = sessions.content_security_policy(domains)
    body = _widget_html(widget_token=widget_token, cfg=cfg)
    return HTMLResponse(body, headers={"Content-Security-Policy": csp})


# ── PUBLIC: session bootstrap + offline history ───────────────────────────

@router.get("/public/config")
async def public_config(widget_token: str = "", session: str = "",
                        request: Request = None):
    row = await asyncio.to_thread(sessions.find_channel_by_widget_token, widget_token)
    if row is None:
        return {"ok": False, "error": "widget_token inválido"}
    channel_id = row["id"]
    cfg = sessions.channel_config(row)
    rate_on = _rate_enabled(cfg)

    sess = await asyncio.to_thread(sessions.get_session, session) if session else None
    if sess and sess.get("channel_id") == channel_id:
        await asyncio.to_thread(sessions.touch_session, sess["session_token"])
    else:
        if not sessions.allow_new_session(_client_ip(request), rate_on):
            return {"ok": False, "error": "rate_limited"}
        sess = await asyncio.to_thread(sessions.mint_session, channel_id,
                                       _client_ip(request))

    history = await asyncio.to_thread(
        _load_history, channel_id, sess["chat_id"], _HISTORY_LIMIT)
    return {"ok": True, "data": {
        "session_token": sess["session_token"],
        "config": _widget_config(cfg),
        "messages": history,
    }}


# ── PUBLIC: inbound message ───────────────────────────────────────────────

@router.post("/public/messages")
async def public_messages(body: dict, request: Request):
    session = (request.headers.get("x-session-token")
               or (body or {}).get("session") or "").strip()
    text = ((body or {}).get("text") or "").strip()
    if not session or not text:
        return {"ok": False, "error": "sessão e texto são obrigatórios"}
    sess = await asyncio.to_thread(sessions.get_session, session)
    if not sess:
        return {"ok": False, "error": "sessão inválida"}
    row = await asyncio.to_thread(channel_repo.get, sess["channel_id"])
    if row is None or row.get("provider") != sessions.PROVIDER:
        return {"ok": False, "error": "canal inválido"}
    cfg = sessions.channel_config(row)

    if not sessions.allow_message(session, _rate_enabled(cfg)):
        return {"ok": False, "error": "rate_limited"}

    # HMAC gate: when the channel demands a verified identity, an un-identified
    # session cannot post. Guard against a mis-config (mandatory but no token set)
    # so a channel is never bricked — treat as not-mandatory + warn.
    if cfg.get("hmac_mandatory"):
        hmac_token = await asyncio.to_thread(
            channel_credential_repo.get, sess["channel_id"], "hmac_token")
        if hmac_token and not sess.get("hmac_verified"):
            return {"ok": False, "error": "identidade não verificada"}
        if not hmac_token:
            logger.warning("[website] channel %s hmac_mandatory but no hmac_token set",
                           sess["channel_id"])

    # Contact name: a verified HMAC identity (setUser) wins; otherwise auto-label
    # the visitor "{nome do canal} - Contato {N}" (stable per session) so the panel
    # never shows the raw session token.
    name = sess.get("identifier") or ""
    if not name:
        name = await asyncio.to_thread(
            sessions.client_display_name, sess["session_token"], sess["channel_id"])

    try:
        msg_id = await _ingest_browser_message(
            sess["channel_id"], sess["chat_id"], text, name=name)
    except Exception as e:  # noqa: BLE001
        logger.warning("[website] ingest failed: %s", e, exc_info=True)
        return {"ok": False, "error": "falha ao processar"}
    await asyncio.to_thread(sessions.touch_session, session)
    return {"ok": True, "data": {"msg_id": msg_id}}


# ── PUBLIC: HMAC setUser (optional) ───────────────────────────────────────

@router.post("/public/identify")
async def public_identify(body: dict, request: Request):
    session = (request.headers.get("x-session-token")
               or (body or {}).get("session") or "").strip()
    identifier = ((body or {}).get("identifier") or "").strip()
    provided = ((body or {}).get("identifier_hash") or "").strip()
    if not session or not identifier or not provided:
        return {"ok": False, "error": "session, identifier e identifier_hash são obrigatórios"}
    sess = await asyncio.to_thread(sessions.get_session, session)
    if not sess:
        return {"ok": False, "error": "sessão inválida"}
    hmac_token = await asyncio.to_thread(
        channel_credential_repo.get, sess["channel_id"], "hmac_token")
    if not hmac_token:
        return {"ok": False, "error": "canal sem HMAC configurado"}
    if not sessions.verify_identifier_hash(hmac_token, identifier, provided):
        return {"ok": False, "error": "assinatura inválida"}
    await asyncio.to_thread(sessions.mark_identified, session, identifier)
    return {"ok": True, "data": {"verified": True}}


# ── PUBLIC: visitor typing → operator presence ────────────────────────────

@router.post("/public/typing")
async def public_typing(body: dict, request: Request):
    session = (request.headers.get("x-session-token")
               or (body or {}).get("session") or "").strip()
    state = ((body or {}).get("state") or "paused").strip()
    sess = await asyncio.to_thread(sessions.get_session, session)
    if not sess:
        return {"ok": False, "error": "sessão inválida"}
    # Best-effort: show the operator the visitor is typing (same event the panel
    # already renders for any channel).
    broadcast("chat_presence", {
        "phone": sess["chat_id"], "channel_id": sess["channel_id"],
        "conversation_id": None,
        "state": "composing" if state == "composing" else "paused", "media": "text"})
    return {"ok": True}


# ── PUBLIC: per-visitor WebSocket (outbound delivery) ─────────────────────

@router.websocket("/public/ws")
async def widget_ws(websocket: WebSocket):
    """Register the visitor's socket under its session's chat_id. Isolation: only
    messages delivered to THIS chat_id (via bridge.hub) reach this socket — a
    visitor never sees another conversation, and the operator ``/ws`` is separate."""
    session = websocket.query_params.get("session", "")
    sess = await asyncio.to_thread(sessions.get_session, session) if session else None
    if not sess:
        await websocket.close(code=4001)
        return
    chat_id = sess["chat_id"]
    await websocket.accept()
    hub.register(chat_id, websocket)
    try:
        while True:
            # Visitors POST their messages over REST; anything received here is a
            # keep-alive/ping — read + discard so the socket stays open.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        hub.unregister(chat_id, websocket)


# ── OPERATOR: config-screen APIs (normal auth) ────────────────────────────

@router.get("/channels", dependencies=[core_permission("channel.manage")])
async def list_website_channels():
    """Website channels + the data the config screen needs to render the embed
    snippet. ``widget_token`` is public; the HMAC secret is NOT returned here."""
    rows = await asyncio.to_thread(channel_repo.list_all)
    base = await asyncio.to_thread(config_repo.get, "public_base_url")
    out = []
    for r in rows:
        if r.get("provider") != sessions.PROVIDER:
            continue
        cfg = sessions.channel_config(r)
        creds = await asyncio.to_thread(
            channel_credential_repo.get_all, r["id"])
        out.append({
            "id": r["id"],
            "display_name": r.get("display_name") or r["id"],
            "enabled": bool(r.get("enabled", 1)),
            "widget_token": cfg.get("widget_token") or "",
            "allowed_domains": cfg.get("allowed_domains") or "",
            "hmac_set": bool((creds or {}).get("hmac_token")),
        })
    return {"ok": True, "data": {"channels": out, "public_base_url": base or ""}}


@router.get("/reveal-hmac", dependencies=[core_permission("channel.manage")])
async def reveal_hmac(channel_id: str = ""):
    """Return the channel's HMAC secret in clear (operator-only route — used by the
    config screen so the operator can wire ``setUser`` on their backend)."""
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    token = await asyncio.to_thread(
        channel_credential_repo.get, channel_id, "hmac_token")
    # Exceção deliberada à regra "GET não audita": esta rota ENTREGA um segredo em
    # claro. Quem viu o segredo do canal é justamente o que a trilha precisa
    # guardar — o valor, claro, nunca entra na linha.
    _audit("hmac.reveal", channel_id, after={"revelado": bool(token)})
    return {"ok": True, "data": {"hmac_token": token or ""}}


# ── widget page HTML ──────────────────────────────────────────────────────

def _widget_html(widget_token: str, cfg: dict, error: str = "") -> str:
    """The iframe document. Self-contained shell that boots ``widget.js`` (served
    same-origin from the static mount). Config is injected as an inline JS object.
    ``<`` is neutralised so a config value can't close the <script> early."""
    import json
    boot = {
        "widgetToken": widget_token,
        "title": (cfg.get("welcome_title") or "Fale conosco"),
        "tagline": (cfg.get("welcome_tagline") or "Responderemos em instantes"),
        "color": (cfg.get("widget_color") or "#0ea5a4"),
        "hmacMandatory": bool(cfg.get("hmac_mandatory")),
        "error": error,
    }
    blob = json.dumps(boot).replace("<", "\\u003c")
    return (
        "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Chat</title></head><body>"
        "<div id=\"wb-root\"></div>"
        f"<script>window.__WB_WIDGET__={blob};</script>"
        "<script src=\"/plugins/website/static/widget.js\"></script>"
        "</body></html>"
    )
