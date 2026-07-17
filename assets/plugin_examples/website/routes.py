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
import hmac
import logging
import secrets
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from db.repositories import (channel_repo, channel_credential_repo, config_repo,
                             contact_repo, conversation_repo, inbox_repo,
                             message_repo)
from plugins.context import broadcast, get_channel_runtime
from plugins.events import emit_with_filter

from whatsbot_plugins.website import sessions
from whatsbot_plugins.website.bridge import hub

logger = logging.getLogger(__name__)

router = APIRouter()

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
    # get_context_by_conversation returns newest-first (ts DESC); the widget renders
    # top→bottom oldest→newest, so hand it chronological order.
    rows = list(reversed(message_repo.get_context_by_conversation(conv["id"], limit)))
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


def _message_exists(msg_id: str) -> bool:
    """Durable idempotency check for server-side ingest: has this ``msg_id`` (the
    caller's stable ``dedup_id``) already been persisted? Mirrors the pattern the
    curseduca_forum plugin used — survives restarts (unlike the funnel's in-memory
    dedup set), so a webhook re-delivery becomes a no-op."""
    if not msg_id:
        return False
    from sqlalchemy import select
    from db.engine import get_engine
    from db.tables import messages
    with get_engine().connect() as conn:
        row = conn.execute(
            select(messages.c.id).where(messages.c.msg_id == msg_id).limit(1)).first()
    return row is not None


def _server_ingest_sync(*, channel_id: str, external_id: str, name: str,
                        email: str, text: str, msg_id: str, reopen: bool):
    """Persist ONE server-pushed message synchronously (no batching, no AI).

    Why NOT the browser funnel (``parse_inbound`` → ``ctx.ingest_event``): that path
    batches inbound by ``message_batch_delay`` and, when it flushes, MERGES the
    batch into a single row keeping only the LAST ``msg_id`` — which would collapse
    two distinct forum dúvidas into one message and drop the earlier ``dedup_id``,
    breaking idempotency. A forum notice is one discrete post, so we save it directly
    (mirrors the proven curseduca_forum ingest) but target the WEBSITE channel so the
    conversation lands in that inbox and is typed by its provider.

    Returns ``(mem, saved)`` so the async caller can broadcast + emit ``message.saved``
    (which is what protocolos listens on to open the protocol)."""
    from agent.memory import ContactMemory
    # channel_id here → ContactMemory resolves the website provider's contact_type
    # and routes the conversation to the "Avisos Curseduca" inbox. Creates on first
    # touch; an existing student contact is reused (stable identity ⇒ same protocol).
    mem = ContactMemory(external_id, default_ai_enabled=False, channel_id=channel_id)
    if name and not (mem.info.get("name") or "").strip():
        mem.info["name"] = name          # conversation name = the student's name
    if email and not (mem.info.get("email") or "").strip():
        mem.info["email"] = email
    # Panel-only: never auto-reply, never send anything back to the student. The
    # atendente reads the dúvida and answers in the forum (via the link in the text).
    mem.ai_enabled = False
    mem.can_send = False
    mem.save()
    saved = mem.add_message("user", text, msg_id=msg_id,
                            reopen=(None if reopen else False))
    mem.increment_unread(saved.get("msg_id"))
    return mem, saved


def _email_from_external_id(external_id: str) -> str:
    """Best-effort e-mail extraction from ``cursos:aluno@ex.com`` style ids (so the
    panel shows the student's e-mail). Returns "" when there's no ``@`` part."""
    tail = external_id.split(":", 1)[1] if ":" in external_id else external_id
    return tail.strip() if "@" in tail else ""


# ── PUBLIC: server-to-server ingest (Windmill / backend integrations) ─────

