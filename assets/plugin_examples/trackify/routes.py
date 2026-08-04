"""REST do plugin (montado em ``/api/plugins/trackify``).

Shell fino sobre ``journey``/``identity``. Envelope ``{"ok", "data"|"error"}`` e
gate por ``plugin_permission`` (default-allow em instalação legada/sem RBAC).

⚠️ O contrato é ``?contact_id=<int do WhatsBot>``, **nunca** ``?phone=``:

* telefone em query string vaza para log de proxy — a mesma higiene que o plano
  exige do DSN;
* ``?phone=`` seria spoofável — qualquer atendente com ``view`` consultaria
  quanto QUALQUER número gastou, que é exatamente o vazamento financeiro que o
  RBAC tenta gatear.

Leitura NÃO é auditada (regra do repo: GET/listagem fica fora da trilha).
Tudo que toca o Nexus roda em ``asyncio.to_thread`` — ``run_read`` é bloqueante.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.repositories import contact_repo
from plugins.context import audit, plugin_permission

from . import (_config, dispatcher, field_map, identity, journey, push,
               session, sync_state, trackify_db)

logger = logging.getLogger("plugins.trackify.routes")

router = APIRouter()

# Sentinela do segredo: o que a tela recebe e pode devolver sem apagar a chave.
_MASK = "***"


def _err(msg: str, status: int = 400, data: dict | None = None):
    """``data`` carrega o detalhe estruturado (ex.: erros por linha do editor de
    mapeamentos). Um "erro ao salvar" genérico numa lista de vinte linhas obriga
    o operador a caçar qual delas ofendeu."""
    body: dict = {"ok": False, "error": msg}
    if data is not None:
        body["data"] = data
    return JSONResponse(body, status_code=status)


def _contact_scope(contact_id: int) -> dict | None:
    """Telefone/e-mail/tipo do contato do WhatsBot — a única fonte de identidade.

    Resolver no servidor (em vez de aceitar o telefone do cliente) é o que torna
    a rota não-spoofável.
    """
    c = contact_repo.get(contact_id)
    if not c:
        return None
    return {
        "phone": c.get("phone") or "",
        "email": (c.get("email") or "").strip(),
        "name": c.get("name") or "",
        "contact_type": c.get("contact_type") or "whatsapp",
        "is_group": bool(c.get("is_group")),
        # Campos conectados que são identificador no CDP — entram na busca ao
        # lado do telefone.
        "hints": field_map.identifier_hints(c),
    }


@router.get("/journey", dependencies=[plugin_permission("view")])
async def get_journey(contact_id: int):
    """Os 3 blocos da jornada do contato da conversa aberta."""
    scope = await asyncio.to_thread(_contact_scope, contact_id)
    if scope is None:
        return _err("Contato não encontrado.", 404)
    if scope["is_group"]:
        # Grupo não é pessoa: não tem jornada comercial nenhuma.
        return {"ok": True, "data": {"found": False, "configured": True,
                                     "is_group": True, "candidates": []}}

    data = await asyncio.to_thread(
        journey.journey_for,
        phone=scope["phone"], email=scope["email"] or None,
        extras=scope["hints"], contact_type=scope["contact_type"])
    data["whatsbot"] = {"contact_id": contact_id, "name": scope["name"]}
    return {"ok": True, "data": data}


@router.get("/journey/events", dependencies=[plugin_permission("view")])
async def get_journey_events(trackify_contact_id: str, offset: int = 0,
                             event_type: str | None = None,
                             limit: int | None = None):
    """Página seguinte da linha do tempo de um contato JÁ resolvido.

    Recebe o uuid do Trackify (devolvido por ``/journey``), não o telefone — a
    resolução já foi feita e gateada na chamada anterior.
    """
    if not trackify_db.is_configured():
        return _err("Conexão com o Trackify não configurada.", 503)
    data = await asyncio.to_thread(
        journey.fetch_timeline, trackify_contact_id,
        limit=limit, offset=offset, event_type=event_type)
    return {"ok": True, "data": data}


@router.get("/journey/search", dependencies=[plugin_permission("view")])
async def search_journey(email: str | None = None, cpf: str | None = None):
    """Busca manual quando o telefone não casou (~77% dos casos).

    Mesmo gate da jornada: buscar CPF arbitrário no CDP é mais sensível do que
    abrir a jornada do contato que já está na tela, então não afrouxa aqui.
    """
    if not (email or cpf):
        return _err("Informe e-mail ou CPF.")
    if not trackify_db.is_configured():
        return _err("Conexão com o Trackify não configurada.", 503)

    def _run():
        matches = identity.resolve_by_email(email) if email else []
        if not matches and cpf:
            matches = identity.resolve_by_cpf(cpf)
        if not matches:
            return {"found": False, "candidates": []}
        if len(matches) > 1:
            return {"found": False, "ambiguous": True, "candidates": [
                {**(journey.fetch_identity(m.contact_id) or {"contact_id": m.contact_id}),
                 "matched_by": m.slug} for m in matches]}
        data = journey.build_journey(matches[0].contact_id)
        data["matched_by"] = matches[0].slug
        return data

    return {"ok": True, "data": await asyncio.to_thread(_run)}


@router.get("/journey/by-id", dependencies=[plugin_permission("view")])
async def get_journey_by_id(trackify_contact_id: str):
    """Jornada de um cadastro escolhido no seletor de ambiguidade."""
    if not trackify_db.is_configured():
        return _err("Conexão com o Trackify não configurada.", 503)
    data = await asyncio.to_thread(journey.build_journey, trackify_contact_id)
    return {"ok": True, "data": data}


@router.get("/outbox", dependencies=[plugin_permission("manage")])
async def outbox(status: str | None = None, limit: int = 50):
    """Fila de saída: contadores + as linhas que precisam de atenção."""
    def _run():
        from sqlalchemy import text as _t
        from plugins.context import make_plugin_db
        st = dispatcher.stats()
        where = "WHERE status = :st" if status else "WHERE status IN ('failed','blocked')"
        with make_plugin_db() as conn:
            rows = conn.execute(
                _t("SELECT id, external_id, kind, phone, status, attempts, "
                   "       last_error, last_http_status, occurred_at, updated_at "
                   f"FROM plugin_trackify_outbox {where} "
                   "ORDER BY updated_at DESC LIMIT :lim"),
                {"st": status, "lim": max(1, min(limit, 200))}).mappings().all()
        return {"stats": st, "rows": [dict(r) for r in rows]}
    return {"ok": True, "data": await asyncio.to_thread(_run)}


@router.post("/outbox/{row_id}/requeue", dependencies=[plugin_permission("manage")])
async def requeue(row_id: int):
    """Devolve uma linha à fila — o caminho de volta depois de consertar o canal
    ou o mapeamento no Trackify (que é o que produz `blocked`)."""
    def _run():
        import time as _time
        from sqlalchemy import text as _t
        from plugins.context import make_plugin_db
        with make_plugin_db() as conn:
            res = conn.execute(
                _t("UPDATE plugin_trackify_outbox SET status = 'pending', attempts = 0, "
                   " next_attempt_at = :now, last_error = '', updated_at = :now "
                   "WHERE id = :id AND status IN ('failed','blocked','dropped')"),
                {"id": row_id, "now": _time.time()})
        return int(res.rowcount or 0)
    n = await asyncio.to_thread(_run)
    if not n:
        return _err("Linha não encontrada ou não reprocessável.", 404)
    audit("trackify", "outbox.requeue", resource_id=str(row_id))
    return {"ok": True, "data": {"requeued": n}}


@router.get("/ingestion-key", dependencies=[plugin_permission("manage")])
async def get_key():
    """Devolve a chave MASCARADA. O form declarativo de settings devolveria o
    valor em claro (``GET /api/plugins/{id}/settings`` não mascara nada), por
    isso a chave mora fora dele."""
    val = await asyncio.to_thread(lambda: (_config.setting("api_key") or "").strip())
    return {"ok": True, "data": {"set": bool(val), "masked": _MASK if val else ""}}


@router.put("/ingestion-key", dependencies=[plugin_permission("manage")])
async def put_key(body: dict):
    """Grava a chave. O sentinela ``***`` preserva o valor atual — assim a tela
    pode enviar o formulário inteiro sem apagar o segredo por descuido."""
    val = str((body or {}).get("api_key") or "")
    if val == _MASK:
        return {"ok": True, "data": {"unchanged": True}}

    def _run():
        from db.repositories import config_repo
        config_repo.set(_config.PREFIX + "api_key", val.strip())
    await asyncio.to_thread(_run)
    audit("trackify", "config.update", resource_id="api_key")
    return {"ok": True, "data": {"set": bool(val.strip())}}


# ── Sincronização de campos do contato ───────────────────────────────────

@router.get("/contact-attributes", dependencies=[plugin_permission("manage")])
async def contact_attributes():
    """Vocabulário do lado ESQUERDO do mapeamento."""
    data = await asyncio.to_thread(field_map.wb_vocabulary)
    return {"ok": True, "data": data}


@router.get("/trackify-fields", dependencies=[plugin_permission("manage")])
async def trackify_fields(refresh: int = 0):
    """Vocabulário do lado DIREITO. Nunca 500: distingue não-configurado de
    inalcançável de "o CDP não tem campo nenhum"."""
    data = await asyncio.to_thread(field_map.tk_fields, bool(refresh))
    return {"ok": True, "data": data}


@router.get("/mappings", dependencies=[plugin_permission("manage")])
async def get_mappings():
    rows = await asyncio.to_thread(field_map.list_maps)
    return {"ok": True, "data": {
        "rows": rows,
        "credential_set": await asyncio.to_thread(_config.credential_set),
        "max_rows": field_map.MAX_ROWS,
    }}


@router.put("/mappings", dependencies=[plugin_permission("manage")])
async def put_mappings(body: dict):
    """Troca o conjunto inteiro. Revalida TUDO no servidor: a tela é
    conveniência, o gate é aqui (um chamador programático não passa por ela)."""
    rows = (body or {}).get("rows")

    def _run():
        before = field_map.list_maps()
        clean, errors = field_map.validate(
            rows, credential_set=_config.credential_set())
        if errors:
            return before, None, errors
        field_map.replace_all(clean)
        return before, field_map.list_maps(), {}

    before, after, errors = await asyncio.to_thread(_run)
    if errors:
        return _err("Corrija os mapeamentos destacados.", 400,
                    {"row_errors": errors})
    # Só nomes de campo entram na trilha — nunca valor de contato.
    audit("trackify", "fieldmap.update",
          before=[{k: r[k] for k in ("wb_key", "tk_slug", "direction")} for r in before],
          after=[{k: r[k] for k in ("wb_key", "tk_slug", "direction")} for r in after])
    return {"ok": True, "data": {"rows": after}}


@router.get("/service-account", dependencies=[plugin_permission("manage")])
async def get_service_account():
    def _run():
        return {
            "set": _config.credential_set(),
            "email": (_config.setting("service_email") or "").strip(),
            "password_masked": _MASK if (_config.setting("service_password") or "") else "",
            "api_base": _config.api_base(),
            "user_id": (_config.setting("sync_user_id") or ""),
            "blocked_reason": (_config.setting("sync_blocked_reason") or ""),
            "last_login_error": (_config.setting("sync_last_login_error") or ""),
        }
    return {"ok": True, "data": await asyncio.to_thread(_run)}


@router.put("/service-account", dependencies=[plugin_permission("manage")])
async def put_service_account(body: dict):
    """Grava e-mail e senha. O sentinela ``***`` preserva a senha atual, para a
    tela poder reenviar o formulário inteiro sem apagar o segredo por descuido."""
    b = body or {}
    email = str(b.get("email") or "").strip()
    password = str(b.get("password") or "")

    def _run():
        from db.repositories import config_repo
        updates = {_config.PREFIX + "service_email": email}
        if password != _MASK:
            updates[_config.PREFIX + "service_password"] = password.strip()
            # Credencial nova invalida a identidade capturada e o bloqueio.
            updates[_config.PREFIX + "sync_user_id"] = ""
            updates[_config.PREFIX + "sync_blocked_reason"] = ""
        config_repo.set_many(updates)
        return _config.credential_set()

    ok = await asyncio.to_thread(_run)
    # NUNCA o valor: só o fato de existir.
    audit("trackify", "config.update", resource_id="service_account",
          after={"email": email, "senha_definida": password != ""})
    return {"ok": True, "data": {"set": ok, "unchanged_password": password == _MASK}}


@router.post("/service-account/test", dependencies=[plugin_permission("manage")])
async def test_service_account(body: dict | None = None):
    """Prova a credencial ANTES de qualquer escrita.

    Aceita e-mail/senha no corpo para testar o que ainda não foi salvo — é a
    diferença entre o operador conferir e o operador salvar torto e descobrir
    pela fila de erro. Só faz login: nunca grava num contato real.

    Não é auditado (teste de conexão fica fora da trilha, por regra do repo).
    """
    import httpx

    b = body or {}
    async with httpx.AsyncClient() as client:
        data = await session.probe(client,
                                   email=str(b.get("email") or ""),
                                   password=str(b.get("password") or ""))
    return {"ok": True, "data": data}


@router.get("/field-sync/status", dependencies=[plugin_permission("manage")])
async def field_sync_status():
    def _run():
        cur = sync_state.get_cursor("changelog")
        return {
            "enabled": bool(_config.setting("field_sync_enabled", False)),
            "dry_run": bool(_config.setting("field_sync_dry_run", True)),
            "pull_enabled": bool(_config.setting("field_sync_pull_enabled", False)),
            "credential_set": _config.credential_set(),
            "blocked_reason": (_config.setting("sync_blocked_reason") or ""),
            "last_login_error": (_config.setting("sync_last_login_error") or ""),
            "logged_in": bool((_config.setting("sync_user_id") or "").strip()),
            "cursor_ts": cur.get("cursor_ts") or 0.0,
            "cursor_note": cur.get("note") or "",
            "state": sync_state.counters(),
            "mappings": field_map.list_maps(),
            "conflicts": sync_state.conflicts(),
        }
    return {"ok": True, "data": await asyncio.to_thread(_run)}


@router.post("/field-sync/run", dependencies=[plugin_permission("manage")])
async def field_sync_run(body: dict | None = None):
    """Simula (ou dispara) a sincronização de UM contato.

    Existe para o operador conferir num contato antes de soltar em cima de
    milhares — é a diferença entre ousar ligar isto e não. Sem ``contact_id``
    seria uma varredura da base inteira, que não pode rodar dentro da requisição
    (estouraria o tempo e prenderia um worker), então isso é recusado aqui: quem
    faz varredura é a task de conferência, no ritmo dela.
    """
    b = body or {}
    contact_id = b.get("contact_id")
    if not contact_id:
        return _err("Informe o contato a simular.", 400)

    def _run():
        plano = push.plan_for_contact(int(contact_id))
        return {
            "contact_id": int(contact_id),
            "linked": bool(plano.trackify_contact_id),
            "trackify_contact_id": plano.trackify_contact_id,
            "skip": plano.skip,
            "would_write": plano.field_values,
            "decisions": [
                {"wb_key": d["map"]["wb_key"], "tk_slug": d["map"]["tk_slug"],
                 "direction": d["map"]["direction"], "action": d["decision"].action,
                 "reason": d["decision"].reason}
                for d in plano.decisions
            ],
        }

    data = await asyncio.to_thread(_run)
    if b.get("apply") and not data["skip"]:
        # Enfileira; quem entrega é o worker, que é onde mora o ritmo.
        await asyncio.to_thread(push.enqueue, int(contact_id), "manual")
        data["queued"] = True
        audit("trackify", "field_sync.run", resource_id=str(contact_id))
    return {"ok": True, "data": data}


@router.get("/health", dependencies=[plugin_permission("view")])
async def health():
    """Distingue "não configurado" de "inalcançável" de "schema mudou".

    Sem isto, um schema alterado no Trackify viraria uma tela vazia sem
    explicação — o erro tem que ser acionável, não um 500.
    """
    configured = await asyncio.to_thread(trackify_db.is_configured)
    reachable, message = (False, "DSN do Nexus não configurado.")
    schema_ok, missing = False, []
    if configured:
        reachable, message = await asyncio.to_thread(trackify_db.ping)
        if reachable:
            schema_ok, missing = await asyncio.to_thread(trackify_db.schema_check)
    return {"ok": True, "data": {
        "configured": configured,
        "reachable": reachable,
        "message": message,
        "schema_ok": schema_ok,
        "schema_missing": missing,
        "base_url_set": bool(_config.nexus_base_url()),
        "mirror_enabled": bool(_config.setting("mirror_enabled", False)),
        "field_sync_enabled": bool(_config.setting("field_sync_enabled", False)),
        "field_sync_credential_set": await asyncio.to_thread(_config.credential_set),
    }}
