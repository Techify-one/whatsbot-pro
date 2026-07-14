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

from . import logic

router = APIRouter()


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
    data, err = await asyncio.to_thread(
        logic.create_suggestion,
        phone=str(body.get("phone") or ""),
        target_message=target,
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


# ── Config (modelo/prompt da análise — namespace do plugin) ──────────────────

@router.get("/config", dependencies=[plugin_permission("view")])
async def get_config():
    return {"ok": True, "data": {
        "model": logic._setting("model"),
        "prompt": logic._setting("prompt"),
        "prompt_default": logic.generation.DEFAULT_IMPROVEMENT_PROMPT,
    }}


@router.put("/config", dependencies=[plugin_permission("configure")])
async def put_config(body: dict):
    from db.repositories import config_repo
    body = body or {}

    def _save():
        if "model" in body:
            config_repo.set(f"plugin.{logic.PLUGIN_ID}.model", str(body.get("model") or ""))
        if "prompt" in body:
            config_repo.set(f"plugin.{logic.PLUGIN_ID}.prompt", str(body.get("prompt") or ""))
    await asyncio.to_thread(_save)
    return {"ok": True, "data": {
        "model": logic._setting("model"), "prompt": logic._setting("prompt")}}
