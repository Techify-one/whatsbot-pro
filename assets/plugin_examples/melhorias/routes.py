"""REST endpoints do plugin ``melhorias`` (montados em /api/plugins/melhorias).

Casca fina sobre ``logic.py``. Gating por ``plugin_permission``; o handler (para
gerar a análise na aprovação) vem de ``get_deps()`` (ou ``app.state.deps`` no
harness de testes). Formato ``{"ok", "data"|"error"}``.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request

from plugins.context import get_deps, plugin_permission
from server.authz import current_user

from . import ai_client, chat_logic, logic
from .internal_routes import router as internal_router

router = APIRouter()
# Callbacks do executor (HMAC): /public/_internal/* — auth-exempt via a
# convenção /public/ do core; a autenticação real é o hmac_guard (plano 51).
router.include_router(internal_router)


def _actor(request: Request) -> tuple[int | None, str]:
    u = current_user(request) or {}
    name = str(u.get("name") or u.get("email") or "")
    return u.get("id"), name


def _handler(request: Request):
    """AgentHandler p/ a geração da análise. get_deps() é None no harness; cai
    em app.state.deps (setado no create_app), então funciona nos testes."""
    deps = get_deps() or getattr(request.app.state, "deps", None)
    return getattr(deps, "agent_handler", None) if deps else None


def _err(msg: str, status: int = 400):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status, content={"ok": False, "error": msg})


def _maybe_list(v):
    """Valor único OU lista JSON (multi-seleção do filtro). Ambos aceitos."""
    if v is None:
        return None
    s = str(v).strip()
    if s.startswith("["):
        try:
            p = json.loads(s)
            return p if isinstance(p, list) else v
        except (ValueError, TypeError):
            return v
    return v


# ── Sugestões ────────────────────────────────────────────────────────────────

@router.post("/suggestions", dependencies=[plugin_permission("request")])
async def create_suggestion(body: dict, request: Request):
    uid, name = _actor(request)
    body = body or {}
    target = body.get("message") or {}
    # plano 51: multi-seleção — `messages:[{content,ts,_id,...}]`. O singular
    # `message:{...}` continua aceito (compat).
    targets = body.get("messages")
    if not isinstance(targets, list):
        targets = None
    data, err = await asyncio.to_thread(
        logic.create_suggestion,
        phone=str(body.get("phone") or ""),
        target_message=target,
        target_messages=targets,
        feedback=str(body.get("feedback") or ""),
        conversation_id=body.get("conversation_id"),
        requester_user_id=uid, requester_name=name)
    if err:
        return _err(err, status=404 if "não encontrado" in err else 400)
    return {"ok": True, "data": data}


@router.get("/suggestions", dependencies=[plugin_permission("view")])
async def list_suggestions(status: str | None = None, requester_user_id: str | None = None,
                           approver_user_id: str | None = None,
                           contact_id: int | None = None, conversation_id: int | None = None,
                           model: str | None = None, q: str | None = None,
                           requested_from: float | None = None, requested_to: float | None = None,
                           decided_from: float | None = None, decided_to: float | None = None,
                           limit: int = 200, offset: int = 0):
    data = await asyncio.to_thread(
        logic.list_suggestions,
        status=_maybe_list(status),
        requester_user_id=_maybe_list(requester_user_id),
        approver_user_id=_maybe_list(approver_user_id),
        contact_id=contact_id, conversation_id=conversation_id,
        model=model, q=q,
        requested_from=requested_from, requested_to=requested_to,
        decided_from=decided_from, decided_to=decided_to,
        limit=limit, offset=offset)
    return {"ok": True, "data": data}


@router.get("/suggestions/{sid}", dependencies=[plugin_permission("view")])
async def get_suggestion(sid: int):
    data = await asyncio.to_thread(logic.get_suggestion, sid)
    if not data:
        return _err("Sugestão não encontrada.", status=404)
    return {"ok": True, "data": data}


@router.post("/suggestions/{sid}/approve", dependencies=[plugin_permission("approve")])
async def approve_suggestion(sid: int, request: Request):
    uid, name = _actor(request)
    # plano 51: com o backend agêntico, aprovar = abrir a conversa (gate D1-a)
    # em vez de gerar a análise inline. O painel novo usa o endpoint dedicado
    # POST /suggestions/{sid}/conversations (com observação); este continua
    # funcionando para chamadas legadas.
    if logic._setting("generator_backend", "external") == "external":
        conv, err = await chat_logic.start_conversation(
            sid, observation="", model="", user_id=uid, handler=_handler(request))
        if err:
            status = 404 if "não encontrada" in err else (409 if "já foi" in err else 400)
            return _err(err, status=status)
        return {"ok": True, "data": {"conversation": conv,
                                     "suggestion": logic.get_suggestion(sid)}}
    data, err = await asyncio.to_thread(
        logic.decide_suggestion, sid, "aprovada",
        handler=_handler(request), approver_user_id=uid, approver_name=name)
    if err:
        status = 404 if "não encontrada" in err else (409 if "já foi" in err else 400)
        return _err(err, status=status)
    return {"ok": True, "data": data}


@router.post("/suggestions/{sid}/reject", dependencies=[plugin_permission("approve")])
async def reject_suggestion(sid: int, request: Request):
    uid, name = _actor(request)
    data, err = await asyncio.to_thread(
        logic.decide_suggestion, sid, "recusada",
        handler=_handler(request), approver_user_id=uid, approver_name=name)
    if err:
        status = 404 if "não encontrada" in err else (409 if "já foi" in err else 400)
        return _err(err, status=status)
    return {"ok": True, "data": data}


# ── Conversa agêntica (plano 51 · 02 F3) ─────────────────────────────────────

@router.post("/suggestions/{sid}/conversations",
             dependencies=[plugin_permission("approve")])
async def start_agentic_conversation(sid: int, body: dict, request: Request):
    """Gate D1-(a): humano aprova a IA COMEÇAR + injeta observação extra."""
    uid, _name = _actor(request)
    body = body or {}
    conv, err = await chat_logic.start_conversation(
        sid, observation=str(body.get("observation") or ""),
        model=str(body.get("model") or ""), user_id=uid,
        handler=_handler(request))
    if err:
        status = (404 if "não encontrada" in err
                  else 409 if "já foi" in err
                  else 400)
        return _err(err, status=status)
    return {"ok": True, "data": {"conversation": conv,
                                 "suggestion": logic.get_suggestion(sid)}}


@router.get("/suggestions/{sid}/conversations",
            dependencies=[plugin_permission("view")])
async def list_agentic_conversations(sid: int):
    return {"ok": True, "data": await asyncio.to_thread(
        chat_logic.list_conversations, sid)}


@router.get("/conversations/{cid}", dependencies=[plugin_permission("view")])
async def get_agentic_conversation(cid: str):
    conv = await asyncio.to_thread(chat_logic.get_conversation, cid)
    if not conv:
        return _err("Conversa não encontrada.", status=404)
    messages = await asyncio.to_thread(chat_logic.list_chat_messages, cid)
    approvals = await asyncio.to_thread(chat_logic.list_approvals, cid)
    return {"ok": True, "data": {"conversation": conv, "messages": messages,
                                 "approvals": approvals}}


@router.post("/conversations/{cid}/messages",
             dependencies=[plugin_permission("approve")])
async def send_agentic_message(cid: str, body: dict, request: Request):
    """Mensagem do humano no chat: ``{text}`` ou ``{parts:[{type:'text'|'image',…}]}``."""
    uid, _name = _actor(request)
    body = body or {}
    conv = await asyncio.to_thread(chat_logic.get_conversation, cid)
    if not conv:
        return _err("Conversa não encontrada.", status=404)
    text = str(body.get("text") or "")
    parts = body.get("parts") if isinstance(body.get("parts"), list) else None
    if parts:
        # Imagens selecionadas (media_path) viram base64 AQUI — o executor não
        # lê arquivo; path confinado a statics/ (anti-traversal).
        parts = await asyncio.to_thread(chat_logic.resolve_image_parts, parts)
    if not text.strip() and not parts:
        return _err("Mensagem vazia.")
    try:
        chat_logic.ensure_consumer(cid, conv.get("suggestion_id"), user_id=uid)
        await ai_client.send(cid, user_id=uid, text=text, parts=parts)
    except Exception as e:  # noqa: BLE001
        # 404 do executor = runner in-memory morto (restart/erro). Auto-resume
        # (recria o runner hidratando o histórico) e reenvia UMA vez — o
        # operador não precisa saber que o executor reiniciou.
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code != 404:
            return _err(f"Falha ao enviar ao executor: {e}", status=502)
        _conv, err = await chat_logic.resume_conversation(cid, user_id=uid)
        if err:
            return _err(f"Conversa inativa no executor e o resume falhou: {err}",
                        status=502)
        try:
            await ai_client.send(cid, user_id=uid, text=text, parts=parts)
        except Exception as e2:  # noqa: BLE001
            return _err(f"Falha ao reenviar após resume: {e2}", status=502)
    # O executor só persiste as mensagens do ASSISTANT (write-through); a do
    # humano é persistida AQUI (a mensagem inicial de contexto do start não é
    # persistida de propósito — não polui a hidratação do chat).
    try:
        await asyncio.to_thread(
            chat_logic.append_chat_message, cid, "user",
            content=text.strip() or "[imagem enviada]")
    except Exception:  # noqa: BLE001 — persistência best-effort
        pass
    return {"ok": True, "data": {"sent": True}}


@router.post("/conversations/{cid}/approve",
             dependencies=[plugin_permission("approve")])
async def approve_agentic_tool(cid: str, body: dict, request: Request):
    """Gate D1-(b): V/X por mutação. Idempotente (já decidido ⇒ 409)."""
    uid, _name = _actor(request)
    body = body or {}
    aid = str(body.get("approval_id") or body.get("approvalId") or "")
    if not aid:
        return _err("approval_id obrigatório.")
    approved = bool(body.get("approved"))
    reason = str(body.get("reason") or "")
    row, err = await asyncio.to_thread(
        chat_logic.decide_approval, aid, cid,
        approved=approved, reason=reason, decided_by=uid)
    if err:
        return _err(err, status=409 if "já decidida" in err else 404)
    try:
        await ai_client.approve(cid, user_id=uid, approval_id=aid,
                                approved=approved, reason=reason)
    except Exception as e:  # noqa: BLE001
        return _err(f"Decisão registrada, mas o executor não respondeu: {e}",
                    status=502)
    return {"ok": True, "data": row}


@router.post("/conversations/{cid}/cancel",
             dependencies=[plugin_permission("approve")])
async def cancel_agentic_conversation(cid: str, request: Request):
    uid, _name = _actor(request)
    conv = await asyncio.to_thread(chat_logic.get_conversation, cid)
    if not conv:
        return _err("Conversa não encontrada.", status=404)
    try:
        await ai_client.cancel(cid, user_id=uid)
    except Exception:  # noqa: BLE001 — executor fora do ar não impede o cancelamento local
        pass
    conv = await asyncio.to_thread(chat_logic.set_conversation_status, cid, "CANCELLED")
    chat_logic.stop_consumer(cid)
    return {"ok": True, "data": conv}


@router.post("/conversations/{cid}/complete",
             dependencies=[plugin_permission("approve")])
async def complete_agentic_conversation(cid: str, request: Request):
    """Encerramento MANUAL (plano 58): para o runner do executor, fecha a conversa
    (COMPLETED) e finaliza a sugestão como ``concluida`` (distinto do auto-COMPLETED
    do executor, que marca ``aprovada``)."""
    uid, _name = _actor(request)
    conv = await asyncio.to_thread(chat_logic.get_conversation, cid)
    if not conv:
        return _err("Conversa não encontrada.", status=404)
    try:
        await ai_client.cancel(cid, user_id=uid)
    except Exception:  # noqa: BLE001 — executor fora do ar não impede o encerramento local
        pass
    conv = await asyncio.to_thread(chat_logic.conclude_conversation, cid)
    chat_logic.stop_consumer(cid)
    return {"ok": True, "data": conv}


@router.post("/conversations/{cid}/resume",
             dependencies=[plugin_permission("approve")])
async def resume_agentic_conversation(cid: str, request: Request):
    """Recria o runner in-memory do executor hidratando do DB (restart do
    executor perde os runners — 'Continuar' reabre)."""
    uid, _name = _actor(request)
    conv, err = await chat_logic.resume_conversation(cid, user_id=uid)
    if err:
        return _err(err, status=404 if "não encontrada" in err else 502)
    return {"ok": True, "data": conv}


# ── Relogin OAuth do executor (proxy assinado) ───────────────────────────────

@router.post("/admin/relogin/start", dependencies=[plugin_permission("configure")])
async def relogin_start(request: Request):
    uid, _name = _actor(request)
    try:
        data = await ai_client.relogin_start(uid)
    except Exception as e:  # noqa: BLE001
        return _err(f"Executor indisponível: {e}", status=502)
    return {"ok": True, "data": data}


@router.post("/admin/relogin/complete", dependencies=[plugin_permission("configure")])
async def relogin_complete(body: dict, request: Request):
    uid, _name = _actor(request)
    body = body or {}
    try:
        data = await ai_client.relogin_complete(
            uid, str(body.get("sessionId") or body.get("session_id") or ""),
            str(body.get("code") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(f"Executor indisponível: {e}", status=502)
    return {"ok": True, "data": data}


@router.post("/admin/relogin/abort", dependencies=[plugin_permission("configure")])
async def relogin_abort(body: dict, request: Request):
    uid, _name = _actor(request)
    body = body or {}
    try:
        data = await ai_client.relogin_abort(
            uid, str(body.get("sessionId") or body.get("session_id") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(f"Executor indisponível: {e}", status=502)
    return {"ok": True, "data": data}


@router.post("/admin/test-connection", dependencies=[plugin_permission("configure")])
async def test_connection(request: Request):
    """Prova o secret contra /auth-check do executor (não o /health)."""
    uid, _name = _actor(request)
    if not ai_client.is_configured():
        return _err("Configure URL e secret (≥32 chars) primeiro.")
    try:
        data = await ai_client.auth_check(uid)
    except Exception as e:  # noqa: BLE001
        return _err(f"Executor inalcançável: {e}", status=502)
    return {"ok": True, "data": data}


# ── Config (modelo/prompt da análise + servidor agêntico) ────────────────────

_AI_CFG_KEYS = ("ai_server_url", "ai_model", "ai_timeout_ms", "generator_backend",
                "callback_url")


@router.get("/config", dependencies=[plugin_permission("view")])
async def get_config():
    return {"ok": True, "data": {
        "model": logic._setting("model"),
        "prompt": logic._setting("prompt"),
        "prompt_default": logic.generation.DEFAULT_IMPROVEMENT_PROMPT,
        # plano 51 — servidor agêntico. Secret NUNCA sai em claro.
        "generator_backend": logic._setting("generator_backend", "external") or "external",
        "ai_server_url": logic._setting("ai_server_url"),
        "ai_server_secret": "***" if ai_client.shared_secret() else "",
        "ai_model": logic._setting("ai_model"),
        "ai_timeout_ms": logic._setting("ai_timeout_ms", "30000"),
        "callback_url": logic._setting("callback_url"),
        "callback_url_effective": ai_client.callback_url(),
        # plano 58: filtro padrão compartilhado do painel (objeto ou null).
        "default_filter": logic.get_default_filter(),
    }}


@router.put("/default-filter", dependencies=[plugin_permission("approve")])
async def put_default_filter(body: dict):
    """Salva o filtro padrão compartilhado do painel (plano 58). Decisão do produto:
    qualquer usuário com ``approve`` (não só ``configure``) pode redefini-lo, pois é
    a ferramenta de trabalho dele. ``filter`` ausente/vazio limpa o padrão."""
    from db.repositories import config_repo
    body = body or {}
    flt = body.get("filter")
    if flt is not None and not isinstance(flt, dict):
        return _err("filter deve ser um objeto ou nulo.")

    def _save():
        val = json.dumps(flt, ensure_ascii=False) if flt else ""
        config_repo.set(f"plugin.{logic.PLUGIN_ID}.default_filter", val)

    await asyncio.to_thread(_save)
    return {"ok": True, "data": {"default_filter": flt or None}}


@router.put("/config", dependencies=[plugin_permission("configure")])
async def put_config(body: dict):
    from db.repositories import config_repo
    body = body or {}

    def _save():
        if "model" in body:
            config_repo.set(f"plugin.{logic.PLUGIN_ID}.model", str(body.get("model") or ""))
        if "prompt" in body:
            config_repo.set(f"plugin.{logic.PLUGIN_ID}.prompt", str(body.get("prompt") or ""))
        for key in _AI_CFG_KEYS:
            if key in body:
                config_repo.set(f"plugin.{logic.PLUGIN_ID}.{key}",
                                str(body.get(key) or "").strip())
        # Secret: vazio/"***" PRESERVA o atual (molde settings.service.ts do nexus).
        if "ai_server_secret" in body:
            secret = str(body.get("ai_server_secret") or "").strip()
            if secret and secret != "***":
                if len(secret) < ai_client.SECRET_MIN_LEN:
                    raise ValueError(
                        f"Secret muito curto (mínimo {ai_client.SECRET_MIN_LEN} chars).")
                config_repo.set(f"plugin.{logic.PLUGIN_ID}.ai_server_secret", secret)

    try:
        await asyncio.to_thread(_save)
    except ValueError as e:
        return _err(str(e))
    return {"ok": True, "data": {
        "model": logic._setting("model"), "prompt": logic._setting("prompt"),
        "generator_backend": logic._setting("generator_backend", "external") or "external",
        "ai_server_url": logic._setting("ai_server_url"),
        "ai_server_secret": "***" if ai_client.shared_secret() else "",
        "ai_model": logic._setting("ai_model"),
        "ai_timeout_ms": logic._setting("ai_timeout_ms", "30000"),
        "callback_url": logic._setting("callback_url"),
        "callback_url_effective": ai_client.callback_url(),
    }}