@router.post("/public/ingest")
async def public_ingest(body: dict, request: Request):
    """Post a message into a website channel from a TRUSTED backend (no browser).

    Unlike ``/public/messages`` (browser: sorted session id, HMAC identify), this
    endpoint accepts a caller-chosen STABLE identity + display name + text in one
    call, authenticated by the channel's ``ingest_secret`` credential. Built for
    integrations like the Windmill Curseduca-fórum automation:

        {"widget_token": "wgt_…", "secret": "<ingest_secret>",
         "external_id": "cursos:aluno@ex.com",   # stable conversation identity
         "name": "Nome do Aluno",                 # becomes the conversation name
         "text": "<mensagem já formatada>",
         "dedup_id": "curseduca:post:123"}        # idempotency key (→ messages.msg_id)

    Stable ``external_id`` means repeat dúvidas of the same person reuse the same
    contact (so protocolos keeps ONE open protocol per person, reopening on return);
    ``dedup_id`` makes a webhook re-delivery a no-op. Auth-exempt by path
    (``/public/``); the ``secret`` is the actual gate."""
    body = body or {}
    widget_token = str(body.get("widget_token") or "").strip()
    secret = str(body.get("secret") or "").strip()
    external_id = str(body.get("external_id") or "").strip()
    name = str(body.get("name") or "").strip()
    email = str(body.get("email") or "").strip()
    text = str(body.get("text") or "").strip()
    dedup_id = str(body.get("dedup_id") or "").strip()
    reopen = body.get("reopen", True)
    reopen = True if reopen is None else bool(reopen)

    if not widget_token or not external_id or not text:
        return {"ok": False, "error": "widget_token, external_id e text são obrigatórios"}

    row = await asyncio.to_thread(sessions.find_channel_by_widget_token, widget_token)
    if row is None:
        return {"ok": False, "error": "widget_token inválido"}
    channel_id = row["id"]

    # Auth: constant-time compare against the channel's ingest_secret credential.
    ingest_secret = await asyncio.to_thread(
        channel_credential_repo.get, channel_id, "ingest_secret")
    if not ingest_secret:
        logger.warning("[website] /public/ingest called but channel %s has no "
                       "ingest_secret set", channel_id)
        return {"ok": False, "error": "canal sem ingest_secret configurado"}
    if not secret or not hmac.compare_digest(str(secret), str(ingest_secret)):
        return {"ok": False, "error": "unauthorized"}

    # Durable dedup (survives restarts) — a webhook re-delivery is a no-op. The
    # direct save persists synchronously, so a repeat call always sees the prior row.
    if dedup_id and await asyncio.to_thread(_message_exists, dedup_id):
        return {"ok": True, "data": {"skipped": True, "msg_id": dedup_id}}

    if not email:
        email = _email_from_external_id(external_id)
    msg_id = dedup_id or ("wing_" + secrets.token_hex(8))
    try:
        mem, saved = await asyncio.to_thread(
            _server_ingest_sync, channel_id=channel_id, external_id=external_id,
            name=name, email=email, text=text, msg_id=msg_id, reopen=reopen)
    except Exception as e:  # noqa: BLE001
        logger.warning("[website] server ingest failed: %s", e, exc_info=True)
        return {"ok": False, "error": "falha ao processar"}

    # Live panel update + the bus event protocolos consumes to open the protocol.
    try:
        broadcast("new_message", {"phone": mem.phone, "channel_id": channel_id,
                                  "message": saved})
        await emit_with_filter("message.saved", {
            "phone": mem.phone, "channel_id": channel_id, "text": text,
            "msg_id": saved.get("msg_id"), "media_type": None, "media_path": None,
            "media_extras": None, "is_group": False, "group_jid": None,
            "source": "website_ingest", "ts": time.time(),
        })
    except Exception:  # noqa: BLE001 — a failed broadcast/emit must not fail the ingest
        logger.warning("[website] post-ingest broadcast/emit failed", exc_info=True)

    return {"ok": True, "data": {"msg_id": saved.get("msg_id"),
                                 "conversation_id": saved.get("conversation_id"),
                                 "contact_id": mem.id, "channel_id": channel_id,
                                 "skipped": False}}


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

    try:
        msg_id = await _ingest_browser_message(
            sess["channel_id"], sess["chat_id"], text,
            name=sess.get("identifier") or "")
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

@router.get("/channels")
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


@router.get("/reveal-hmac")
async def reveal_hmac(channel_id: str = ""):
    """Return the channel's HMAC secret in clear (operator-only route — used by the
    config screen so the operator can wire ``setUser`` on their backend)."""
    if not channel_id:
        return {"ok": False, "error": "channel_id é obrigatório"}
    token = await asyncio.to_thread(
        channel_credential_repo.get, channel_id, "hmac_token")
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
