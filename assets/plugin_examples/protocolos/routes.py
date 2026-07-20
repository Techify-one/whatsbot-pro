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


def _client_ip(request: Request) -> str:
    """IP do cliente p/ o bucket de rate-limit das rotas PÚBLICAS (avaliação).

    Atrás do proxy reverso documentado (Coolify/Traefik/nginx) a entrada MAIS À
    DIREITA do X-Forwarded-For é o endereço que o proxy confiável realmente viu — o
    chamador pode PREPENDAR entradas forjadas, mas não a que o proxy anexa. Usa o hop
    mais à direita (não o forjável mais à esquerda). Sem XFF → o peer TCP real."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else ""


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

# status/assignee_user_id/nota: valor ÚNICO OU lista JSON (multi-seleção do filtro). Ambos
# aceitos — '["aberto","fechado"]' vira lista; 'aberto' / '5' seguem como escalar (a logic
# normaliza).
def _maybe_list(v):
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


def _parse_attr_filters(attr_filters: str | None):
    """attr_filters = JSON {chave: valor} (filtro por campo/atributo da aba)."""
    if not attr_filters:
        return None
    try:
        parsed = json.loads(attr_filters)
        return {str(k): v for k, v in parsed.items()} if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def _filters_dict(status, assignee_user_id, contact_id, q, opened_from, opened_to,
                  attr_filters, nota=None) -> dict:
    """Filtros normalizados — MESMO conjunto para a lista e para o índice do Kanban.

    ``nota`` entra aqui (e não só na lista) porque o índice do Kanban precisa varrer
    exatamente o mesmo recorte: filtro de avaliação só na lista faria as contagens por
    coluna contarem protocolos que a lista esconde.
    """
    return {"status": _maybe_list(status),
            "assignee_user_id": _maybe_list(assignee_user_id),
            "contact_id": contact_id, "q": q,
            "opened_from": opened_from, "opened_to": opened_to,
            "nota": _maybe_list(nota),
            "attr_filters": _parse_attr_filters(attr_filters)}


@router.get("/protocolos", dependencies=[plugin_permission("view")])
async def list_protocolos(status: str | None = None, assignee_user_id: str | None = None,
                            contact_id: int | None = None, q: str | None = None,
                            opened_from: float | None = None, opened_to: float | None = None,
                            attr_filters: str | None = None, nota: str | None = None,
                            limit: int = 200, offset: int = 0):
    f = _filters_dict(status, assignee_user_id, contact_id, q, opened_from, opened_to,
                      attr_filters, nota)
    data = await asyncio.to_thread(
        lambda: logic.list_protocolos(**f, limit=limit, offset=offset))
    return {"ok": True, "data": data}


@router.get("/canais", dependencies=[plugin_permission("view")])
async def list_canais():
    """Canais disponíveis (não-arquivados) p/ o dropdown do filtro 'Canal' do Kanban."""
    return {"ok": True, "data": logic.list_channels()}


# ── Kanban agrupado (colunas + página POR COLUNA) ────────────────────────────
# O agrupamento roda no SERVIDOR (grouping.py) sobre um índice em cache
# (kanban_index.py): o cliente pede as colunas e depois pagina UMA coluna por vez,
# em vez de baixar a coleção inteira para agrupar no navegador.

def _view_dict(group_by, group_attr_key, group_field_scope, group_date_mode,
               group_date_grain, group_date_from, group_date_to) -> dict:
    """Config de agrupamento da visualização ativa (mesmas chaves da tabela de views)."""
    return {"group_by": group_by or "status", "group_attr_key": group_attr_key,
            "group_field_scope": group_field_scope, "group_date_mode": group_date_mode,
            "group_date_grain": group_date_grain, "group_date_from": group_date_from,
            "group_date_to": group_date_to}


@router.get("/grouped/columns", dependencies=[plugin_permission("view")])
async def grouped_columns(status: str | None = None, assignee_user_id: str | None = None,
                          contact_id: int | None = None, q: str | None = None,
                          opened_from: float | None = None, opened_to: float | None = None,
                          attr_filters: str | None = None, nota: str | None = None,
                          group_by: str | None = None, group_attr_key: str | None = None,
                          group_field_scope: str | None = None,
                          group_date_mode: str | None = None,
                          group_date_grain: str | None = None,
                          group_date_from: str | None = None,
                          group_date_to: str | None = None):
    """Colunas da visualização + contagem EXATA por coluna (índice em cache)."""
    view = _view_dict(group_by, group_attr_key, group_field_scope, group_date_mode,
                      group_date_grain, group_date_from, group_date_to)
    f = _filters_dict(status, assignee_user_id, contact_id, q, opened_from, opened_to,
                      attr_filters, nota)
    data = await asyncio.to_thread(logic.grouped_columns, view, f)
    return {"ok": True, "data": data}


@router.get("/grouped/column", dependencies=[plugin_permission("view")])
async def grouped_column(col_id: str = "",
                         status: str | None = None, assignee_user_id: str | None = None,
                         contact_id: int | None = None, q: str | None = None,
                         opened_from: float | None = None, opened_to: float | None = None,
                         attr_filters: str | None = None, nota: str | None = None,
                         group_by: str | None = None, group_attr_key: str | None = None,
                         group_field_scope: str | None = None,
                         group_date_mode: str | None = None,
                         group_date_grain: str | None = None,
                         group_date_from: str | None = None,
                         group_date_to: str | None = None,
                         limit: int = 50, offset: int = 0):
    """Uma página de UMA coluna — envelope ``{items,total,has_more}``.

    ``col_id`` vai como QUERY param (não no path): ids de coluna carregam o valor cru da
    opção em ``o:<valor>``, que pode conter ``/`` e acentos.
    """
    view = _view_dict(group_by, group_attr_key, group_field_scope, group_date_mode,
                      group_date_grain, group_date_from, group_date_to)
    f = _filters_dict(status, assignee_user_id, contact_id, q, opened_from, opened_to,
                      attr_filters, nota)
    data = await asyncio.to_thread(
        lambda: logic.grouped_column_page(view, f, col_id, limit=limit, offset=offset))
    return {"ok": True, "data": data}


@router.get("/protocolos/{atid}", dependencies=[plugin_permission("view")])
async def get_protocolo(atid: int):
    at = logic.with_avaliacao(logic.get_protocolo(atid))
    if not at:
        return _err("Protocolo não encontrado.", status=404)
    return {"ok": True, "data": {"protocolo": at, "atendimentos": logic.list_atendimentos(atid)}}


@router.get("/contacts/{contact_id}/protocolo", dependencies=[plugin_permission("view")])
async def get_contact_protocolo(contact_id: int):
    """Protocolo ABERTO do contato + suas atendimentos (alimenta o painel do chat)."""
    at = logic.get_open_protocolo_for_contact(contact_id)
    atendimentos = logic.list_atendimentos(at["id"]) if at else []
    return {"ok": True, "data": {"protocolo": at, "atendimentos": atendimentos}}


@router.get("/contacts/{contact_id}/relink-suggestion", dependencies=[plugin_permission("view")])
async def relink_suggestion(contact_id: int):
    """Plano 49: há protocolo fechado dentro da janela p/ sugerir o vínculo? (contrato §4)."""
    return {"ok": True, "data": logic.relink_suggestion_for_contact(contact_id)}


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
    # Corpo opcional: `reactivate_ai` (bool) sobrepõe a setting — é o seam do futuro
    # botão "Finalizar atendimento" (fechar + religar IA / só fechar). Tolerante a
    # corpo vazio ou não-JSON (o fluxo atual do painel fecha sem corpo).
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    at, err = logic.close_protocolo(atid, assignee_user_id=uid, assignee_name=name)
    if err:
        # 404 (não existe) | 400 (atendimento aberta / campo obrigatório faltando).
        return _err(err, status=404 if at is None and "encontrado" in err else 400)
    # Ao finalizar: dispara as mensagens de protocolo/avaliação (em thread, best-effort).
    try:
        await asyncio.to_thread(logic.send_protocol_on_close, at)
    except Exception:  # noqa: BLE001 — envio nunca pode quebrar a resposta do fechar
        pass
    # Religar a IA na conversa: override explícito do corpo OU a setting (default ON).
    override = (body or {}).get("reactivate_ai")
    do_reactivate = bool(override) if override is not None \
        else logic.get_reactivate_ai_on_close_setting()
    if do_reactivate:
        try:
            await logic.reactivate_ai_after_close(at, actor_name=name)
        except Exception:  # noqa: BLE001 — religar nunca pode quebrar a resposta do fechar
            pass
    return {"ok": True, "data": at}


@router.post("/protocolos/{atid}/reopen", dependencies=[plugin_permission("resolve")])
async def reopen_protocolo(atid: int):
    at, err = logic.reopen_protocolo(atid)
    if err:
        return _err(err, status=404 if "encontrado" in err else 409)
    return {"ok": True, "data": at}


@router.post("/protocolos/{previous_id}/relink", dependencies=[plugin_permission("resolve")])
async def relink_protocolo(previous_id: int, request: Request):
    """Plano 49: vincula o atendimento atual ao protocolo ANTERIOR (reabre o anterior,
    absorve os ciclos do novo e descarta o novo). Corpo opcional ``{current_open_id}``."""
    _, name = _atendente(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — corpo vazio/não-JSON é tolerado
        body = {}
    if not isinstance(body, dict):
        body = {}
    cur = body.get("current_open_id")
    cur = int(cur) if cur not in (None, "") else None
    at, err = logic.relink_to_previous(previous_id, current_open_id=cur, actor=(name or None))
    if err:
        return _err(err, status=404 if "encontrado" in err else 409)
    return {"ok": True, "data": {"protocolo": at}}


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


@router.post("/protocolos/{atid}/set-field", dependencies=[plugin_permission("edit")])
async def set_protocolo_field(atid: int, body: dict):
    """Drag-and-drop do kanban agrupado por CAMPO DE PROTOCOLO (de opção): grava o valor do
    campo do escopo (protocolo | atendimento) no dono certo. value vazio/None limpa a chave."""
    scope = str((body or {}).get("scope") or "")
    key = str((body or {}).get("key") or "")
    value = (body or {}).get("value")
    at, err = logic.set_protocolo_field(atid, scope, key, value)
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
        group_field_scope=(body or {}).get("group_field_scope"),
        group_date_mode=(body or {}).get("group_date_mode"),
        group_date_from=(body or {}).get("group_date_from"),
        group_date_to=(body or {}).get("group_date_to"),
        group_date_grain=(body or {}).get("group_date_grain"),
        filters=(body or {}).get("filters") or {},
        available_filters=(body or {}).get("available_filters"),
        favorite_filters=(body or {}).get("favorite_filters"),
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
    for k in ("available_filters", "favorite_filters", "column_order", "visibility_roles",
              "visibility_users_include", "visibility_users_exclude"):
        if isinstance(body, dict) and k in body:
            extra[k] = body[k]
    view, err = logic.update_kanban_view(
        vid, name=(body or {}).get("name"), scope=target_scope,
        group_by=(body or {}).get("group_by"),
        group_attr_key=(body or {}).get("group_attr_key"),
        group_field_scope=(body or {}).get("group_field_scope"),
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


# ── Ignorar abertura por regex (direção configurável) ─────────────────────────

@router.get("/skip-open-config", dependencies=[plugin_permission("view")])
async def get_skip_open_config():
    return {"ok": True, "data": logic.get_skip_open_config()}


@router.put("/skip-open-config", dependencies=[plugin_permission("config")])
async def set_skip_open_config(body: dict):
    return {"ok": True, "data": logic.set_skip_open_config(body or {})}


# ── PÚBLICO: avaliação de protocolo (round-trip da página externa) ────────────
# Rotas sob /public/ → ISENTAS de auth (o core exime /api/plugins/<id>/public/).
# Autenticam-se pelo próprio id_protocol + rate-limit por IP + uso único. A página
# externa (Cloudflare Worker) chama server-side. Ver plano 50.

@router.get("/public/avaliacao/{id_protocol}")
async def get_avaliacao_public(id_protocol: str, request: Request):
    """Consulta pública: nome do atendente + se já foi avaliado, pelo id_protocol."""
    if logic.rate_limited(_client_ip(request)):
        return _err("Muitas requisições. Tente novamente em instantes.", status=429)
    data = await asyncio.to_thread(logic.get_avaliacao_public, id_protocol)
    if data is None:
        return _err("Avaliação não encontrada.", status=404)
    return {"ok": True, "data": data}


@router.post("/public/avaliacao/{id_protocol}")
async def post_avaliacao_public(id_protocol: str, request: Request):
    """Gravação pública da nota (1..5) + sugestão. Corpo JSON ``{nota, sugestao}``."""
    if logic.rate_limited(_client_ip(request)):
        return _err("Muitas requisições. Tente novamente em instantes.", status=429)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    # Rota PÚBLICA: um corpo JSON truthy NÃO-dict (ex.: `5`, `"x"`, `[1]`) passaria
    # `body or {}` e estouraria em `.get` → 500. Normaliza para dict.
    body = body if isinstance(body, dict) else {}
    data, err = await asyncio.to_thread(
        logic.record_avaliacao, id_protocol, body.get("nota"),
        body.get("sugestao") or "", _client_ip(request))
    if err:
        # 404 não encontrada | 409 já respondida | 400 nota inválida.
        status = 404 if "não encontrada" in err else (409 if "já foi" in err else 400)
        return _err(err, status=status)
    return {"ok": True, "data": data}
