"""Rotas ``_internal/*`` — as "tools" que a IA chama de volta (plano 51 · 02 F4).

Montadas sob ``/api/plugins/melhorias/public/_internal/...`` — a ÚNICA forma
auth-exempt sem tocar no core (``PLUGIN_PUBLIC_PATH_RE``, D02-b); a autenticação
REAL é a dependency ``hmac_guard`` (HMAC + nonce + On-Behalf-Of, D02-c).

Três famílias:
- **Write-through** (persistência do chat — sem RBAC, só grava o que o executor
  manda, molde nexus): ``/messages``, ``/approvals``, ``/conversation-status``.
- **READ** (config de IA + trace): reusa os repos direto; exige a permissão
  ``agent.config.manage`` do usuário on-behalf-of.
- **MUTATION**: cada handler reaplica RBAC via ``authz.acheck`` ANTES de
  escrever e delega ao repo VERSIONADO correspondente (``agent_repo.save`` /
  ``tool_repo.save`` / ``variable_repo.save`` / ``tool_override_repo`` /
  rollbacks) — nunca reimplementa versionamento.

⚠️ Código de ``ai_tools`` NÃO agenda restart automático aqui (um restart mataria
a própria conversa agêntica): o save responde ``restart_required: true`` e a
tool só instala no próximo reinício manual + kill-switch ``ai_tools_code_enabled``.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from db.repositories import (agent_repo, message_repo, tool_override_repo,
                             tool_repo, variable_repo)
from server import authz

from . import chat_logic
from .hmac_guard import hmac_guard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/_internal", dependencies=[Depends(hmac_guard)])


def _ok(data) -> dict:
    return {"ok": True, "data": data}


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": msg})


async def _assert_permission(request: Request, key: str) -> JSONResponse | None:
    """RBAC on-behalf-of (D02-c): o hmac_guard já carimbou ``request.state.user``;
    aqui reaplicamos a decisão central do core (RBAC + seam ABAC)."""
    if not await authz.acheck(request, key):
        return _err(f"Sem permissão: {key}", status=403)
    return None


# ── Write-through (persistência do chat) ─────────────────────────────────────

@router.post("/messages")
async def append_message(body: dict):
    body = body or {}
    cid = str(body.get("conversation_id") or body.get("conversationId") or "")
    role = str(body.get("role") or "")
    if not cid or role not in ("user", "assistant", "system", "tool"):
        return _err("conversation_id/role inválidos.")
    mid = await asyncio.to_thread(
        chat_logic.append_chat_message, cid, role,
        content=body.get("content"),
        tool_name=body.get("tool_name") or body.get("toolName"),
        tool_input=body.get("tool_input") or body.get("toolInput"),
        tool_result=body.get("tool_result") or body.get("toolResult"),
        token_usage=body.get("token_usage") or body.get("tokenUsage"))
    return _ok({"id": mid})


@router.post("/approvals")
async def register_approval(body: dict):
    body = body or {}
    aid = str(body.get("approval_id") or body.get("approvalId") or "")
    cid = str(body.get("conversation_id") or body.get("conversationId") or "")
    tool = str(body.get("tool_name") or body.get("toolName") or "")
    if not aid or not cid or not tool:
        return _err("approval_id/conversation_id/tool_name obrigatórios.")
    row = await asyncio.to_thread(
        chat_logic.register_approval, aid, cid, tool_name=tool,
        tool_input=body.get("tool_input") or body.get("toolInput"),
        summary=str(body.get("summary") or ""))
    conv = await asyncio.to_thread(chat_logic.get_conversation, cid)
    chat_logic._ws_emit((conv or {}).get("suggestion_id"), cid,
                        "approval_registered", row or {})
    return _ok(row)


@router.post("/conversation-status")
async def conversation_status(body: dict):
    body = body or {}
    cid = str(body.get("conversation_id") or body.get("conversationId") or "")
    status = str(body.get("status") or "").upper()
    if not cid or status not in chat_logic.CONV_STATUSES:
        return _err("conversation_id/status inválidos.")
    conv = await asyncio.to_thread(chat_logic.on_conversation_status, cid, status)
    if not conv:
        return _err("Conversa não encontrada.", status=404)
    return _ok(conv)


# ── READ (config de IA + trace) ──────────────────────────────────────────────

@router.get("/agents")
async def list_agents(request: Request):
    denied = await _assert_permission(request, "agent.config.manage")
    if denied:
        return denied
    return _ok(await asyncio.to_thread(agent_repo.list_all))


@router.get("/agents/{agent_key}")
async def get_agent(agent_key: str, request: Request):
    denied = await _assert_permission(request, "agent.config.manage")
    if denied:
        return denied
    row = await asyncio.to_thread(agent_repo.get, agent_key)
    if not row:
        return _err("Agente não encontrado.", status=404)
    return _ok(row)


@router.get("/tools")
async def list_tools(request: Request):
    denied = await _assert_permission(request, "agent.config.manage")
    if denied:
        return denied
    from plugins.context import get_deps
    deps = get_deps() or getattr(request.app.state, "deps", None)
    handler = getattr(deps, "agent_handler", None) if deps else None
    registered = handler.list_tools() if handler else []
    ai_tools = await asyncio.to_thread(tool_repo.list_all)
    # ai_tools.code pode ser grande — a listagem devolve só metadados (o código
    # inteiro vem no GET /tools/{name}).
    slim = [{k: v for k, v in t.items() if k != "code"} for t in ai_tools]
    return _ok({"registered": registered, "ai_tools": slim})


@router.get("/tools/{name}")
async def get_tool(name: str, request: Request):
    denied = await _assert_permission(request, "agent.config.manage")
    if denied:
        return denied
    row = await asyncio.to_thread(tool_repo.get, name)
    override = await asyncio.to_thread(tool_override_repo.get, name)
    if not row and not override:
        return _err("Tool não encontrada.", status=404)
    return _ok({"tool": row, "override": override})


@router.get("/variables")
async def list_variables(request: Request):
    denied = await _assert_permission(request, "agent.config.manage")
    if denied:
        return denied
    return _ok(await asyncio.to_thread(variable_repo.list_all))


@router.get("/message-trace")
async def message_trace(request: Request, msg_db_id: int | None = None,
                        msg_id: str | None = None, phone: str | None = None):
    """Trace completo de UMA resposta da IA (o "porquê ela respondeu X").
    Prefere o id interno (``msg_db_id`` = messages.id); aceita o msg_id externo."""
    denied = await _assert_permission(request, "agent.config.manage")
    if denied:
        return denied
    from app.services import execution_trace

    def _load_row():
        if msg_db_id is not None:
            return message_repo.get_by_db_id(int(msg_db_id))
        if msg_id:
            return message_repo.get_by_msg_id(msg_id)
        return None

    row = await asyncio.to_thread(_load_row)
    if not row:
        return _err("Mensagem não encontrada.", status=404)
    bundle = await asyncio.to_thread(
        execution_trace.reconstruct_for_message, row, phone=phone)
    return _ok(bundle)


@router.get("/active-agent")
async def active_agent(request: Request, conversation_id: int | None = None):
    denied = await _assert_permission(request, "agent.config.manage")
    if denied:
        return denied

    def _resolve():
        from db.repositories import conversation_repo
        key = None
        if conversation_id:
            conv = conversation_repo.get(int(conversation_id))
            key = (conv or {}).get("active_agent_key")
        if not key:
            default = agent_repo.get_default()
            key = (default or {}).get("agent_key")
        return {"agent_key": key}

    return _ok(await asyncio.to_thread(_resolve))


# ── MUTATION (repos versionados; RBAC reaplicado por handler) ────────────────

@router.post("/agents/{agent_key}")
async def save_agent(agent_key: str, body: dict, request: Request):
    """Cria/edita um agente (merge sobre a row existente — campos ausentes
    preservados). Novo exige ``agent.create``; existente ``agent.config.manage``;
    mudar o campo ``prompt`` exige também ``agent.prompts.edit``."""
    body = body or {}
    existing = await asyncio.to_thread(agent_repo.get, agent_key)
    denied = await _assert_permission(
        request, "agent.create" if existing is None else "agent.config.manage")
    if denied:
        return denied
    prompt_given = "prompt" in body
    if prompt_given:
        denied = await _assert_permission(request, "agent.prompts.edit")
        if denied:
            return denied

    base = existing or {}
    model_config = body.get("model_config", base.get("model_config") or {})
    tool_names = body.get("tool_names", base.get("tool_names"))
    routing_targets = body.get("routing_targets", base.get("routing_targets"))
    hooks_config = body.get("hooks_config", base.get("hooks_config") or {})
    if not isinstance(model_config, dict) or not isinstance(hooks_config, dict):
        return _err("model_config/hooks_config devem ser objetos.")
    if tool_names is not None and not isinstance(tool_names, list):
        return _err("tool_names deve ser lista ou null.")
    if routing_targets is not None and not isinstance(routing_targets, list):
        return _err("routing_targets deve ser lista ou null.")
    if agent_key == agent_repo.DEFAULT_AGENT_KEY and not bool(
            body.get("enabled", base.get("enabled", True))):
        return _err("O agente padrão não pode ser desativado.")

    row = await asyncio.to_thread(
        agent_repo.save, agent_key,
        display_name=body.get("display_name", base.get("display_name") or agent_key),
        prompt=body.get("prompt", base.get("prompt") or ""),
        prompt_key=base.get("prompt_key") or "",
        model_config=model_config,
        tool_names=tool_names,
        enabled=bool(body.get("enabled", base.get("enabled", True))),
        description=body.get("description", base.get("description") or ""),
        is_router=bool(body.get("is_router", base.get("is_router", False))),
        is_default=bool(base.get("is_default", False)),
        routing_targets=routing_targets,
        hooks_config=hooks_config,
        change_note=body.get("change_note") or "melhoria agêntica",
    )
    _invalidate("agent", agent_key)
    return _ok(row)


@router.post("/agents/{agent_key}/prompt")
async def patch_agent_prompt(agent_key: str, body: dict, request: Request):
    """Patch só do prompt inline (semântica do ``PUT /api/ai/agents/{key}/prompt``)."""
    denied = await _assert_permission(request, "agent.prompts.edit")
    if denied:
        return denied
    existing = await asyncio.to_thread(agent_repo.get, agent_key)
    if not existing:
        return _err("Agente não encontrado.", status=404)
    row = await asyncio.to_thread(
        agent_repo.save, agent_key,
        display_name=existing.get("display_name") or "",
        prompt=(body or {}).get("prompt", ""),
        prompt_key=existing.get("prompt_key") or "",
        model_config=existing.get("model_config") or {},
        tool_names=existing.get("tool_names"),
        enabled=bool(existing.get("enabled", True)),
        description=existing.get("description") or "",
        is_router=bool(existing.get("is_router", False)),
        is_default=bool(existing.get("is_default", False)),
        routing_targets=existing.get("routing_targets"),
        hooks_config=existing.get("hooks_config") or {},
        change_note=(body or {}).get("change_note") or "melhoria agêntica",
    )
    _invalidate("agent", agent_key)
    return _ok(row)


@router.post("/agents/{agent_key}/rollback/{version}")
async def rollback_agent(agent_key: str, version: int, request: Request):
    denied = await _assert_permission(request, "agent.prompts.version")
    if denied:
        return denied
    row = await asyncio.to_thread(agent_repo.rollback, agent_key, version)
    if not row:
        return _err("Versão não encontrada.", status=404)
    _invalidate("agent", agent_key)
    return _ok(row)


@router.post("/tools/{name}")
async def save_tool(name: str, body: dict, request: Request):
    """Cria/edita uma tool de código (``ai_tools`` kind=code). Born-disabled
    (P63) e SEM restart automático — responde ``restart_required``."""
    denied = await _assert_permission(request, "agent.tools.manage")
    if denied:
        return denied
    body = body or {}
    existing = await asyncio.to_thread(tool_repo.get, name)
    if existing and existing.get("kind") == "builtin":
        return _err("Tool builtin não é editável por aqui.", status=400)
    dependencies = body.get("dependencies", (existing or {}).get("dependencies") or [])
    if not isinstance(dependencies, list):
        return _err("dependencies deve ser uma lista.")
    row = await asyncio.to_thread(
        tool_repo.save, name,
        description=body.get("description", (existing or {}).get("description") or ""),
        code=body.get("code", (existing or {}).get("code") or ""),
        dependencies=dependencies,
        # nasce/permanece DESLIGADA — um humano liga pela UI após o restart.
        enabled=bool((existing or {}).get("enabled", False)) if existing else False,
    )
    _invalidate("tool", name)
    return _ok({"tool": row, "restart_required": True,
                "note": ("Código salvo e versionado. A instalação só ocorre no "
                         "próximo reinício do servidor, com o kill-switch "
                         "ai_tools_code_enabled ligado; a tool nasce desabilitada.")})


@router.post("/tools/{name}/rollback/{version}")
async def rollback_tool(name: str, version: int, request: Request):
    denied = await _assert_permission(request, "agent.tools.manage")
    if denied:
        return denied
    row = await asyncio.to_thread(tool_repo.rollback, name, version)
    if not row:
        return _err("Versão não encontrada.", status=404)
    _invalidate("tool", name)
    return _ok({"tool": row, "restart_required": True})


@router.post("/tool-overrides/{name}")
async def set_tool_override(name: str, body: dict, request: Request):
    """Override de tool registrada (enabled/description/display_label) — vale na
    hora (sem restart), mas NÃO é versionado (gap conhecido)."""
    denied = await _assert_permission(request, "agent.tools.manage")
    if denied:
        return denied
    body = body or {}
    kwargs = {}
    if "enabled" in body:
        kwargs["enabled"] = bool(body["enabled"])
    if "description" in body:
        kwargs["description"] = body["description"]
    if "display_label" in body:
        kwargs["display_label"] = body["display_label"]
    if not kwargs:
        return _err("Nada para atualizar.")
    row = await asyncio.to_thread(tool_override_repo.upsert_override, name, **kwargs)
    if row is None:
        return _err("Tool não encontrada.", status=404)
    _invalidate("tool_override", name)
    return _ok(row)


@router.post("/variables/{name}")
async def save_variable(name: str, body: dict, request: Request):
    denied = await _assert_permission(request, "agent.variables.manage")
    if denied:
        return denied
    import re
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", name or ""):
        return _err("Nome inválido: letras/números/_ começando por letra (máx 64).")
    row = await asyncio.to_thread(variable_repo.save, name,
                                  str((body or {}).get("value", "")))
    _invalidate("variable", name)
    return _ok(row)


@router.post("/variables/{name}/rollback/{version}")
async def rollback_variable(name: str, version: int, request: Request):
    denied = await _assert_permission(request, "agent.variables.manage")
    if denied:
        return denied
    row = await asyncio.to_thread(variable_repo.rollback, name, version)
    if row is None:
        return _err("Versão não encontrada.", status=404)
    _invalidate("variable", name)
    return _ok(row)


def _invalidate(kind: str, key: str) -> None:
    """Mudança de config de IA vale na próxima mensagem (cache TTL 60s) — o
    mesmo pós-mutação das rotas /api/ai/* do core."""
    try:
        from ai_engine import dynamic_registry
        dynamic_registry.invalidate()
    except Exception:  # noqa: BLE001
        pass
    try:
        from plugins.events import emit
        emit("ai.config.changed", {"kind": kind, "key": key,
                                   "source": "melhorias_agentic"})
    except Exception:  # noqa: BLE001
        pass
