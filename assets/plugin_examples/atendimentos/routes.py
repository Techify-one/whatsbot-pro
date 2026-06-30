"""REST endpoints do plugin Atendimentos (mountados em /api/plugins/atendimentos).

Casca fina sobre ``logic.py``. Gating por ``plugin_permission``; snapshots de
atendente vêm do ``current_user`` do request. Formato ``{"ok", "data"|"error"}``.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request

from plugins.context import plugin_permission
from server.authz import acheck, current_user

from . import logic

router = APIRouter()


def _atendente(request: Request) -> tuple[int | None, str]:
    u = current_user(request) or {}
    name = str(u.get("name") or u.get("email") or "")
    return u.get("id"), name


def _err(msg: str, status: int = 400):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status, content={"ok": False, "error": msg})


async def _can_team(request: Request) -> bool:
    """Pode criar/editar visualizações de EQUIPE? (default-allow em legado/open via acheck)."""
    return await acheck(request, "plugin.atendimentos.manage_team_views")


async def _gate_view_write(request: Request, *, existing: dict | None,
                           target_scope: str | None) -> tuple[bool, str]:
    """Gate de escrita de visualização. Envolver EQUIPE (a existente OU o alvo) exige
    manage_team_views; uma PESSOAL exige ser o dono (owner_user_id == uid) — ou ter a
    permissão de equipe. Default-allow em legado/open vem do acheck/uid None."""
    uid, _ = _atendente(request)
    involves_team = (existing and existing.get("scope") == "team") or (target_scope == "team")
    if involves_team:
        if await _can_team(request):
            return True, ""
        return False, "Requer permissão para gerenciar visualizações de equipe."
    if (existing is not None and existing.get("owner_user_id") not in (None, uid)
            and not await _can_team(request)):
        return False, "Você só pode editar suas próprias visualizações."
    return True, ""


# ── Atendimentos ──────────────────────────────────────────────────────────────

@router.get("/atendimentos", dependencies=[plugin_permission("view")])
async def list_atendimentos(status: str | None = None, assignee_user_id: int | None = None,
                            contact_id: int | None = None, q: str | None = None,
                            opened_from: float | None = None, opened_to: float | None = None,
                            attr_filters: str | None = None,
                            limit: int = 200, offset: int = 0):
    # attr_filters = JSON {attribute_key: valor} (filtro por atributo de conversa da aba).
    af = None
    if attr_filters:
        try:
            parsed = json.loads(attr_filters)
            if isinstance(parsed, dict):
                af = {str(k): v for k, v in parsed.items()}
        except (ValueError, TypeError):
            af = None
    data = logic.list_atendimentos(
        status=status, assignee_user_id=assignee_user_id, contact_id=contact_id, q=q,
        opened_from=opened_from, opened_to=opened_to, attr_filters=af,
        limit=limit, offset=offset)
    return {"ok": True, "data": data}


@router.get("/atendimentos/{atid}", dependencies=[plugin_permission("view")])
async def get_atendimento(atid: int):
    at = logic.get_atendimento(atid)
    if not at:
        return _err("Atendimento não encontrado.", status=404)
    return {"ok": True, "data": {"atendimento": at, "conversas": logic.list_conversas(atid)}}


@router.get("/contacts/{contact_id}/atendimento", dependencies=[plugin_permission("view")])
async def get_contact_atendimento(contact_id: int):
    """Atendimento ABERTO do contato + suas conversas (alimenta o painel do chat)."""
    at = logic.get_open_atendimento_for_contact(contact_id)
    conversas = logic.list_conversas(at["id"]) if at else []
    return {"ok": True, "data": {"atendimento": at, "conversas": conversas}}


@router.post("/contacts/{contact_id}/atendimento/ensure", dependencies=[plugin_permission("edit")])
async def ensure_contact_atendimento(contact_id: int):
    """Garante (cria se preciso) o atendimento aberto do contato — ação explícita
    do painel quando ainda não há nenhum (evita criar só ao visualizar). Ao CRIAR um
    atendimento novo, grava a nota privada de abertura (announce_open)."""
    from db.repositories import contact_repo, conversation_repo
    contact = contact_repo.get(contact_id)
    if not contact:
        return _err("Contato não encontrado.", status=404)
    conv = (conversation_repo.get_open_for_contact(contact_id)
            or conversation_repo.get_latest_for_contact(contact_id))
    at = logic.ensure_atendimento_for_contact(
        contact_id, phone=contact.get("phone", ""), name=logic._contact_name(contact),
        conversation_id=(conv or {}).get("id"), announce_open=True)
    return {"ok": True, "data": {"atendimento": at, "conversas": logic.list_conversas(at["id"])}}


@router.put("/atendimentos/{atid}/fields", dependencies=[plugin_permission("edit")])
async def update_fields(atid: int, body: dict, request: Request):
    uid, name = _atendente(request)
    at, err = logic.update_atendimento_fields(
        atid, (body or {}).get("fields") or {}, assignee_user_id=uid, assignee_name=name)
    if err:
        return _err(err, status=400 if at is None else 400)
    return {"ok": True, "data": at}


@router.post("/atendimentos/{atid}/close", dependencies=[plugin_permission("edit")])
async def close_atendimento(atid: int, request: Request):
    uid, name = _atendente(request)
    at, err = logic.close_atendimento(atid, assignee_user_id=uid, assignee_name=name)
    if err:
        # 404 (não existe) | 400 (conversa aberta / campo obrigatório faltando).
        return _err(err, status=404 if at is None and "encontrado" in err else 400)
    # Ao finalizar: dispara as mensagens de protocolo/avaliação (em thread, best-effort).
    try:
        await asyncio.to_thread(logic.send_protocol_on_close, at)
    except Exception:  # noqa: BLE001 — envio nunca pode quebrar a resposta do fechar
        pass
    return {"ok": True, "data": at}


@router.post("/atendimentos/{atid}/reopen", dependencies=[plugin_permission("edit")])
async def reopen_atendimento(atid: int):
    at, err = logic.reopen_atendimento(atid)
    if err:
        return _err(err, status=404 if "encontrado" in err else 409)
    return {"ok": True, "data": at}


@router.post("/atendimentos/{atid}/assign", dependencies=[plugin_permission("edit")])
async def assign_atendimento(atid: int, body: dict):
    """Define/limpa o atendente do atendimento (drag-and-drop do kanban por atendente)."""
    auid = (body or {}).get("assignee_user_id")
    auid = int(auid) if auid not in (None, "") else None
    aname = str((body or {}).get("assignee_name") or "")
    at, err = logic.assign_atendimento(atid, auid, assignee_name=aname)
    if err:
        return _err(err, status=404)
    return {"ok": True, "data": at}


@router.post("/atendimentos/{atid}/set-attr", dependencies=[plugin_permission("edit")])
async def set_atendimento_attr(atid: int, body: dict):
    """Drag-and-drop do kanban agrupado por ATRIBUTO: grava o valor do atributo na última
    conversa do atendimento (custom_attributes do core). value vazio/None limpa a chave."""
    key = str((body or {}).get("key") or "")
    value = (body or {}).get("value")
    at, err = logic.set_conversa_attr(atid, key, value)
    if err:
        return _err(err, status=404 if "encontrado" in err else 400)
    return {"ok": True, "data": at}


# ── Visualizações personalizadas do Kanban (abas de "Agrupar por") ────────────

@router.get("/kanban-views", dependencies=[plugin_permission("view")])
async def list_kanban_views(request: Request):
    uid, _ = _atendente(request)
    return {"ok": True, "data": logic.list_kanban_views(user_id=uid)}


@router.post("/kanban-views", dependencies=[plugin_permission("view")])
async def create_kanban_view(body: dict, request: Request):
    uid, _ = _atendente(request)
    scope = str((body or {}).get("scope") or "personal")
    if scope == "team" and not await _can_team(request):
        return _err("Requer permissão para criar visualizações de equipe.", status=403)
    view, err = logic.create_kanban_view(
        name=(body or {}).get("name"), scope=scope,
        group_by=(body or {}).get("group_by") or "status",
        group_attr_key=(body or {}).get("group_attr_key"),
        group_date_mode=(body or {}).get("group_date_mode"),
        filters=(body or {}).get("filters") or {}, owner_user_id=uid)
    if err:
        return _err(err, status=400)
    return {"ok": True, "data": view}


@router.put("/kanban-views/{vid}", dependencies=[plugin_permission("view")])
async def update_kanban_view(vid: int, body: dict, request: Request):
    existing = logic.get_kanban_view(vid)
    if not existing:
        return _err("Visualização não encontrada.", status=404)
    target_scope = str((body or {}).get("scope") or existing.get("scope") or "personal")
    allowed, msg = await _gate_view_write(request, existing=existing, target_scope=target_scope)
    if not allowed:
        return _err(msg, status=403)
    view, err = logic.update_kanban_view(
        vid, name=(body or {}).get("name"), scope=target_scope,
        group_by=(body or {}).get("group_by"),
        group_attr_key=(body or {}).get("group_attr_key"),
        group_date_mode=(body or {}).get("group_date_mode"),
        filters=(body or {}).get("filters"))
    if err:
        return _err(err, status=404 if "não encontrada" in err else 400)
    return {"ok": True, "data": view}


@router.delete("/kanban-views/{vid}", dependencies=[plugin_permission("view")])
async def delete_kanban_view(vid: int, request: Request):
    existing = logic.get_kanban_view(vid)
    if not existing:
        return _err("Visualização não encontrada.", status=404)
    allowed, msg = await _gate_view_write(request, existing=existing, target_scope=None)
    if not allowed:
        return _err(msg, status=403)
    ok, err = logic.delete_kanban_view(vid)
    if err:
        return _err(err, status=400)
    return {"ok": True, "data": {"deleted": ok}}


# ── Vínculo / resolução de conversa ───────────────────────────────────────────

@router.post("/conversas/{conversation_id}/resolve", dependencies=[plugin_permission("edit")])
async def resolve_conversa(conversation_id: int, body: dict, request: Request):
    uid, name = _atendente(request)
    link, err = logic.resolve_conversa(
        conversation_id, (body or {}).get("fields") or {},
        assignee_name=name, assignee_user_id=uid)
    if err:
        return _err(err, status=404 if "não encontrada" in err else 400)
    return {"ok": True, "data": link}


@router.get("/conversas/{conversa_id}/anchor", dependencies=[plugin_permission("view")])
async def conversa_anchor(conversa_id: int):
    """Conversa (thread) + _id da 1ª mensagem do ciclo — alvo do scroll no chat
    (permalink /conversations/<id>?message=<_id>). conversa_id = id do CICLO."""
    return {"ok": True, "data": logic.cycle_anchor(conversa_id)}


# ── Definições de campos ──────────────────────────────────────────────────────

@router.get("/field-defs", dependencies=[plugin_permission("view")])
async def get_field_defs(scope: str = "conversa"):
    return {"ok": True, "data": {"scope": scope, "defs": logic.get_field_defs(scope)}}


@router.put("/field-defs", dependencies=[plugin_permission("edit")])
async def set_field_defs(body: dict):
    scope = (body or {}).get("scope")
    try:
        defs = logic.set_field_defs(scope, (body or {}).get("defs") or [])
    except ValueError as e:
        return _err(str(e), status=400)
    return {"ok": True, "data": {"scope": scope, "defs": defs}}


# ── Mensagens de protocolo/avaliação ao finalizar ─────────────────────────────

@router.get("/protocol-config", dependencies=[plugin_permission("view")])
async def get_protocol_config():
    return {"ok": True, "data": logic.get_protocol_config()}


@router.put("/protocol-config", dependencies=[plugin_permission("edit")])
async def set_protocol_config(body: dict):
    return {"ok": True, "data": logic.set_protocol_config(body or {})}
