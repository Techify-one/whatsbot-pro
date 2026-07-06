"""REST endpoints do plugin Protocolos (mountados em /api/plugins/protocolos).

Casca fina sobre ``logic.py``. Gating por ``plugin_permission``; snapshots de
atendente vêm do ``current_user`` do request. Formato ``{"ok", "data"|"error"}``.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request

from plugins.context import plugin_permission
from server.authz import acheck, current_user
from db.repositories import rbac_repo

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
    return await acheck(request, "plugin.protocolos.manage_team_views")


async def _can_create_views(request: Request) -> bool:
    """Pode CRIAR novas visualizações (agrupamentos)? Tem create_views OU manage_team_views
    (quem gerencia views de equipe também pode criar). Default-allow em legado/open via acheck."""
    return (await acheck(request, "plugin.protocolos.create_views")
            or await _can_team(request))


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


# ── Protocolos ──────────────────────────────────────────────────────────────

@router.get("/protocolos", dependencies=[plugin_permission("view")])
async def list_protocolos(status: str | None = None, assignee_user_id: int | None = None,
                            contact_id: int | None = None, q: str | None = None,
                            opened_from: float | None = None, opened_to: float | None = None,
                            attr_filters: str | None = None,
                            limit: int = 200, offset: int = 0):
    # attr_filters = JSON {attribute_key: valor} (filtro por atributo de atendimento da aba).
    af = None
    if attr_filters:
        try:
            parsed = json.loads(attr_filters)
            if isinstance(parsed, dict):
                af = {str(k): v for k, v in parsed.items()}
        except (ValueError, TypeError):
            af = None
    data = logic.list_protocolos(
        status=status, assignee_user_id=assignee_user_id, contact_id=contact_id, q=q,
        opened_from=opened_from, opened_to=opened_to, attr_filters=af,
        limit=limit, offset=offset)
    return {"ok": True, "data": data}


@router.get("/protocolos/{atid}", dependencies=[plugin_permission("view")])
async def get_protocolo(atid: int):
    at = logic.get_protocolo(atid)
    if not at:
        return _err("Protocolo não encontrado.", status=404)
    return {"ok": True, "data": {"protocolo": at, "atendimentos": logic.list_atendimentos(atid)}}


@router.get("/contacts/{contact_id}/protocolo", dependencies=[plugin_permission("view")])
async def get_contact_protocolo(contact_id: int):
    """Protocolo ABERTO do contato + suas atendimentos (alimenta o painel do chat)."""
    at = logic.get_open_protocolo_for_contact(contact_id)
    atendimentos = logic.list_atendimentos(at["id"]) if at else []
    return {"ok": True, "data": {"protocolo": at, "atendimentos": atendimentos}}


@router.post("/contacts/{contact_id}/protocolo/ensure", dependencies=[plugin_permission("edit")])
async def ensure_contact_protocolo(contact_id: int):
    """Garante (cria se preciso) o protocolo aberto do contato — ação explícita
    do painel quando ainda não há nenhum (evita criar só ao visualizar). Ao CRIAR um
    protocolo novo, grava a nota privada de abertura (announce_open)."""
    from db.repositories import contact_repo, conversation_repo
    contact = contact_repo.get(contact_id)
    if not contact:
        return _err("Contato não encontrado.", status=404)
    atend = (conversation_repo.get_open_for_contact(contact_id)
            or conversation_repo.get_latest_for_contact(contact_id))
    at = logic.ensure_protocolo_for_contact(
        contact_id, phone=contact.get("phone", ""), name=logic._contact_name(contact),
        conversation_id=(atend or {}).get("id"), announce_open=True)
    return {"ok": True, "data": {"protocolo": at, "atendimentos": logic.list_atendimentos(at["id"])}}


@router.put("/protocolos/{atid}/fields", dependencies=[plugin_permission("edit")])
async def update_fields(atid: int, body: dict, request: Request):
    uid, name = _atendente(request)
    body = body or {}
    propagate = body.get("propagate_assignee_to_conversations")
    if propagate is None:
        propagate = body.get("propagate_assignee", True)
    at, err = logic.update_protocolo_fields(
        atid, body.get("fields") or {}, assignee_user_id=uid, assignee_name=name,
        propagate_assignee=bool(propagate))
    if err:
        return _err(err, status=400 if at is None else 400)
    return {"ok": True, "data": at}


@router.post("/protocolos/{atid}/close", dependencies=[plugin_permission("resolve")])
async def close_protocolo(atid: int, request: Request):
    uid, name = _atendente(request)
    at, err = logic.close_protocolo(atid, assignee_user_id=uid, assignee_name=name)
    if err:
        # 404 (não existe) | 400 (atendimento aberta / campo obrigatório faltando).
        return _err(err, status=404 if at is None and "encontrado" in err else 400)
    # Ao finalizar: dispara as mensagens de protocolo/avaliação (em thread, best-effort).
    try:
        await asyncio.to_thread(logic.send_protocol_on_close, at)
    except Exception:  # noqa: BLE001 — envio nunca pode quebrar a resposta do fechar
        pass
    return {"ok": True, "data": at}


@router.post("/protocolos/{atid}/reopen", dependencies=[plugin_permission("resolve")])
async def reopen_protocolo(atid: int):
    at, err = logic.reopen_protocolo(atid)
    if err:
        return _err(err, status=404 if "encontrado" in err else 409)
    return {"ok": True, "data": at}


@router.post("/protocolos/{atid}/assign", dependencies=[plugin_permission("assign")])
async def assign_protocolo(atid: int, body: dict):
    """Define/limpa o atendente do protocolo (drag-and-drop do kanban por atendente)."""
    auid = (body or {}).get("assignee_user_id")
    auid = int(auid) if auid not in (None, "") else None
    aname = str((body or {}).get("assignee_name") or "")
    at, err = logic.assign_protocolo(atid, auid, assignee_name=aname)
    if err:
        return _err(err, status=404)
    return {"ok": True, "data": at}


@router.post("/protocolos/{atid}/set-attr", dependencies=[plugin_permission("edit")])
async def set_protocolo_attr(atid: int, body: dict):
    """Drag-and-drop do kanban agrupado por ATRIBUTO: grava o valor do atributo na última
    atendimento do protocolo (custom_attributes do core). value vazio/None limpa a chave."""
    key = str((body or {}).get("key") or "")
    value = (body or {}).get("value")
    at, err = logic.set_atendimento_attr(atid, key, value)
    if err:
        return _err(err, status=404 if "encontrado" in err else 400)
    return {"ok": True, "data": at}


# ── Visualizações personalizadas do Kanban (abas de "Agrupar por") ────────────

@router.get("/roles", dependencies=[plugin_permission("view")])
async def list_roles():
    """Grupos de permissão (roles) p/ o seletor "Quem pode ver" do editor de visualização.
    Endpoint próprio do plugin (gated só por `view`) — o /api/roles do core exige users.manage."""
    roles = await asyncio.to_thread(rbac_repo.list_roles)
    return {"ok": True, "data": {"roles": [{"key": r["key"], "name": r["name"]} for r in roles]}}


def _visibility_scope(body: dict, fallback: str) -> str:
    """scope efetivo p/ o gate: há grupo OU usuário incluído → 'team' (compartilhar exige
    manage_team_views). Senão o scope pedido/atual."""
    if (body or {}).get("visibility_roles") or (body or {}).get("visibility_users_include"):
        return "team"
    return fallback


@router.get("/kanban-views", dependencies=[plugin_permission("view")])
async def list_kanban_views(request: Request):
    uid, _ = _atendente(request)
    return {"ok": True, "data": logic.list_kanban_views(user_id=uid)}


@router.post("/kanban-views", dependencies=[plugin_permission("view")])
async def create_kanban_view(body: dict, request: Request):
    uid, _ = _atendente(request)
    if not await _can_create_views(request):
        return _err("Requer permissão para criar visualizações.", status=403)
    scope = _visibility_scope(body, str((body or {}).get("scope") or "personal"))
    if scope == "team" and not await _can_team(request):
        return _err("Requer permissão para criar visualizações de equipe.", status=403)
    view, err = logic.create_kanban_view(
        name=(body or {}).get("name"), scope=scope,
        group_by=(body or {}).get("group_by") or "status",
        group_attr_key=(body or {}).get("group_attr_key"),
        group_date_mode=(body or {}).get("group_date_mode"),
        group_date_from=(body or {}).get("group_date_from"),
        group_date_to=(body or {}).get("group_date_to"),
        group_date_grain=(body or {}).get("group_date_grain"),
        filters=(body or {}).get("filters") or {},
        available_filters=(body or {}).get("available_filters"),
        column_order=(body or {}).get("column_order"),
        visibility_roles=(body or {}).get("visibility_roles"),
        visibility_users_include=(body or {}).get("visibility_users_include"),
        visibility_users_exclude=(body or {}).get("visibility_users_exclude"),
        owner_user_id=uid)
    if err:
        return _err(err, status=400)
    return {"ok": True, "data": view}


@router.put("/kanban-views/{vid}", dependencies=[plugin_permission("view")])
async def update_kanban_view(vid: int, body: dict, request: Request):
    existing = logic.get_kanban_view(vid)
    if not existing:
        return _err("Visualização não encontrada.", status=404)
    fallback = str((body or {}).get("scope") or existing.get("scope") or "personal")
    target_scope = _visibility_scope(body, fallback)
    allowed, msg = await _gate_view_write(request, existing=existing, target_scope=target_scope)
    if not allowed:
        return _err(msg, status=403)
    # Repassa só o que veio no body (ausente = mantém o atual, via sentinela no logic).
    extra = {}
    for k in ("available_filters", "column_order", "visibility_roles",
              "visibility_users_include", "visibility_users_exclude"):
        if isinstance(body, dict) and k in body:
            extra[k] = body[k]
    view, err = logic.update_kanban_view(
        vid, name=(body or {}).get("name"), scope=target_scope,
        group_by=(body or {}).get("group_by"),
        group_attr_key=(body or {}).get("group_attr_key"),
        group_date_mode=(body or {}).get("group_date_mode"),
        group_date_from=(body or {}).get("group_date_from"),
        group_date_to=(body or {}).get("group_date_to"),
        group_date_grain=(body or {}).get("group_date_grain"),
        filters=(body or {}).get("filters"), **extra)
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


# ── Preferência pessoal x equipe dos filtros (POR-USUÁRIO, por visualização) ───
# A preferência é do PRÓPRIO usuário (qual conjunto de filtros aplicar ao entrar na aba),
# então exige só `view` — NÃO manage_team_views (essa só gate os filtros DA EQUIPE).

@router.get("/kanban-views/{vid}/my-pref", dependencies=[plugin_permission("view")])
async def get_my_view_pref(vid: int, request: Request):
    if not logic.get_kanban_view(vid):
        return _err("Visualização não encontrada.", status=404)
    uid, _ = _atendente(request)
    return {"ok": True, "data": logic.get_user_view_pref(vid, uid)}


@router.put("/kanban-views/{vid}/my-pref", dependencies=[plugin_permission("view")])
async def set_my_view_pref(vid: int, body: dict, request: Request):
    """Preferência do PRÓPRIO usuário para esta aba: usar os filtros da EQUIPE (default) ou
    os PESSOAIS dele. ``use_personal`` e ``personal_filters`` são opcionais/independentes."""
    if not logic.get_kanban_view(vid):
        return _err("Visualização não encontrada.", status=404)
    uid, _ = _atendente(request)
    if uid is None:
        # Legado/open (sem identidade): nada a persistir → devolve o default de equipe.
        return {"ok": True, "data": {"use_personal": False, "personal_filters": {}}}
    body = body or {}
    up = body.get("use_personal")
    pf = body.get("personal_filters")
    pco = body.get("personal_column_order")
    pref = logic.upsert_user_view_pref(
        vid, uid,
        use_personal=(None if up is None else bool(up)),
        personal_filters=(pf if isinstance(pf, dict) else None),
        personal_column_order=(pco if isinstance(pco, list) else None))
    return {"ok": True, "data": pref}


# ── Vínculo / resolução de atendimento ───────────────────────────────────────────

@router.post("/atendimentos/{conversation_id}/resolve", dependencies=[plugin_permission("resolve")])
async def resolve_atendimento(conversation_id: int, body: dict, request: Request):
    uid, name = _atendente(request)
    link, err = logic.resolve_atendimento(
        conversation_id, (body or {}).get("fields") or {},
        assignee_name=name, assignee_user_id=uid)
    if err:
        return _err(err, status=404 if "não encontrada" in err else 400)
    return {"ok": True, "data": link}


@router.get("/atendimentos/{atendimento_id}/anchor", dependencies=[plugin_permission("view")])
async def atendimento_anchor(atendimento_id: int):
    """Atendimento (thread) + _id da 1ª mensagem do ciclo — alvo do scroll no chat
    (permalink /conversations/<id>?message=<_id>). atendimento_id = id do CICLO."""
    return {"ok": True, "data": logic.cycle_anchor(atendimento_id)}


# ── Definições de campos ──────────────────────────────────────────────────────

@router.get("/field-defs", dependencies=[plugin_permission("view")])
async def get_field_defs(scope: str = "atendimento"):
    return {"ok": True, "data": {"scope": scope, "defs": logic.get_field_defs(scope)}}


@router.put("/field-defs", dependencies=[plugin_permission("config")])
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


@router.put("/protocol-config", dependencies=[plugin_permission("config")])
async def set_protocol_config(body: dict):
    return {"ok": True, "data": logic.set_protocol_config(body or {})}


@router.get("/general-config", dependencies=[plugin_permission("view")])
async def get_general_config():
    return {"ok": True, "data": logic.get_general_config()}


@router.put("/general-config", dependencies=[plugin_permission("config")])
async def set_general_config(body: dict):
    return {"ok": True, "data": logic.set_general_config(body or {})}
